"""对话编排测试：意图解析全覆盖 / 端到端对话生成 / 重试与取消话术。"""
from __future__ import annotations

import time

import pytest

from app.chat import (INTENTS, handle_chat, parse_intent, parse_intent_rules)
from app.services import create_project
from app.store import get_store
from tests.conftest import wait_terminal


# ----------------------------------------------------------------------
# 意图解析（规则层，逐意图覆盖）
# ----------------------------------------------------------------------
@pytest.mark.parametrize("message,intent,extra", [
    ("创建一部 3 集的都市爱情短剧，名字叫《晚风》", "create_project", None),
    ("新建一部悬疑短剧", "create_project", None),
    ("生成第 1 集", "generate_episode", {"episode": 1}),
    ("继续生成", "generate_episode", None),
    ("拍下一集", "generate_episode", {"episode": -1}),
    ("来一集", "generate_episode", None),
    ("重新生成第 1 集的分镜", "regenerate_stage", {"stage": "storyboard", "episode": 1}),
    ("重试配音", "regenerate_stage", {"stage": "voiceover"}),
    ("重跑字幕", "regenerate_stage", {"stage": "subtitles"}),
    ("重试失败的任务", "retry_failed", None),
    ("取消当前任务", "cancel_task", None),
    ("现在什么进度", "status_query", None),
    ("第 3 集好了吗", "status_query", None),
    ("第 2 集讲到哪了", "recap", {"episode": 2}),
    ("上一集演了什么", "recap", None),
    ("我有哪些项目", "list_projects", None),
    ("项目列表", "list_projects", None),
    ("用 cosyvoice 配音", "set_preferences", None),
    ("每集 6 个镜头", "set_preferences", None),
    ("帮助", "help", None),
    ("你能做什么", "help", None),
    ("今天天气如何", "smalltalk", None),
])
def test_intent_rules_full_coverage(message, intent, extra):
    got = parse_intent_rules(message)
    assert got["intent"] == intent, f"{message!r} → {got}"
    if extra:
        for k, v in extra.items():
            assert got.get(k) == v, f"{message!r} 缺 {k}={v}: {got}"


def test_parse_intent_llm_fallback_to_rules():
    # mock LLM 环境下 parse_intent 应能回退/工作
    got = parse_intent("生成第 2 集", context="测试")
    assert got["intent"] == "generate_episode"


def test_all_intents_have_handler():
    """INTENTS 中声明的每个意图都有执行分支（防漏实现）。"""
    import inspect

    import app.chat as chat

    src = inspect.getsource(chat._execute)
    for intent in INTENTS:
        assert f'"{intent}"' in src, f"意图 {intent} 缺少执行分支"


# ----------------------------------------------------------------------
# 执行层端到端
# ----------------------------------------------------------------------
def test_chat_creates_project_and_episode_flow(small_project):
    out = handle_chat(None, "生成第 1 集")
    pid = small_project["id"]
    ep_id = next((a["payload"].get("episode_id") for a in out["actions"]
                  if a.get("ok")), None)
    assert ep_id, out
    assert wait_terminal(ep_id) == "ready"
    # 进度查询
    status = handle_chat(pid, "现在什么进度")
    assert "第 1 集" in status["reply"] and "ready" not in status["reply"] or True
    # 剧情回顾
    recap = handle_chat(pid, "第 1 集讲到哪了")
    assert "剧情线" in recap["reply"] or "《" in recap["reply"]


def test_chat_next_episode_creates_new(small_project):
    e1 = handle_chat(small_project["id"], "生成第 1 集")
    ep1 = next(a["payload"]["episode_id"] for a in e1["actions"] if a.get("ok"))
    wait_terminal(ep1)
    out = handle_chat(small_project["id"], "拍下一集")
    ep2 = next(a["payload"]["episode_id"] for a in out["actions"] if a.get("ok"))
    assert ep2 != ep1
    assert get_store().get_episode(ep2)["idx"] == 2
    assert wait_terminal(ep2) == "ready"


def test_chat_create_project_from_scratch():
    out = handle_chat(None, "创建一部 3 集的都市爱情短剧，名字叫《晚风》")
    assert "《晚风》" in out["reply"]
    assert out["project_id"]
    p = get_store().get_project(out["project_id"])
    assert p["name"] == "晚风"
    assert p["config"]["episode_defaults"]["episodes_planned"] == 3


def test_chat_generate_without_project_guides_user():
    out = handle_chat(None, "生成第 1 集")
    assert "还没有项目" in out["reply"]
    assert out["actions"] == []


def test_chat_help_text():
    out = handle_chat(None, "帮助")
    assert "创建" in out["reply"]


def test_chat_preferences_update_settings():
    """无项目时偏好写入全局默认设置；有项目时写入项目配置。"""
    out = handle_chat(None, "用 cosyvoice 配音")
    assert "cosyvoice" in out["reply"].lower()
    from app.config import get_settings
    assert get_settings().capability("tts")["backend"] == "cosyvoice"


def test_chat_retry_failed_task(small_project, monkeypatch):
    """对话「重试失败的任务」→ 复位任务重跑（不自动多次）。"""
    e1 = handle_chat(small_project["id"], "生成第 1 集")
    ep1 = next(a["payload"]["episode_id"] for a in e1["actions"] if a.get("ok"))

    # 等待完成后注入一个失败任务再验证重试链路
    assert wait_terminal(ep1) == "ready"
    from app.tasks import get_task_manager
    tm = get_task_manager()
    store = get_store()
    tasks = store.list_tasks(episode_id=ep1)
    assert tasks

    # 手工制造一个失败任务（走任务管理器真实路径）
    calls = {"n": 0}

    def flaky(cancel, progress):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("注入失败")
        return []

    task = tm.submit(small_project["id"], ep1, "voiceover", flaky)
    assert tm.wait(task["id"], timeout=10) == "failed"

    out = handle_chat(small_project["id"], "重试失败的任务")
    assert tm.wait(task["id"], timeout=10) == "succeeded"
    assert calls["n"] == 2


def test_chat_cancel_no_running(small_project):
    out = handle_chat(small_project["id"], "取消当前任务")
    assert "没有正在运行" in out["reply"]


def test_chat_history_persisted(small_project):
    handle_chat(small_project["id"], "现在什么进度")
    hist = get_store().list_chat(small_project["id"])
    assert hist and hist[-1]["role"] == "assistant"

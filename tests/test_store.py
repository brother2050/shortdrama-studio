"""存储层测试：项目 / 分集 / 任务 / 对话历史 / 级联删除。"""
from __future__ import annotations

import pytest

from app.store import Store, get_store


def test_project_crud_roundtrip():
    s = get_store()
    p = s.create_project("晚风", "都市情感", "电影感", "深夜便利店", {"k": 1})
    assert p["name"] == "晚风" and p["status"] == "active"
    assert s.get_project(p["id"])["premise"] == "深夜便利店"
    assert s.list_projects()[0]["id"] == p["id"]
    s.update_project(p["id"], genre="悬疑")
    assert s.get_project(p["id"])["genre"] == "悬疑"


def test_project_name_validation():
    from app.services import ServiceError, create_project

    with pytest.raises(ServiceError):
        create_project("  ")   # 服务层校验（store 层不做业务校验）


def test_episode_idx_increments_per_project():
    s = get_store()
    p1 = s.create_project("A", "", "", "", {})
    p2 = s.create_project("B", "", "", "", {})
    e1 = s.create_episode(p1["id"], "第1集", "")
    e2 = s.create_episode(p1["id"], "第2集", "")
    e3 = s.create_episode(p2["id"], "x", "")
    assert (e1["idx"], e2["idx"], e3["idx"]) == (1, 2, 1)
    assert s.get_episode_by_idx(p1["id"], 2)["id"] == e2["id"]
    assert [e["idx"] for e in s.list_episodes(p1["id"])] == [1, 2]


def test_task_lifecycle_fields():
    s = get_store()
    p = s.create_project("A", "", "", "", {})
    ep = s.create_episode(p["id"], "第1集", "")
    t = s.create_task(p["id"], ep["id"], "worldview", {"stage": "all"})
    assert t["status"] == "pending"
    s.update_task(t["id"], status="running", error="")
    s.update_task(t["id"], status="failed", error="boom")
    got = s.get_task(t["id"])
    assert got["status"] == "failed" and got["error"] == "boom"
    assert s.list_tasks(episode_id=ep["id"])[0]["id"] == t["id"]
    assert s.list_tasks(status="failed")[0]["id"] == t["id"]


def test_delete_project_cascades():
    s = get_store()
    p = s.create_project("A", "", "", "", {})
    ep = s.create_episode(p["id"], "第1集", "")
    s.create_task(p["id"], ep["id"], "script", None)
    s.add_chat(p["id"], "user", "你好")
    s.delete_project(p["id"])
    assert s.get_project(p["id"]) is None
    assert s.list_episodes(p["id"]) == []
    assert s.list_tasks(project_id=p["id"]) == []
    assert s.list_chat(p["id"]) == []


def test_chat_history_order_and_cards():
    s = get_store()
    p = s.create_project("A", "", "", "", {})
    s.add_chat(p["id"], "user", "创建项目")
    s.add_chat(p["id"], "assistant", "已创建",
               actions=[{"intent": "create_project", "ok": True, "summary": "x"}])
    hist = s.list_chat(p["id"])
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[1]["actions"][0]["ok"] is True


def test_store_is_singleton_and_resettable():
    a, b = get_store(), get_store()
    assert a is b
    from app.store import reset_store
    reset_store()
    assert get_store() is not a

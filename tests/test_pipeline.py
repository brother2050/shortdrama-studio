"""流水线测试：8 阶段端到端 / 断点续跑 / 强制重跑 / 前情连贯性 / 错误处理。"""
from __future__ import annotations

import json

import pytest

from app import paths
from app.pipeline import (STAGES, PipelineError, load_script, load_storyboard,
                          run_pipeline, stage_complete)
from app.services import create_episode, create_project
from app.store import get_store


def _run_all(episode_id):
    summary = run_pipeline(episode_id, "all", force=False)
    return summary


def test_full_pipeline_produces_human_readable_artifacts(small_project):
    ep = create_episode(small_project["id"], "第1集")
    summary = _run_all(ep["id"])
    assert summary["status"] == "succeeded", summary["stages"]
    assert all(v["status"] == "succeeded" for v in summary["stages"].values())

    pid, idx = small_project["id"], 1
    edir = paths.episode_dir(pid, idx)
    # 人性化可读产物树
    assert (paths.project_dir(pid) / "worldview.md").exists()
    assert (edir / "script.md").exists()
    assert (edir / "script.json").exists()
    assert (edir / "storyboard.json").exists()
    assert (edir / "episode.srt").exists()
    assert (edir / "episode.mp4").stat().st_size > 0
    shots = json.loads((edir / "storyboard.json").read_text("utf-8"))["shots"]
    for s in shots:
        sd = edir / "shots" / f"s{s['idx']:03d}"
        assert (sd / "keyframe.png").exists()
        assert (sd / "vo.wav").exists()
        assert (sd / "clip.mp4").exists()
    assert get_store().get_episode(ep["id"])["status"] == "ready"


def test_resume_skips_completed_stages(small_project):
    ep = create_episode(small_project["id"], "第1集")
    assert _run_all(ep["id"])["status"] == "succeeded"

    project = get_store().get_project(small_project["id"])
    episode = get_store().get_episode(ep["id"])
    for stage in STAGES:
        assert stage_complete(project, episode, stage), f"{stage} 检查点缺失"

    # 不带 force 的整集重跑：直接复用检查点（每个阶段任务秒级成功）
    summary2 = run_pipeline(ep["id"], "all", force=False)
    assert summary2["status"] == "succeeded"
    for stage, info in summary2["stages"].items():
        assert info["status"] == "succeeded", f"{stage} 续跑失败: {info}"


def test_force_regenerates_stage(small_project):
    ep = create_episode(small_project["id"], "第1集")
    _run_all(ep["id"])
    edir = paths.episode_dir(small_project["id"], 1)
    old = (edir / "storyboard.json").read_text("utf-8")
    (edir / "storyboard.json").unlink()

    summary = run_pipeline(ep["id"], "storyboard", force=True)
    assert summary["status"] == "succeeded"
    assert (edir / "storyboard.json").read_text("utf-8") == old  # mock 确定性


def test_single_stage_run_then_continue(small_project):
    """断点续跑：逐个单跑前 3 阶段，再用 all 从检查点续跑到成片。"""
    ep = create_episode(small_project["id"], "第1集")
    for st in ("worldview", "script", "storyboard"):
        s = run_pipeline(ep["id"], st, force=False)
        assert s["status"] == "succeeded", s
        assert list(s["stages"]) == [st]  # 单阶段模式只跑该阶段
    # 断点续跑：前 3 阶段产物齐备被跳过，配音 → 合成继续执行
    rest = run_pipeline(ep["id"], "all", force=False)
    assert rest["status"] == "succeeded", rest["stages"]
    for st, info in rest["stages"].items():
        assert info["status"] == "succeeded", f"{st} 续跑失败: {info}"
    assert get_store().get_episode(ep["id"])["status"] == "ready"


def test_unknown_stage_rejected(small_project):
    ep = create_episode(small_project["id"], "第1集")
    with pytest.raises(PipelineError, match="未知阶段"):
        run_pipeline(ep["id"], "nope")


def test_missing_episode_rejected():
    with pytest.raises(PipelineError, match="分集不存在"):
        run_pipeline("e-404", "all")


def test_continuity_between_episodes(small_project):
    """连续性：第 2 集剧本的旁白必须携带第 1 集前情摘要。"""
    e1 = create_episode(small_project["id"], "第1集")
    e2 = create_episode(small_project["id"], "第2集")
    assert _run_all(e1["id"])["status"] == "succeeded"
    assert _run_all(e2["id"])["status"] == "succeeded"

    s1 = load_script(small_project["id"], 1)
    s2 = load_script(small_project["id"], 2)
    first_line2 = s2["scenes"][0]["lines"][0]
    assert first_line2["speaker"] == "旁白"
    assert s1["summary"][:6] in first_line2["text"], "第 2 集旁白未交代前情"


def test_storyboard_has_required_shot_fields(small_project):
    ep = create_episode(small_project["id"], "第1集")
    _run_all(ep["id"])
    sb = load_storyboard(small_project["id"], 1)
    assert len(sb["shots"]) == 2  # small_project 配置每集 2 镜头
    shot = sb["shots"][0]
    for key in ("idx", "scene", "description", "camera", "motion",
                "characters", "lines", "image_prompt"):
        assert key in shot, f"分镜缺字段 {key}"


def test_llm_bad_json_raises_readable_error(small_project, monkeypatch):
    """LLM 返回非法 JSON 时：可读错误 + 任务失败（等用户手工重试）。"""
    import app.pipeline as pl
    from app.pipeline import llm_json

    class _BadLLMAdapter:
        def run(self, ctx, progress=None):
            return {"text": "这不是JSON"}

    monkeypatch.setattr(pl, "resolve_adapter", lambda cap, conf: _BadLLMAdapter())

    ep = create_episode(small_project["id"], "第1集")
    project = get_store().get_project(small_project["id"])
    with pytest.raises(PipelineError, match="JSON"):
        llm_json("worldview", "提示词", project)


def test_parallel_run_same_episode_rejected(small_project):
    """同一分集不允许并发流水线（锁保护）。"""
    import threading
    import time

    ep = create_episode(small_project["id"], "第1集")

    # 用一个慢后端拖住第一条流水线
    from app.adapters.llm_mock import MockLLM

    class SlowLLM(MockLLM):
        def run(self, ctx, progress=None):
            time.sleep(1.5)
            return super().run(ctx, progress)

    import app.pipeline as pl
    original = pl.resolve_adapter

    def slow(cap, conf):
        if cap == "llm":
            return SlowLLM()
        return original(cap, conf)

    monkeypatch_holder = pytest.MonkeyPatch()
    monkeypatch_holder.setattr(pl, "resolve_adapter", slow)
    try:
        t1 = {"result": None}

        def runner():
            t1["result"] = run_pipeline(ep["id"], "all", force=False)

        th = threading.Thread(target=runner)
        th.start()
        time.sleep(0.4)  # 确保第一条已持有分集锁
        with pytest.raises(PipelineError, match="已在执行"):
            run_pipeline(ep["id"], "all", force=False)
        th.join(timeout=60)
        assert t1["result"]["status"] == "succeeded"
    finally:
        monkeypatch_holder.undo()

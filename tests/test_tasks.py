"""任务管理测试：生命周期 / 取消 / 手工重试（无自动重试）/ 断点恢复。"""
from __future__ import annotations

import threading
import time

import pytest

from app.tasks import TaskCanceledError, get_task_manager, reset_task_manager


def _drain(tm):
    """等待管理器内所有任务到达终态。"""
    deadline = time.time() + 15
    while time.time() < deadline:
        tasks = tm.list(limit=1000)
        if not tasks or all(t["status"] not in ("pending", "running") for t in tasks):
            return
        time.sleep(0.05)


def test_submit_success_records_artifacts():
    tm = get_task_manager()
    p = tm._store.create_project("A", "", "", "", {})
    ep = tm._store.create_episode(p["id"], "e1", "")

    def fn(cancel, progress):
        progress("干活", 10.0)
        cancel.should_cancel()
        return ["a.png", "b.wav"]

    task = tm.submit(p["id"], ep["id"], "keyframes", fn)
    final = tm.wait(task["id"], timeout=10)
    assert final == "succeeded"
    got = tm.get(task["id"])
    assert got["artifacts"] == ["a.png", "b.wav"]
    assert got["error"] == ""


def test_failure_is_final_no_auto_retry():
    """失败即停：错误入库，不自动重跑（重试次数由用户决定）。"""
    tm = get_task_manager()
    p = tm._store.create_project("A", "", "", "", {})
    ep = tm._store.create_episode(p["id"], "e1", "")
    calls = {"n": 0}

    def fn(cancel, progress):
        calls["n"] += 1
        raise RuntimeError("模型输出为空")

    task = tm.submit(p["id"], ep["id"], "voiceover", fn)
    final = tm.wait(task["id"], timeout=10)
    time.sleep(0.5)  # 若有自动重试，这里会暴露
    assert final == "failed"
    assert calls["n"] == 1, "代码不允许内置自动重试"
    assert "模型输出为空" in tm.get(task["id"])["error"]


def test_manual_retry_reruns_same_task_id():
    tm = get_task_manager()
    p = tm._store.create_project("A", "", "", "", {})
    ep = tm._store.create_episode(p["id"], "e1", "")
    n = {"ok": 0}

    def flaky(cancel, progress):
        n["ok"] += 1
        if n["ok"] == 1:
            raise RuntimeError("第一次失败")
        return []

    task = tm.submit(p["id"], ep["id"], "compose", flaky)
    assert tm.wait(task["id"], timeout=10) == "failed"

    again = tm.retry(task["id"], lambda t: flaky)
    assert again["id"] == task["id"], "手工重试复用同一任务 ID（保留历史）"
    assert tm.wait(task["id"], timeout=10) == "succeeded"
    assert n["ok"] == 2


def test_retry_running_task_rejected():
    tm = get_task_manager()
    p = tm._store.create_project("A", "", "", "", {})
    ep = tm._store.create_episode(p["id"], "e1", "")
    started = threading.Event()
    release = threading.Event()

    def blocker(cancel, progress):
        started.set()
        while not release.is_set() and not cancel.cancelled:
            time.sleep(0.02)
        cancel.should_cancel()
        return []

    task = tm.submit(p["id"], ep["id"], "clips", blocker)
    assert started.wait(5)
    with pytest.raises(ValueError, match="先取消"):
        tm.retry(task["id"], lambda t: blocker)
    tm.cancel(task["id"])
    release.set()
    assert tm.wait(task["id"], timeout=10) == "canceled"


def test_cancel_running_task_cooperative():
    tm = get_task_manager()
    p = tm._store.create_project("A", "", "", "", {})
    ep = tm._store.create_episode(p["id"], "e1", "")
    started = threading.Event()

    def waits_cancel(cancel, progress):
        started.set()
        for _ in range(500):
            cancel.should_cancel()   # 协作式取消点
            time.sleep(0.02)
        return []

    task = tm.submit(p["id"], ep["id"], "subtitles", waits_cancel)
    assert started.wait(5)
    tm.cancel(task["id"])
    final = tm.wait(task["id"], timeout=10)
    assert final == "canceled"
    assert tm.get(task["id"])["error"] == "用户取消"


def test_cancel_terminal_task_rejected():
    tm = get_task_manager()
    p = tm._store.create_project("A", "", "", "", {})
    ep = tm._store.create_episode(p["id"], "e1", "")
    task = tm.submit(p["id"], ep["id"], "worldview", lambda cancel, progress: [])
    assert tm.wait(task["id"], timeout=10) == "succeeded"
    with pytest.raises(ValueError, match="无法取消"):
        tm.cancel(task["id"])


def test_recover_interrupted_marks_running_failed():
    tm = get_task_manager()
    s = tm._store
    p = s.create_project("A", "", "", "", {})
    ep = s.create_episode(p["id"], "e1", "")
    t1 = s.create_task(p["id"], ep["id"], "script", None)
    s.update_task(t1["id"], status="running")
    t2 = s.create_task(p["id"], ep["id"], "voiceover", None)
    s.update_task(t2["id"], status="pending")

    count = tm.recover_interrupted()
    assert count == 2
    assert s.get_task(t1["id"])["status"] == "failed"
    assert "手工重试" in s.get_task(t1["id"])["error"]
    assert s.get_task(t2["id"])["status"] == "failed"


def test_status_summary_counts():
    tm = get_task_manager()
    s = tm._store
    p = s.create_project("A", "", "", "", {})
    ep = s.create_episode(p["id"], "e1", "")
    t = s.create_task(p["id"], ep["id"], "compose", None)
    s.update_task(t["id"], status="succeeded")
    summary = tm.status_summary()
    assert summary["succeeded"] >= 1
    assert summary["total"] >= 1


def test_event_bus_receives_task_events():
    from app.events import get_bus

    tm = get_task_manager()
    seen: list[dict] = []
    q = get_bus().subscribe()

    # 直接订阅异步队列在无事件循环时不可用；改用历史断言
    p = tm._store.create_project("A", "", "", "", {})
    ep = tm._store.create_episode(p["id"], "e1", "")
    tm.submit(p["id"], ep["id"], "keyframes", lambda cancel, progress: [])
    _drain(tm)
    time.sleep(0.1)
    types = [e["type"] for e in get_bus().history(100)]
    assert "task" in types
    get_bus().unsubscribe(q)

"""任务管理器：统一任务中心（ThreadPool + SQLite 持久化 + EventBus）。

状态机：pending → running → succeeded / failed / canceled

设计约束（对应需求"可以手工重试，不要在代码中规定重试次数"）：
- 本模块**不含任何自动重试逻辑**：失败即停、错误入库、等待用户触发
  ``retry()``（UI 按钮 / 对话 / REST），重试次数完全由用户决定。
- 任务在 ThreadPoolExecutor 中执行；同一任务 ID 重试时复位状态后重新入队。
- 支持协作式取消：执行函数收到 ``cancel`` 上下文（``should_cancel()``），
  ffmpeg 等子进程通过 ``register_subprocess`` 登记以便取消时终止。
- 服务重启恢复：``recover_interrupted()`` 将遗留 running/pending 任务标记为
  failed（附中文说明），用户可从断点（产物检查点）手工重试续跑。
"""
from __future__ import annotations

import logging
import subprocess
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from app.events import get_bus
from app.store import Store, get_store

logger = logging.getLogger("app.tasks")

VALID_STAGES = ("worldview", "script", "storyboard", "voiceover",
                "keyframes", "clips", "subtitles", "compose")


class TaskCanceledError(RuntimeError):
    """任务被用户取消。"""


class CancelContext:
    """协作式取消上下文（线程安全）。"""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._procs: list[subprocess.Popen] = []
        self._lock = threading.Lock()

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            for p in self._procs:
                try:
                    p.kill()
                except Exception:
                    pass

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def should_cancel(self) -> None:
        if self._event.is_set():
            raise TaskCanceledError("任务已被用户取消")

    def register_subprocess(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.append(proc)


class TaskManager:
    """任务生命周期管理（提交/查询/取消/手工重试）。"""

    def __init__(self, store: Store | None = None, max_workers: int | None = None):
        import os
        self._store = store or get_store()
        self._bus = get_bus()
        workers = max_workers or int(os.environ.get("STUDIO_MAX_WORKERS", "4"))
        self._executor = ThreadPoolExecutor(max_workers=workers,
                                            thread_name_prefix="studio-task")
        self._cancels: dict[str, CancelContext] = {}
        self._events: dict[str, threading.Event] = {}
        self._fns: dict[str, Callable[..., Any]] = {}  # 任务ID → 原执行函数（重试复用）
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 提交与执行
    # ------------------------------------------------------------------
    def submit(self, project_id: str, episode_id: str | None, stage: str,
               fn: Callable[..., Any], params: dict | None = None,
               task_id: str | None = None) -> dict:
        """提交任务（不阻塞）。``fn(cancel, progress) -> artifacts list``。"""
        task = self._store.create_task(project_id, episode_id, stage, params) \
            if task_id is None else self._store.update_task(
                task_id, status="pending", error="", note="")
        tid = task["id"]
        ctx = CancelContext()
        with self._lock:
            self._cancels[tid] = ctx
            self._events[tid] = threading.Event()
            self._fns[tid] = fn
        self._executor.submit(self._run, tid, fn, ctx)
        return task

    def wait(self, tid: str, timeout: float | None = None) -> str:
        """阻塞等待任务进入终态，返回终态（供流水线顺序执行）。"""
        with self._lock:
            ev = self._events.get(tid)
        if ev is None:
            task = self._store.get_task(tid)
            return task["status"] if task else "unknown"
        ev.wait(timeout)
        task = self._store.get_task(tid)
        return task["status"] if task else "unknown"

    def _run(self, tid: str, fn: Callable[..., Any], ctx: CancelContext) -> None:
        task = self._store.get_task(tid)
        if task is None:
            return
        if ctx.cancelled:  # 提交后立即被取消
            self._finish(tid, "canceled", note="已取消")
            return
        self._store.update_task(tid, status="running", error="")
        self._bus.publish("task", task_id=tid, stage=task["stage"],
                          status="running", project_id=task["project_id"],
                          episode_id=task["episode_id"])

        def progress(msg: str, pct: float = 0.0) -> None:
            self._bus.publish("progress", task_id=tid, stage=task["stage"],
                              message=msg, pct=pct)

        try:
            ctx.should_cancel()
            artifacts = fn(cancel=ctx, progress=progress) or []
            self._store.update_task(tid, status="succeeded", artifacts=list(artifacts))
            self._finish(tid, "succeeded")
        except TaskCanceledError:
            self._store.update_task(tid, status="canceled", error="用户取消")
            self._finish(tid, "canceled")
        except Exception as exc:  # noqa: BLE001 —— 错误如实入库，等用户手工重试
            logger.exception("任务 %s 失败", tid)
            err = f"{type(exc).__name__}: {exc}"
            self._store.update_task(tid, status="failed", error=err)
            self._finish(tid, "failed", error=err)

    def _finish(self, tid: str, status: str, error: str = "", note: str = "") -> None:
        task = self._store.get_task(tid)
        with self._lock:
            self._cancels.pop(tid, None)
            ev = self._events.pop(tid, None)
        if ev is not None:
            ev.set()
        self._bus.publish("task", task_id=tid,
                          stage=task["stage"] if task else "?",
                          status=status, error=error, note=note,
                          project_id=task["project_id"] if task else None,
                          episode_id=task["episode_id"] if task else None)

    # ------------------------------------------------------------------
    # 查询 / 取消 / 手工重试
    # ------------------------------------------------------------------
    def get(self, tid: str) -> dict | None:
        return self._store.get_task(tid)

    def list(self, **filters: Any) -> list[dict]:
        return self._store.list_tasks(**filters)

    def cancel(self, tid: str) -> dict:
        task = self._store.get_task(tid)
        if task is None:
            raise KeyError(f"任务不存在: {tid}")
        if task["status"] not in ("pending", "running"):
            raise ValueError(f"任务状态为 {task['status']}，无法取消")
        with self._lock:
            ctx = self._cancels.get(tid)
        if ctx is not None:
            ctx.cancel()
        else:  # 尚未开始执行
            self._store.update_task(tid, status="canceled", error="用户取消")
            self._finish(tid, "canceled", note="队列中取消")
        return self._store.get_task(tid)

    def stored_fn(self, tid: str) -> Callable[..., Any] | None:
        """返回任务首次提交时的执行函数（服务重启后为 None，调用方需重建）。"""
        with self._lock:
            return self._fns.get(tid)

    def retry(self, tid: str, fn_builder: Callable[[dict], Callable[..., Any]] | None = None) -> dict:
        """手工重试：复位同 ID 任务并重新执行。

        优先复用首次提交的执行函数（与首次执行**完全相同**的代码路径，
        包括测试注入的自定义函数）；``fn_builder(task)`` 供调用方在函数
        已丢失（如服务重启）时重建该阶段的执行函数。
        """
        task = self._store.get_task(tid)
        if task is None:
            raise KeyError(f"任务不存在: {tid}")
        if task["status"] == "running":
            raise ValueError("任务正在运行，请先取消再重试")
        fn = fn_builder(task) if fn_builder is not None else self.stored_fn(tid)
        if fn is None:
            raise ValueError("任务没有可重用的执行函数（服务可能重启过），"
                             "请通过流水线断点续跑该阶段")
        return self.submit(task["project_id"], task["episode_id"], task["stage"],
                           fn, params=task["params"], task_id=tid)

    # ------------------------------------------------------------------
    # 恢复
    # ------------------------------------------------------------------
    def recover_interrupted(self) -> int:
        """服务重启后：遗留 running/pending → failed（附说明，等待手工重试）。"""
        count = 0
        for status in ("running", "pending"):
            for t in self._store.list_tasks(status=status):
                self._store.update_task(
                    t["id"], status="failed",
                    error="服务重启导致任务中断；产物检查点仍在，可手工重试续跑")
                self._finish(t["id"], "failed", error="服务重启中断")
                count += 1
        return count

    def status_summary(self) -> dict[str, int]:
        out = {"pending": 0, "running": 0, "succeeded": 0, "failed": 0,
               "canceled": 0, "total": 0}
        for t in self._store.list_tasks(limit=100000):
            out[t["status"]] = out.get(t["status"], 0) + 1
            out["total"] += 1
        return out

    def shutdown(self, timeout: float = 15.0) -> None:
        """等待在跑任务到达终态后关闭线程池（测试隔离 / 进程退出用）。

        先等事件再关池，避免任务线程在存储关闭后仍写库
        （sqlite3.ProgrammingError: Cannot operate on a closed database）。
        """
        import time

        with self._lock:
            events = list(self._events.values())
        deadline = time.time() + timeout
        for ev in events:
            ev.wait(max(0.0, deadline - time.time()))
        self._executor.shutdown(wait=False, cancel_futures=True)


_manager: TaskManager | None = None


def get_task_manager() -> TaskManager:
    global _manager
    if _manager is None:
        _manager = TaskManager()
    return _manager


def reset_task_manager() -> None:
    global _manager
    if _manager is not None:
        _manager.shutdown()
    _manager = None

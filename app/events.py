"""事件总线：任务/进度/聊天事件 → SSE 推送。

- 任务线程通过 ``publish()`` 发布事件（线程安全）。
- SSE 端点通过 ``subscribe()`` 获得 asyncio.Queue（绑定事件循环，
  跨线程用 ``loop.call_soon_threadsafe`` 投递）。
- 保留最近 200 条历史（``history()``），便于前端断线补拉与测试断言。
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Any


class EventBus:
    def __init__(self, history_size: int = 200) -> None:
        self._lock = threading.Lock()
        self._subs: list[asyncio.Queue] = []
        self._history: list[dict] = []
        self._history_size = history_size
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- 生命周期 -------------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # -- 发布 ---------------------------------------------------------------
    def publish(self, type_: str, **data: Any) -> dict:
        event = {"type": type_, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 **data}
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._history_size:
                self._history = self._history[-self._history_size:]
            subs = list(self._subs)
        for q in subs:
            try:
                if self._loop is not None and self._loop.is_running():
                    self._loop.call_soon_threadsafe(q.put_nowait, event)
                else:
                    q.put_nowait(event)
            except Exception:  # 订阅方已关闭等情形，静默丢弃
                pass
        return event

    # -- 订阅 ---------------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def history(self, limit: int = 50) -> list[dict]:
        with self._lock:
            return list(self._history[-limit:])


_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus

"""系统与设置接口：健康矩阵 / 设置读写 / SSE 事件流。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.adapters import registry
from app.config import CAPABILITIES
from app.events import get_bus
from app.schemas import SettingsUpdate
from app.services import ServiceError, system_health, update_settings

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/system/health")
def health():
    return system_health()


@router.get("/system/backends")
def backends():
    """全部能力 × 后端可用性矩阵（设置页渲染用）。"""
    return {cap: registry.list_specs(cap) for cap in CAPABILITIES}


@router.get("/settings")
def get_settings_api():
    from app.config import get_settings
    return get_settings().as_dict()


@router.put("/settings")
def put_settings(req: SettingsUpdate):
    try:
        return update_settings(req.settings)
    except ServiceError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/events")
async def sse_events():
    """SSE 事件流：task/progress/chat/episode/project/pipeline_error。"""
    bus = get_bus()
    queue = bus.subscribe()

    async def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield (f"event: {event['type']}\n"
                           f"data: {json.dumps(event, ensure_ascii=False)}\n\n")
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # 保活，防代理断连
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})

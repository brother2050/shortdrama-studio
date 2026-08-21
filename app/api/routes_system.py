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
from app.services import (ServiceError, reset_settings, system_health,
                          update_settings)

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/system/health")
def health():
    return system_health()


@router.get("/system/backends")
def backends():
    """全部能力 × 后端可用性矩阵（设置页渲染用）。"""
    return {cap: registry.list_specs(cap) for cap in CAPABILITIES}


@router.get("/system/models")
def models_catalog():
    """模型预设目录（统一存放在项目根 models/）。

    设置页「模型预设」下拉 + JSON 自动填充的数据源：
    每个预设含推荐 backend、params 模板（相对路径）与已下载状态。
    """
    from app.models_registry import catalog
    return catalog()


@router.get("/system/vram")
def vram_status():
    """显存状态：设备信息 + 已加载模型列表。"""
    from app.vram import vram_summary
    return vram_summary()


@router.post("/system/vram/release")
def vram_release():
    """手动释放所有已加载模型（释放显存）。"""
    registry.unload_all()
    from app.vram import vram_summary
    return vram_summary()


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


@router.post("/settings/reset")
def post_settings_reset():
    """恢复出厂默认设置（设置页「恢复默认」按钮）。"""
    return reset_settings()


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

"""FastAPI 应用入口：装配路由、静态前端、启动恢复。

启动：`python -m app` 或 `uvicorn app.main:app --port 8320`
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__, paths
from app.api import (routes_chat, routes_media, routes_projects, routes_system,
                     routes_tasks)
from app.events import get_bus
from app.tasks import get_task_manager

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_bus().bind_loop(asyncio.get_running_loop())
    recovered = get_task_manager().recover_interrupted()
    if recovered:
        logger.warning("已恢复 %d 个中断任务（标记 failed，可手工重试）", recovered)
    logger.info("ShortDrama Studio %s 就绪，数据目录: %s",
                __version__, paths.data_dir())
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="ShortDrama Studio", version=__version__,
                  description="完全离线的对话式连续短剧生成平台",
                  lifespan=lifespan)
    for router in (routes_chat.router, routes_projects.router,
                   routes_tasks.router, routes_system.router,
                   routes_media.router):
        app.include_router(router)

    @app.get("/api/version")
    def version():
        return {"app": "shortdrama-studio", "version": __version__}

    # 前端静态资源（零构建 vanilla ES Modules）
    web = paths.web_dir()
    if web.exists():
        app.mount("/static", StaticFiles(directory=str(web)), name="static")

        @app.middleware("http")
        async def _no_cache_static(request, call_next):
            """静态模块强制走条件请求（etag 复验），避免开发迭代时缓存旧 JS。"""
            response = await call_next(request)
            if request.url.path.startswith("/static"):
                response.headers["Cache-Control"] = "no-cache"
            return response

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(str(web / "index.html"))

        @app.get("/favicon.ico", include_in_schema=False)
        def favicon():
            f = web / "favicon.svg"
            return FileResponse(str(f), media_type="image/svg+xml") if f.exists() \
                else JSONResponse({"detail": "no icon"}, status_code=404)
    return app


app = create_app()

"""媒体服务：项目产物（图/视频/音频/文本）受控访问。

路径限定在项目目录内，防目录穿越。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from app import paths

router = APIRouter(prefix="/api/projects/{pid}/media", tags=["media"])

_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".mp4": "video/mp4", ".wav": "audio/wav", ".mp3": "audio/mpeg",
    ".srt": "text/plain; charset=utf-8", ".json": "application/json",
    ".md": "text/plain; charset=utf-8",
}


def _safe_path(pid: str, rel: str) -> Path:
    base = paths.project_dir(pid).resolve()
    target = (base / rel).resolve()
    if base not in target.parents and target != base:
        raise HTTPException(403, "非法路径")
    if not target.is_file():
        raise HTTPException(404, f"文件不存在: {rel}")
    return target


@router.get("/{rel:path}")
def media(pid: str, rel: str, download: bool = False):
    path = _safe_path(pid, rel)
    if path.suffix.lower() in (".srt", ".md") and not download:
        return PlainTextResponse(path.read_text("utf-8"))
    mime = _MIME.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=mime,
                        filename=path.name if download else None)

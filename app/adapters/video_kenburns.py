"""视频后端 1/2：kenburns（ffmpeg zoompan 运镜，默认后端，无 GPU 可用）。

由关键帧生成带运镜的镜头片段：推(in)/拉(out)/摇移(pan)/固定(fixed)，
时长与配音对齐。ffmpeg 缺失时探测为不可用并给出安装指引。
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter, which_ffmpeg)

_MOTIONS = ("in", "out", "pan", "fixed")


def build_filter(motion: str, frames: int, width: int, height: int) -> str:
    """构造 zoompan 滤镜（可独立测试）。"""
    d = max(2, frames)
    if motion == "out":
        zoom = f"1.13-0.13*on/{d}"
    elif motion == "pan":
        zoom = "1.10"
    elif motion == "fixed":
        zoom = "1.01"
    else:  # in
        zoom = f"1+0.13*on/{d}"
    x = f"(iw-iw/zoom)*on/{d}" if motion == "pan" else "(iw-iw/zoom)/2"
    y = "(ih-ih/zoom)/2"
    return (f"scale={width * 2}:{height * 2},"
            f"zoompan=z='{zoom}':x='{x}':y='{y}':d={d}:s={width}x{height}")


def pick_motion(motion: str, seed_text: str) -> str:
    """auto → 按提示词哈希确定性选择运镜。"""
    if motion in _MOTIONS:
        return motion
    h = int(hashlib.md5(seed_text.encode("utf-8")).hexdigest()[:6], 16)
    return _MOTIONS[h % len(_MOTIONS)]


@register_adapter
class KenBurnsVideo(AdapterBase):
    spec = AdapterSpec(
        name="kenburns", capability="video", display_name="Ken Burns 运镜（ffmpeg）",
        description="关键帧 + ffmpeg zoompan 生成推/拉/摇移镜头片段，零模型依赖，"
                    "任何装有 ffmpeg 的环境可用（默认视频后端）。",
        priority=10, requires=[],
        default_params={"fps": 24, "motion": "auto"},
        param_docs={"fps": "帧率（默认 24）",
                    "motion": "运镜 auto/in/out/pan/fixed，auto 按镜头确定性选择"},
        license="MIT（本项目实现，依赖系统 ffmpeg）",
    )

    @classmethod
    def _extra_available(cls) -> bool:
        return which_ffmpeg() is not None

    @classmethod
    def _unavailable_reason(cls) -> str:
        return "未找到 ffmpeg：请安装（apt install ffmpeg / brew install ffmpeg）"

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        ffmpeg = which_ffmpeg()
        if not ffmpeg:
            raise AdapterError(self._unavailable_reason())
        image = Path(ctx["image_path"])
        if not image.exists():
            raise AdapterError(f"关键帧不存在: {image}")
        out = Path(ctx["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        fps = int(ctx.get("fps") or self.params.get("fps", 24))
        width = int(ctx.get("width") or 1280)
        height = int(ctx.get("height") or 720)
        duration = float(ctx.get("duration") or 4.0)
        frames = max(2, round(duration * fps))
        motion = pick_motion(str(ctx.get("motion") or self.params.get("motion", "auto")),
                             f"{image}")
        vf = build_filter(motion, frames, width, height)
        cmd = [ffmpeg, "-y", "-loop", "1", "-i", str(image),
               "-vf", vf, "-frames:v", str(frames), "-r", str(fps),
               "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
               str(out)]
        if progress:
            progress(f"ffmpeg 运镜 {motion}，{frames} 帧", 40.0)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0 or not out.exists():
            raise AdapterError(f"ffmpeg 失败({proc.returncode}): {proc.stderr[-800:]}")
        if progress:
            progress("片段完成", 90.0)
        return {"path": str(out), "duration": round(frames / fps, 3), "motion": motion}

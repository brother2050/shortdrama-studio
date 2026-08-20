"""视频后端 2/2：wan_i2v（ModelScope Wan2.1/2.2 图生视频，diffusers 本地）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)


@register_adapter
class WanI2VVideo(AdapterBase):
    spec = AdapterSpec(
        name="wan_i2v", capability="video", display_name="Wan 图生视频（diffusers）",
        description="本地 Wan 图生视频：Wan-AI/Wan2.1-T2V-1.3B（≈8GB）、Wan2.2-TI2V-5B"
                    "（单卡 4090 可 720p）。以关键帧为首帧，保持画面一致性。",
        priority=5, requires=["torch", "diffusers"],
        default_params={
            "model_path": "", "num_frames": 81, "fps": 16,
            "guidance": 6.0, "steps": 30,
        },
        param_docs={
            "model_path": "本地模型目录（scripts/download_models.py 下载后的路径）",
            "num_frames": "生成帧数（81 帧 ≈ 5 秒@16fps）",
            "fps": "输出帧率（Wan2.1 为 16）", "guidance": "引导强度", "steps": "采样步数",
        },
        vram_gb=8.0, license="Apache-2.0（Wan2.x）",
    )

    _pipe = None

    def _load(self):
        path = str(self.params.get("model_path") or "").strip()
        if not path:
            raise AdapterError(
                "wan_i2v 需要设置参数 model_path（本地模型目录）。"
                "离线下载：python scripts/download_models.py --capability video --local-dir ./models")
        if self._pipe is not None:
            return self._pipe
        import torch
        from diffusers import WanImageToVideoPipeline
        dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        pipe = WanImageToVideoPipeline.from_pretrained(path, torch_dtype=dtype)
        if torch.cuda.is_available():
            pipe.to("cuda")
        self._pipe = pipe
        return pipe

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        from PIL import Image  # diffusers 环境必带 Pillow
        pipe = self._load()
        image_path = Path(ctx["image_path"])
        if not image_path.exists():
            raise AdapterError(f"关键帧不存在: {image_path}")
        out = Path(ctx["out_path"])
        prompt = str(ctx.get("prompt", ""))
        fps = int(self.params.get("fps", 16))
        if progress:
            progress("Wan 扩散采样中（较慢，属正常）", 40.0)
        video = pipe(
            prompt=prompt,
            image=Image.open(image_path),
            num_frames=int(self.params.get("num_frames", 81)),
            guidance_scale=float(self.params.get("guidance", 6.0)),
            num_inference_steps=int(self.params.get("steps", 30)),
        ).frames[0]
        out.parent.mkdir(parents=True, exist_ok=True)
        from diffusers.utils import export_to_video
        export_to_video(video, str(out), fps=fps)
        n = len(video)
        if progress:
            progress("片段完成", 90.0)
        return {"path": str(out), "duration": round(n / fps, 3), "motion": "wan_i2v"}

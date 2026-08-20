"""图像后端 2/2：diffusers（ModelScope 本地 SD / SDXL / FLUX / Qwen-Image）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)
from app.vram import ModelSlot, pick_device, unload_model, check_vram

_NEG_DEFAULT = "低清, 变形, 多余肢体, 文字水印, 过曝, 摩尔纹"


@register_adapter
class DiffusersImage(AdapterBase):
    spec = AdapterSpec(
        name="diffusers", capability="image", display_name="diffusers（SD/SDXL/FLUX/Qwen-Image）",
        description="本地 diffusers 文生图：AI-ModelScope/stable-diffusion-v1-5（4-6GB）、"
                    "stabilityai/stable-diffusion-xl-base-1.0（6-10GB）、"
                    "AI-ModelScope/FLUX.1-schnell（8-12GB, Apache-2.0）、Qwen/Qwen-Image-2512。",
        priority=5, requires=["torch", "diffusers"],
        default_params={
            "model_path": "",
            "steps": 28,           # FLUX.1-schnell 建议 4
            "guidance": 7.0,       # schnell 固定 3.5
            "negative_prompt": _NEG_DEFAULT,
        },
        param_docs={
            "model_path": "本地模型目录（scripts/download_models.py 下载后的路径）",
            "steps": "采样步数（默认 28；FLUX.1-schnell 建议 4）",
            "guidance": "CFG 引导强度（默认 7.0）",
            "negative_prompt": "负面提示词",
        },
        vram_gb=6.0, license="遵循所选模型许可（FLUX.1-schnell 为 Apache-2.0）",
    )

    _slot = ModelSlot("image_diffusers")

    def _load(self):
        path = str(self.params.get("model_path") or "").strip()
        if not path:
            raise AdapterError(
                "diffusers 图像后端需要设置参数 model_path（本地模型目录）。"
                "离线下载：python scripts/download_models.py --capability image --local-dir ./models")
        if self._slot.is_loaded:
            return self._slot.model
        if not check_vram(self.spec.vram_gb):
            raise AdapterError(f"显存不足：需要约 {self.spec.vram_gb}GB，当前可用不足。"
                               f"请先在系统页查看显存状态，或切换到不需要 GPU 的后端。")

        def _do_load():
            import torch
            from diffusers import FluxPipeline, StableDiffusionPipeline
            cls = FluxPipeline if "flux" in path.lower() else StableDiffusionPipeline
            device = pick_device(self.params.get("device", "auto"), self.spec.vram_gb)
            dtype = torch.bfloat16 if cls is FluxPipeline else torch.float16
            if device != "cuda":
                dtype = torch.float32
            try:
                pipe = cls.from_pretrained(path, torch_dtype=dtype)
                if device == "cuda":
                    pipe = pipe.to("cuda")
            except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
                if "out of memory" in str(exc).lower():
                    # OOM 恢复：回退到 CPU + float32
                    pipe = cls.from_pretrained(path, torch_dtype=torch.float32)
                    pipe = pipe.to("cpu")
                else:
                    raise
            return pipe

        return self._slot.load(_do_load)

    def unload(self) -> None:
        self._slot.unload()

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        pipe = self._load()
        width = int(ctx.get("width") or 1280)
        height = int(ctx.get("height") or 720)
        prompt = str(ctx.get("prompt", ""))
        negative = ctx.get("negative_prompt") or self.params.get("negative_prompt", _NEG_DEFAULT)
        if progress:
            progress(f"采样中 steps={self.params.get('steps')}", 40.0)
        kwargs: dict[str, Any] = {
            "prompt": prompt, "width": width, "height": height,
            "num_inference_steps": int(self.params.get("steps", 28)),
        }
        if isinstance(pipe, StableDiffusionPipeline):
            kwargs["guidance_scale"] = float(self.params.get("guidance", 7.0))
            kwargs["negative_prompt"] = negative
        else:
            kwargs["guidance_scale"] = 3.5
        image = pipe(**kwargs).images[0]
        out = Path(ctx["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(out)
        if progress:
            progress("图像完成", 90.0)
        return {"path": str(out), "width": width, "height": height}

"""图像后端 2/2：diffsynth（DiffSynth-Studio 本地 SD/SDXL/FLUX/Qwen-Image）。

使用 ModelScope DiffSynth-Studio 引擎替代 diffusers，支持更丰富的模型生态。
DiffSynth-Studio 更新频繁，作为外部依赖安装（``pip install diffsynth`` 或
``git clone + pip install -e .``），不移植代码到本项目中。

模型通过 ``ModelConfig(model_id=...)`` 自动从 ModelScope 下载，支持
``DIFFSYNTH_MODEL_BASE_PATH`` 环境变量指定缓存目录。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)
from app.vram import ModelSlot, check_vram, pick_device

_NEG_DEFAULT = "低清, 变形, 多余肢体, 文字水印, 过曝, 摩尔纹"

# 支持的模型预设：preset_name → (model_id, pipeline_key, description)
_MODEL_PRESETS: dict[str, tuple[str, str, str]] = {
    "sd15": ("AI-ModelScope/stable-diffusion-v1-5", "sd", "入门：快、省显存"),
    "sdxl": ("stabilityai/stable-diffusion-xl-base-1.0", "sdxl", "推荐：质量好（需 ≥10GB 显存）"),
    "flux-schnell": ("AI-ModelScope/FLUX.1-schnell", "flux", "高质量档，4 步出图（Apache-2.0）"),
}


@register_adapter
class DiffSynthImage(AdapterBase):
    spec = AdapterSpec(
        name="diffsynth", capability="image",
        display_name="DiffSynth-Studio（SD/SDXL/FLUX）",
        description="DiffSynth-Studio 本地文生图：AI-ModelScope/stable-diffusion-v1-5"
        "（4-6GB）、stabilityai/stable-diffusion-xl-base-1.0（6-10GB）、"
        "AI-ModelScope/FLUX.1-schnell（8-12GB, Apache-2.0）。",
        priority=5, requires=["diffsynth"],
        default_params={
            "model_preset": "sd15",
            "steps": 28,
            "guidance": 7.0,
            "negative_prompt": _NEG_DEFAULT,
        },
        param_docs={
            "model_preset": "模型预设：sd15 / sdxl / flux-schnell",
            "steps": "采样步数（默认 28；FLUX.1-schnell 建议 4）",
            "guidance": "CFG 引导强度（默认 7.0；FLUX 建议 3.5）",
            "negative_prompt": "负面提示词",
        },
        vram_gb=6.0,
        license="遵循所选模型许可（FLUX.1-schnell 为 Apache-2.0）",
    )

    _slot = ModelSlot("image_diffsynth", capability="image")

    def _load(self):
        if self._slot.is_loaded:
            return self._slot.model
        if not check_vram(self.spec.vram_gb):
            raise AdapterError(f"显存不足：需要约 {self.spec.vram_gb}GB，当前可用不足。"
                               f"请先在系统页查看显存状态，或切换到不需要 GPU 的后端。")

        preset = str(self.params.get("model_preset", "sd15")).strip()
        if preset not in _MODEL_PRESETS:
            raise AdapterError(
                f"未知模型预设 {preset!r}，可选: {list(_MODEL_PRESETS)}")
        model_id, pipe_key, _ = _MODEL_PRESETS[preset]

        def _do_load():
            import torch
            from diffsynth.core import ModelConfig

            device = pick_device(self.params.get("device", "auto"),
                                 self.spec.vram_gb)
            dtype = torch.bfloat16 if device == "cuda" else torch.float32

            if pipe_key == "sd":
                from diffsynth.pipelines.stable_diffusion import (
                    StableDiffusionPipeline)
                pipe_cls = StableDiffusionPipeline
                model_configs = [
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="text_encoder/model.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="unet/diffusion_pytorch_model.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="vae/diffusion_pytorch_model.safetensors"),
                ]
                tokenizer_config = ModelConfig(
                    model_id=model_id, origin_file_pattern="tokenizer/")
            elif pipe_key == "sdxl":
                from diffsynth.pipelines.stable_diffusion_xl import (
                    StableDiffusionXLPipeline)
                pipe_cls = StableDiffusionXLPipeline
                model_configs = [
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="text_encoder/model.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="text_encoder_2/model.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="unet/diffusion_pytorch_model.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="vae/diffusion_pytorch_model.safetensors"),
                ]
                tokenizer_config = ModelConfig(
                    model_id=model_id, origin_file_pattern="tokenizer/")
            else:  # flux
                from diffsynth.pipelines.flux2_image import Flux2ImagePipeline
                pipe_cls = Flux2ImagePipeline
                model_configs = [
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="text_encoder/*.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="transformer/*.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="vae/diffusion_pytorch_model.safetensors"),
                ]
                tokenizer_config = ModelConfig(
                    model_id=model_id, origin_file_pattern="tokenizer/")

            try:
                pipe = pipe_cls.from_pretrained(
                    torch_dtype=dtype,
                    model_configs=model_configs,
                    tokenizer_config=tokenizer_config,
                )
            except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
                if "out of memory" in str(exc).lower():
                    pipe = pipe_cls.from_pretrained(
                        torch_dtype=torch.float32,
                        model_configs=model_configs,
                        tokenizer_config=tokenizer_config,
                    )
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
        negative = (ctx.get("negative_prompt")
                     or self.params.get("negative_prompt", _NEG_DEFAULT))
        preset = str(self.params.get("model_preset", "sd15"))
        steps = int(self.params.get("steps", 28))
        guidance = float(self.params.get("guidance", 7.0))

        if progress:
            progress(f"DiffSynth 采样中 steps={steps}", 40.0)

        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "height": height,
            "width": width,
            "num_inference_steps": steps,
            "seed": 42,
        }
        if preset == "flux-schnell":
            kwargs["cfg_scale"] = 3.5
        else:
            kwargs["cfg_scale"] = guidance
            kwargs["negative_prompt"] = negative

        image = pipe(**kwargs)
        out = Path(ctx["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(out))
        if progress:
            progress("图像完成", 90.0)
        return {"path": str(out), "width": width, "height": height}

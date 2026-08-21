"""图像后端 2/2：diffsynth（DiffSynth-Studio 本地 SD/SDXL/FLUX/Qwen-Image）。

使用 ModelScope DiffSynth-Studio 引擎替代 diffusers，支持更丰富的模型生态。
DiffSynth-Studio 更新频繁，作为外部依赖安装（``pip install diffsynth`` 或
``git clone + pip install -e .``），不移植代码到本项目中。

模型通过 ``ModelConfig(model_id=...)`` 自动从 ModelScope 下载，支持
``DIFFSYNTH_MODEL_BASE_PATH`` 环境变量指定缓存目录。

对照官方 examples 的关键差异：
- Qwen-Image 系列：``QwenImagePipeline`` + ``tokenizer_config``；
- Qwen-Image-Edit-2509：多图编辑模型，用 ``processor_config``（非 tokenizer），
  调用时传 ``edit_image=[PIL.Image, ...]``（必须是列表）+ ``edit_image_auto_resize``；
- FLUX 走 ``FluxImagePipeline``（FLUX.1-schnell）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)
from app.vram import ModelSlot, check_vram, pick_device

_NEG_DEFAULT = "低清, 变形, 多余肢体, 文字水印, 过曝, 摩尔纹"

# 模型预设：preset → {model_id, pipe_key, vram_gb, desc, edit}
# 文件 pattern 对照官方 examples（qwen_image/flux 目录）。
_MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "sd15": {
        "model_id": "AI-ModelScope/stable-diffusion-v1-5",
        "pipe": "sd", "vram_gb": 6.0, "edit": False,
        "desc": "入门：快、省显存（4-6GB）",
    },
    "sdxl": {
        "model_id": "stabilityai/stable-diffusion-xl-base-1.0",
        "pipe": "sdxl", "vram_gb": 10.0, "edit": False,
        "desc": "推荐：关键帧质量好（需 ≥10GB 显存）",
    },
    "flux-schnell": {
        "model_id": "AI-ModelScope/FLUX.1-schnell",
        "pipe": "flux", "vram_gb": 12.0, "edit": False,
        "desc": "高质量档，4 步出图（Apache-2.0）",
    },
    "qwen-image": {
        "model_id": "Qwen/Qwen-Image",
        "pipe": "qwen", "vram_gb": 24.0, "edit": False,
        "desc": "Qwen-Image 文生图：中文语义强，画质高（需 ≥24GB 显存）",
    },
    "qwen-image-edit": {
        "model_id": "Qwen/Qwen-Image-Edit-2509",
        "pipe": "qwen_edit", "vram_gb": 24.0, "edit": True,
        "desc": "Qwen-Image-Edit-2509 多图编辑：支持参考图（角色一致性）",
    },
}


@register_adapter
class DiffSynthImage(AdapterBase):
    spec = AdapterSpec(
        name="diffsynth", capability="image",
        display_name="DiffSynth-Studio（SD/SDXL/FLUX/Qwen-Image）",
        description="DiffSynth-Studio 本地文生图/编辑：sd15（4-6GB）、"
        "sdxl（6-10GB）、flux-schnell（8-12GB, Apache-2.0）、"
        "qwen-image（高质量中文语义）、qwen-image-edit（多图编辑，"
        "支持参考图锁定角色外貌，实现跨镜头角色一致性）。",
        priority=5, requires=["diffsynth"],
        default_params={
            "model_preset": "sd15",
            "steps": 28,
            "guidance": 7.0,
            "negative_prompt": _NEG_DEFAULT,
        },
        param_docs={
            "model_preset": "模型预设：sd15 / sdxl / flux-schnell / "
                            "qwen-image / qwen-image-edit",
            "steps": "采样步数（默认 28；FLUX.1-schnell 建议 4；Qwen 建议 40）",
            "guidance": "CFG 引导强度（默认 7.0；FLUX 建议 3.5）",
            "negative_prompt": "负面提示词",
        },
        vram_gb=6.0,
        license="遵循所选模型许可（FLUX.1-schnell 为 Apache-2.0）",
    )

    _slot = ModelSlot("image_diffsynth", capability="image")

    # ------------------------------------------------------------------
    def _preset(self) -> dict[str, Any]:
        preset = str(self.params.get("model_preset", "sd15")).strip()
        if preset not in _MODEL_PRESETS:
            raise AdapterError(
                f"未知模型预设 {preset!r}，可选: {list(_MODEL_PRESETS)}")
        return _MODEL_PRESETS[preset]

    def _load(self):
        if self._slot.is_loaded:
            return self._slot.model
        need_gb = float(self._preset()["vram_gb"])
        if not check_vram(need_gb):
            raise AdapterError(
                f"显存不足：当前预设需要约 {need_gb}GB，当前可用不足。"
                f"请先在系统页查看显存状态，或切换到不需要 GPU 的后端。")

        conf = self._preset()
        model_id = conf["model_id"]

        def _build_configs():
            """按预设构造 (model_configs, tokenizer/processor_config)。"""
            from diffsynth.core import ModelConfig

            if conf["pipe"] == "sd":
                configs = [
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="text_encoder/model.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="unet/diffusion_pytorch_model.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="vae/diffusion_pytorch_model.safetensors"),
                ]
                tok = ModelConfig(model_id=model_id,
                                  origin_file_pattern="tokenizer/")
                return configs, tok, None
            if conf["pipe"] == "sdxl":
                configs = [
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="text_encoder/model.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="text_encoder_2/model.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="unet/diffusion_pytorch_model.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="vae/diffusion_pytorch_model.safetensors"),
                ]
                tok = ModelConfig(model_id=model_id,
                                  origin_file_pattern="tokenizer/")
                return configs, tok, None
            if conf["pipe"] == "flux":
                configs = [
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="text_encoder/*.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="transformer/*.safetensors"),
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="vae/diffusion_pytorch_model.safetensors"),
                ]
                tok = ModelConfig(model_id=model_id,
                                  origin_file_pattern="tokenizer/")
                return configs, tok, None
            if conf["pipe"] == "qwen":
                # Qwen-Image 文生图（对照 examples/qwen_image/Qwen-Image.py）
                configs = [
                    ModelConfig(model_id=model_id,
                                origin_file_pattern="transformer/diffusion_pytorch_model*.safetensors"),
                    ModelConfig(model_id="Qwen/Qwen-Image",
                                origin_file_pattern="text_encoder/model*.safetensors"),
                    ModelConfig(model_id="Qwen/Qwen-Image",
                                origin_file_pattern="vae/diffusion_pytorch_model.safetensors"),
                ]
                tok = ModelConfig(model_id="Qwen/Qwen-Image",
                                  origin_file_pattern="tokenizer/")
                return configs, tok, None
            # Qwen-Image-Edit-2509 多图编辑（对照 Qwen-Image-Edit-2509.py：
            # transformer 来自 Edit-2509，text_encoder/vae 来自 Qwen-Image，
            # processor 来自 Qwen/Qwen-Image-Edit）
            configs = [
                ModelConfig(model_id=model_id,
                            origin_file_pattern="transformer/diffusion_pytorch_model*.safetensors"),
                ModelConfig(model_id="Qwen/Qwen-Image",
                            origin_file_pattern="text_encoder/model*.safetensors"),
                ModelConfig(model_id="Qwen/Qwen-Image",
                            origin_file_pattern="vae/diffusion_pytorch_model.safetensors"),
            ]
            processor = ModelConfig(model_id="Qwen/Qwen-Image-Edit",
                                    origin_file_pattern="processor/")
            return configs, None, processor

        def _do_load():
            import torch

            device = pick_device(self.params.get("device", "auto"), need_gb)
            dtype = torch.bfloat16 if device == "cuda" else torch.float32

            if conf["pipe"] == "sd":
                from diffsynth.pipelines.stable_diffusion import (
                    StableDiffusionPipeline)
                pipe_cls = StableDiffusionPipeline
            elif conf["pipe"] == "sdxl":
                from diffsynth.pipelines.stable_diffusion_xl import (
                    StableDiffusionXLPipeline)
                pipe_cls = StableDiffusionXLPipeline
            elif conf["pipe"] == "flux":
                from diffsynth.pipelines.flux_image import FluxImagePipeline
                pipe_cls = FluxImagePipeline
            else:  # qwen / qwen_edit
                from diffsynth.pipelines.qwen_image import QwenImagePipeline
                pipe_cls = QwenImagePipeline

            model_configs, tok_config, processor_config = _build_configs()

            def _from(dtype_, ):
                kw: dict[str, Any] = {
                    "torch_dtype": dtype_,
                    "model_configs": model_configs,
                }
                if tok_config is not None:
                    kw["tokenizer_config"] = tok_config
                if processor_config is not None:
                    kw["processor_config"] = processor_config
                return pipe_cls.from_pretrained(**kw)

            try:
                return _from(dtype)
            except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
                if "out of memory" not in str(exc).lower():
                    raise
                # OOM 回退 float32（慢但能跑，保任务不中断）
                return _from(torch.float32)

        return self._slot.load(_do_load)

    def unload(self) -> None:
        self._slot.unload()

    # ------------------------------------------------------------------
    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        from PIL import Image

        pipe = self._load()
        conf = self._preset()
        width = int(ctx.get("width") or 1280)
        height = int(ctx.get("height") or 720)
        prompt = str(ctx.get("prompt", ""))
        negative = (ctx.get("negative_prompt")
                    or self.params.get("negative_prompt", _NEG_DEFAULT))
        preset = str(self.params.get("model_preset", "sd15"))
        steps = int(self.params.get("steps", 28))
        guidance = float(self.params.get("guidance", 7.0))

        # 参考图（角色一致性）：qwen-image-edit 预设 + ctx 提供 ref_images 时启用
        ref_paths = ctx.get("ref_images") or []
        ref_images = [Image.open(p) for p in ref_paths
                      if Path(p).exists()] if ref_paths else []

        if progress:
            mode = "多图编辑（参考图一致性）" if ref_images else "文生图"
            progress(f"DiffSynth {mode} steps={steps}", 40.0)

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

        if ref_images:
            # Qwen-Image-Edit-2509：edit_image 必须是列表（官方 examples 约定）
            kwargs["edit_image"] = ref_images
            kwargs["edit_image_auto_resize"] = True

        image = pipe(**kwargs)
        out = Path(ctx["out_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        image.save(str(out))
        if progress:
            progress("图像完成", 90.0)
        return {"path": str(out), "width": width, "height": height,
                "edit_mode": bool(ref_images)}

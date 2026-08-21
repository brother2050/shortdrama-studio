"""视频后端 2/2：diffsynth_wan（DiffSynth-Studio Wan 图生视频）。

使用 DiffSynth-Studio 的 ``WanVideoPipeline`` 替代 diffusers 的
``WanImageToVideoPipeline``。以关键帧为参考图像，生成动态视频片段。
DiffSynth-Studio 作为外部依赖安装，不移植代码到本项目。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)
from app.vram import ModelSlot, check_vram, pick_device


@register_adapter
class DiffSynthWanVideo(AdapterBase):
    spec = AdapterSpec(
        name="diffsynth_wan", capability="video",
        display_name="Wan 图生视频（DiffSynth-Studio）",
        description="DiffSynth-Studio Wan 图生视频：Wan-AI/Wan2.1-T2V-1.3B"
        "（≈8GB）。以关键帧为首帧参考，保持画面一致性。",
        priority=5, requires=["diffsynth"],
        default_params={
            "model_id": "Wan-AI/Wan2.1-T2V-1.3B",
            "num_frames": 81,
            "fps": 16,
            "guidance": 6.0,
            "steps": 30,
        },
        param_docs={
            "model_id": "ModelScope 模型 ID（如 Wan-AI/Wan2.1-T2V-1.3B）",
            "num_frames": "生成帧数（81 帧 ≈ 5 秒@16fps）",
            "fps": "输出帧率（Wan2.1 为 16）",
            "guidance": "引导强度",
            "steps": "采样步数",
        },
        vram_gb=8.0,
        license="Apache-2.0（Wan2.x）",
    )

    _slot = ModelSlot("video_diffsynth_wan", capability="video")

    def _load(self):
        if self._slot.is_loaded:
            return self._slot.model
        if not check_vram(self.spec.vram_gb):
            raise AdapterError(f"显存不足：需要约 {self.spec.vram_gb}GB，当前可用不足。"
                               f"请先在系统页查看显存状态，或切换到不需要 GPU 的后端。")

        model_id = str(self.params.get("model_id", "Wan-AI/Wan2.1-T2V-1.3B")).strip()
        if not model_id:
            raise AdapterError(
                "diffsynth_wan 需要设置参数 model_id"
                "（如 Wan-AI/Wan2.1-T2V-1.3B）")

        def _do_load():
            import torch
            from diffsynth.core import ModelConfig
            from diffsynth.pipelines.wan_video import WanVideoPipeline

            device = pick_device(self.params.get("device", "auto"),
                                 self.spec.vram_gb)
            dtype = torch.bfloat16 if device == "cuda" else torch.float32

            vram_config = {
                "offload_dtype": dtype,
                "offload_device": "cpu" if device == "cuda" else "cpu",
                "onload_dtype": dtype,
                "onload_device": device,
            }

            model_configs = [
                ModelConfig(model_id=model_id,
                            origin_file_pattern="models/diffusion_pytorch_model*.safetensors",
                            **vram_config),
                ModelConfig(model_id=model_id,
                            origin_file_pattern="models_t5_umt5-xxl.pth",
                            **vram_config),
                ModelConfig(model_id=model_id,
                            origin_file_pattern="Wan2.1_VAE.pth",
                            **vram_config),
            ]
            tokenizer_config = ModelConfig(
                model_id=model_id,
                origin_file_pattern="google/umt5-xxl/")

            try:
                pipe = WanVideoPipeline.from_pretrained(
                    torch_dtype=dtype,
                    device=device,
                    model_configs=model_configs,
                    tokenizer_config=tokenizer_config,
                )
            except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
                if "out of memory" in str(exc).lower():
                    pipe = WanVideoPipeline.from_pretrained(
                        torch_dtype=torch.float32,
                        device="cpu",
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
        from PIL import Image

        pipe = self._load()
        image_path = Path(ctx["image_path"])
        if not image_path.exists():
            raise AdapterError(f"关键帧不存在: {image_path}")
        out = Path(ctx["out_path"])
        prompt = str(ctx.get("prompt", ""))
        fps = int(self.params.get("fps", 16))

        if progress:
            progress("DiffSynth Wan 扩散采样中（较慢，属正常）", 40.0)

        ref_image = Image.open(image_path)
        video = pipe(
            prompt=prompt,
            negative_prompt="模糊, 低质量, 变形",
            vace_reference_image=ref_image,
            seed=1,
            tiled=True,
        )

        out.parent.mkdir(parents=True, exist_ok=True)
        from diffsynth.utils.data import save_video
        save_video(video, str(out), fps=fps, quality=5)

        n = len(video) if hasattr(video, '__len__') else int(
            self.params.get("num_frames", 81))
        if progress:
            progress("片段完成", 90.0)
        return {"path": str(out), "duration": round(n / fps, 3),
                "motion": "diffsynth_wan"}

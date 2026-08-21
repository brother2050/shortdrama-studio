"""视频后端 2/2：diffsynth_wan（DiffSynth-Studio Wan 视频）。

使用 DiffSynth-Studio 的 ``WanVideoPipeline`` 替代 diffusers 的
``WanImageToVideoPipeline``。以关键帧为首帧参考生成动态片段，并支持
「首尾帧过渡」（FLF2V：首帧 + 下一镜尾帧 → 平滑转场片段）。
DiffSynth-Studio 作为外部依赖安装，不移植代码到本项目。

参数与文件 pattern 均对照 DiffSynth-Studio 官方 examples：
- Wan2.2-TI2V-5B：单模型同时支持 T2V/I2V（≈8GB 显存，默认推荐）
- Wan2.1-T2V-1.3B：轻量 T2V（视频续写 input_video 场景）
- Wan2.2-I2V-A14B：高质量 I2V（需 ≥16GB 显存）
- Wan2.1-FLF2V-14B-720P：首尾帧过渡（镜头转场）

模型加载双模式（统一存放于项目根 ``models/``，见 app/models_registry.py）：
- **本地直载**：``models/video/<预设名>/`` 已下载 → ``ModelConfig(path=...)``
  完全离线（tokenizer 来自共享组件 ``models/video/_shared/umt5-xxl/``）；
- **在线回退**：未下载 → ``ModelConfig(model_id=...)`` 自动下载，且
  ``DIFFSYNTH_MODEL_BASE_PATH`` 锚定到项目根 ``models/``（不散落到 cwd）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app import models_registry
from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)
from app.adapters.model_paths import ensure_diffsynth_base_path
from app.vram import ModelSlot, check_vram, pick_device

# Wan 官方 negative prompt（examples/wanvideo 通用）
_NEG_WAN = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，"
            "静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，"
            "多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，"
            "形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，"
            "背景人很多，倒着走")

# 模型预设：preset → (model_id, is_flf2v, vram_gb, 说明)
# 文件 pattern 对照官方 examples（注意 diffusion 权重在仓库根目录，
# tokenizer 统一来自 Wan2.1-T2V-1.3B）。
_MODEL_PRESETS: dict[str, dict[str, Any]] = {
    "wan2.2-ti2v-5b": {
        "model_id": "Wan-AI/Wan2.2-TI2V-5B",
        "flf2v": False,
        "vram_gb": 8.19,   # 8GB 依官方 model card
        "files": ["diffusion_pytorch_model*.safetensors",
                  "models_t5_umt5-xxl-enc-bf16.pth", "Wan2.2_VAE.pth"],
        "desc": "默认推荐：单模型支持图生视频，≈8GB 显存",
    },
    "wan2.1-t2v-1.3b": {
        "model_id": "Wan-AI/Wan2.1-T2V-1.3B",
        "flf2v": False,
        "vram_gb": 8.19,
        "files": ["diffusion_pytorch_model*.safetensors",
                  "models_t5_umt5-xxl-enc-bf16.pth", "Wan2.1_VAE.pth"],
        "desc": "轻量 T2V（无 input_image 时走纯文生视频）",
    },
    "wan2.2-i2v-a14b": {
        "model_id": "Wan-AI/Wan2.2-I2V-A14B",
        "flf2v": False,
        "vram_gb": 24.0,
        "files": ["diffusion_pytorch_model*.safetensors",
                  "models_t5_umt5-xxl-enc-bf16.pth", "Wan2.2_VAE.pth"],
        "desc": "高质量 I2V（MoE，需 ≥24GB 显存）",
    },
    "wan2.1-flf2v-14b": {
        "model_id": "Wan-AI/Wan2.1-FLF2V-14B-720P",
        "flf2v": True,
        "vram_gb": 24.0,
        "files": ["diffusion_pytorch_model*.safetensors",
                  "models_t5_umt5-xxl-enc-bf16.pth", "Wan2.1_VAE.pth",
                  "models_clip_open-clip-xlm-roberta-large-vit-huge-14.pth"],
        "desc": "首尾帧过渡：首帧+尾帧→转场片段（镜头衔接）",
    },
}
_TOKENIZER_MODEL = "Wan-AI/Wan2.1-T2V-1.3B"


def _save_video(video, path: str, fps: int, quality: int = 5) -> None:
    """落盘 mp4（diffsynth.utils.data.save_video 的惰性封装，便于测试注入）。"""
    from diffsynth.utils.data import save_video
    save_video(video, path, fps=fps, quality=quality)


def _vram_config(device: str, dtype) -> dict[str, Any]:
    """分层显存管理（对照官方 low_vram examples：offload→onload→computation）。"""
    return {
        "offload_dtype": dtype,
        "offload_device": "cpu",
        "onload_dtype": dtype,
        "onload_device": device,
        "preparing_dtype": dtype,
        "preparing_device": device,
        "computation_dtype": dtype,
        "computation_device": device,
    }


@register_adapter
class DiffSynthWanVideo(AdapterBase):
    spec = AdapterSpec(
        name="diffsynth_wan", capability="video",
        display_name="Wan 视频（DiffSynth-Studio）",
        description="DiffSynth-Studio Wan 视频生成：Wan2.2-TI2V-5B（默认，"
        "图生视频，≈8GB）、Wan2.1-T2V-1.3B（轻量）、Wan2.2-I2V-A14B（高质量）、"
        "Wan2.1-FLF2V-14B（首尾帧过渡转场）。以关键帧为首帧，保持画面一致性；"
        "传入下一镜头关键帧时自动生成平滑转场。",
        priority=5, requires=["diffsynth"],
        default_params={
            "model_preset": "wan2.2-ti2v-5b",
            "num_frames": 81,
            "fps": 15,
            "steps": 30,
            "guidance": 6.0,
            "negative_prompt": _NEG_WAN,
        },
        param_docs={
            "model_preset": "模型预设：wan2.2-ti2v-5b / wan2.1-t2v-1.3b / "
                            "wan2.2-i2v-a14b / wan2.1-flf2v-14b",
            "num_frames": "生成帧数（81 帧 ≈ 5.4 秒@15fps；4n+1 更稳定）",
            "fps": "输出帧率（Wan 官方标准 15）",
            "steps": "采样步数（默认 30）",
            "guidance": "引导强度（默认 6.0）",
            "negative_prompt": "负面提示词（默认 Wan 官方通用词）",
        },
        vram_gb=8.2,
        license="Apache-2.0（Wan2.x）",
    )

    _slot = ModelSlot("video_diffsynth_wan", capability="video")

    # ------------------------------------------------------------------
    def _preset(self) -> dict[str, Any]:
        preset = str(self.params.get("model_preset", "wan2.2-ti2v-5b")).strip()
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
        preset_name = str(self.params.get("model_preset", "wan2.2-ti2v-5b"))
        local_dir = models_registry.preset_local_dir("video", preset_name)
        use_local = local_dir.is_dir() and any(local_dir.iterdir())

        def _files(pattern: str):
            """在预设目录 glob 文件（单文件→str，多分片→list）。"""
            hits = sorted(local_dir.glob(pattern))
            if not hits:
                raise AdapterError(
                    f"本地模型缺文件：期望 {local_dir / pattern}。"
                    f"重新下载：python scripts/download_models.py "
                    f"--capability video --preset {preset_name}")
            return str(hits[0]) if len(hits) == 1 else [str(h) for h in hits]

        def _umt5_dir() -> str:
            d = models_registry.shared_local_dir("video/_shared/umt5-xxl")
            if not d.is_dir() or not any(d.iterdir()):
                raise AdapterError(
                    f"共享 tokenizer 缺目录：期望 {d}。"
                    f"重新下载：python scripts/download_models.py "
                    f"--capability video --preset {preset_name}")
            return str(d)

        def _make_configs(vc: dict[str, Any]):
            """(model_configs, tokenizer_config)：本地直载优先。"""
            from diffsynth.core import ModelConfig

            if use_local:
                configs = [ModelConfig(path=_files(pattern), **vc)
                           for pattern in conf["files"]]
                return configs, ModelConfig(path=_umt5_dir())

            ensure_diffsynth_base_path()   # 在线下载锚定项目根 models/
            model_id = conf["model_id"]
            configs = [ModelConfig(model_id=model_id,
                                   origin_file_pattern=pattern, **vc)
                       for pattern in conf["files"]]
            tokenizer_config = ModelConfig(
                model_id=_TOKENIZER_MODEL,
                origin_file_pattern="google/umt5-xxl/")
            return configs, tokenizer_config

        def _do_load():
            import torch
            from diffsynth.pipelines.wan_video import WanVideoPipeline

            device = pick_device(self.params.get("device", "auto"), need_gb)
            dtype = torch.bfloat16 if device == "cuda" else torch.float32

            def _from(device_, dtype_):
                model_configs, tokenizer_config = _make_configs(
                    _vram_config(device_, dtype_))
                return WanVideoPipeline.from_pretrained(
                    torch_dtype=dtype_,
                    device=device_,
                    model_configs=model_configs,
                    tokenizer_config=tokenizer_config,
                )

            try:
                return _from(device, dtype)
            except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
                if "out of memory" not in str(exc).lower():
                    raise
                # 低显存回退：CPU 常驻 + 分层 offload（对照官方 low_vram 模式）
                return _from("cpu", torch.float32)

        return self._slot.load(_do_load)

    def unload(self) -> None:
        self._slot.unload()

    # ------------------------------------------------------------------
    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        from PIL import Image

        pipe = self._load()
        conf = self._preset()
        image_path = Path(ctx["image_path"])
        if not image_path.exists():
            raise AdapterError(f"关键帧不存在: {image_path}")
        out = Path(ctx["out_path"])
        prompt = str(ctx.get("prompt", ""))

        fps = int(self.params.get("fps", 15))
        num_frames = int(self.params.get("num_frames", 81))
        # 帧数约束为 4n+1（Wan 官方 examples 用 81/121 等）
        if num_frames < 5:
            num_frames = 5
        num_frames = 4 * (num_frames // 4) + 1

        width = int(ctx.get("width") or 1280)
        height = int(ctx.get("height") or 720)
        # Wan 支持的分辨率需为 16 的倍数（examples: 1248x704 / 960x960）
        width = max(16, width // 16 * 16)
        height = max(16, height // 16 * 16)

        negative = str(self.params.get("negative_prompt", _NEG_WAN))
        seed = int(self.params.get("seed", 0) or 0)

        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "negative_prompt": negative,
            "seed": seed,
            "tiled": True,
            "height": height,
            "width": width,
            "num_frames": num_frames,
        }

        # 首尾帧过渡模式（FLF2V）：下一镜头关键帧作为尾帧 → 平滑转场
        end_image_path = ctx.get("end_image_path")
        if conf["flf2v"] and end_image_path:
            end_path = Path(end_image_path)
            if end_path.exists():
                kwargs["input_image"] = Image.open(image_path).resize(
                    (width, height))
                kwargs["end_image"] = Image.open(end_path).resize(
                    (width, height))
                kwargs["sigma_shift"] = 16      # 官方 FLF2V 示例参数
                kwargs.pop("num_frames", None)  # FLF2V 固定 33 帧
                num_frames = 33
                if progress:
                    progress("FLF2V 首尾帧转场采样中（较慢，属正常）", 40.0)
            else:
                if progress:
                    progress(f"尾帧不存在 {end_path}，回退首帧驱动", 30.0)
                kwargs["input_image"] = Image.open(image_path).resize(
                    (width, height))
        else:
            # 常规 I2V：关键帧为首帧
            if progress:
                progress("DiffSynth Wan 扩散采样中（较慢，属正常）", 40.0)
            kwargs["input_image"] = Image.open(image_path).resize(
                (width, height))

        video = pipe(**kwargs)

        out.parent.mkdir(parents=True, exist_ok=True)
        _save_video(video, str(out), fps=fps, quality=5)

        n = len(video) if hasattr(video, "__len__") else num_frames
        if progress:
            progress("片段完成", 90.0)
        return {"path": str(out), "duration": round(n / fps, 3),
                "motion": "diffsynth_wan",
                "preset": str(self.params.get("model_preset", "")),
                "transition": bool(conf["flf2v"] and end_image_path)}

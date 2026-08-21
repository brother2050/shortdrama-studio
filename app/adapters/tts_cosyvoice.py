"""TTS 后端：cosyvoice（ModelScope iic/CosyVoice2-0.5B，完全离线）。

官方 API（github.com/FunAudioLLM/CosyVoice）：
- ``pip install -e /path/to/CosyVoice`` 后
  ``from cosyvoice.cli.cosyvoice import CosyVoice2``。
- ``inference_sft(text, spk)`` 生成器 yield ``{"tts_speech": Tensor}``；
  采样率取 ``model.sample_rate``（不写死）。

音色映射：voice id（female_warm 等）→ CosyVoice SFT 说话人（中文女/中文男）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               ProgressFn, register_adapter)
from app.adapters.model_paths import ModelPathError, resolve_model_path
from app.adapters.tts_mock import write_wav
from app.vram import ModelSlot, check_vram, pick_device

VRAM_GB = 2.0

#: 平台音色 id → CosyVoice SFT 说话人
VOICE_MAP = {
    "female_warm": "中文女",
    "female_bright": "中文女",
    "male_deep": "中文男",
    "male_warm": "中文男",
    "narrator": "中文女",
}


@register_adapter
class CosyVoiceTTS(AdapterBase):
    spec = AdapterSpec(
        name="cosyvoice", capability="tts", display_name="CosyVoice2（本地）",
        description="阿里 CosyVoice2-0.5B（iic/CosyVoice2-0.5B），Flow Matching 架构，"
                    "多音色中文音质最佳；完全离线。",
        priority=5, requires=["cosyvoice"],
        default_params={"model_dir": "models/tts/cosyvoice2-0.5b",
                        "device": "auto", "voice_map": VOICE_MAP},
        param_docs={
            "model_dir": "本地模型目录：预设名（cosyvoice2-0.5b）或 "
                         "models/tts/<预设名>（相对项目根，也可绝对路径）",
            "device": "推理设备 auto/cpu/cuda",
            "voice_map": "角色音色 id → CosyVoice 说话人映射",
        },
        vram_gb=VRAM_GB, license="Apache-2.0（CosyVoice2）",
    )

    _slot = ModelSlot("cosyvoice2", capability="tts")

    def _load(self, params: dict[str, Any]):
        if self._slot.is_loaded:
            return self._slot.model
        try:
            model_dir = resolve_model_path(
                str(params.get("model_dir") or ""), "tts")
        except ModelPathError as exc:
            raise AdapterError(str(exc)) from exc
        if model_dir is None:
            raise AdapterError(
                "CosyVoice2 需要设置参数 model_dir（本地模型目录）。"
                "离线下载：python scripts/download_models.py --capability tts")
        if not check_vram(VRAM_GB):
            raise AdapterError(f"显存不足：CosyVoice2 需要约 {VRAM_GB}GB，"
                               "请到「系统」页释放显存或改用 CPU。")
        pick_device(str(params.get("device") or "auto"), VRAM_GB)  # 决策+警告
        model_dir = str(model_dir)

        def _do_load():
            from cosyvoice.cli.cosyvoice import CosyVoice2  # 惰性导入（重依赖）

            try:
                return CosyVoice2(model_dir, load_jit=False, load_trt=False)
            except RuntimeError as exc:
                if "out of memory" in str(exc).lower():
                    # OOM 恢复：清缓存后重试（CosyVoice2 内部按可用资源调度）
                    from app.vram import unload_model
                    unload_model(None)
                    return CosyVoice2(model_dir, load_jit=False, load_trt=False)
                raise

        return self._slot.load(_do_load)

    def run(self, ctx: dict[str, Any], progress: ProgressFn | None = None) -> dict[str, Any]:
        text = str(ctx.get("text", "")).strip()
        if not text:
            raise AdapterError("TTS 文本为空")
        out = Path(ctx["out_path"])
        voice = str(ctx.get("voice", "narrator"))
        if progress:
            progress("加载 CosyVoice2", 20.0)
        model = self._load(self.params)
        voice_map = dict(self.params.get("voice_map") or VOICE_MAP)
        target = voice_map.get(voice, "中文女")
        if progress:
            progress(f"合成音色 {voice}→{target}", 60.0)
        chunks = []
        for result in model.inference_sft(text, target, stream=False):
            chunks.append(result["tts_speech"])
        if not chunks:
            raise AdapterError("CosyVoice2 未返回音频")
        import torch

        wav = chunks[0] if len(chunks) == 1 else torch.cat(chunks, dim=1)
        sr = int(getattr(model, "sample_rate", 24000))
        duration = write_wav(out, wav.squeeze(0).tolist(), sr)
        if progress:
            progress("合成完成", 90.0)
        return {"path": str(out), "duration": duration,
                "sample_rate": sr, "voice": voice}

    def unload(self) -> None:
        self._slot.unload()

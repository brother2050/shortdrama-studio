"""引擎 2/4：CosyVoice2 本地推理（共享单例，tts_cosyvoice 与多引擎适配器复用）。

移植要点（官方 API，github.com/FunAudioLLM/CosyVoice）：
- ``pip install -e /path/to/CosyVoice`` 后 ``from cosyvoice.cli.cosyvoice import CosyVoice2``。
- 模型 ModelScope ``iic/CosyVoice2-0.5B``（另需 ``iic/CosyVoice-ttsfrd`` 文本前端）。
- ``inference_sft(text, spk)`` 返回生成器，yield ``{"tts_speech": Tensor}``；
  采样率取 ``model.sample_rate``（不写死）。

共享单例：两个 TTS 后端（cosyvoice 直连 / mosaic 多引擎路由）指向同一份模型，
显存只占一份；``release_all()`` 或适配器 ``unload()`` 时统一释放。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.base import AdapterError
from app.adapters.tts_libs._base import ProgressFn, TTSEngineBase
from app.adapters.tts_mock import write_wav
from app.vram import ModelSlot, check_vram

VRAM_GB = 2.0

#: 平台音色 id → CosyVoice SFT 说话人
VOICE_MAP = {
    "female_warm": "中文女",
    "female_bright": "中文女",
    "male_deep": "中文男",
    "male_warm": "中文男",
    "narrator": "中文女",
}

_slot = ModelSlot("tts_cosyvoice2_shared")


def shared_load(model_dir: str, device_pref: str = "auto") -> Any:
    """加载/复用 CosyVoice2 共享模型实例。"""
    if _slot.is_loaded:
        return _slot.model
    if not check_vram(VRAM_GB):
        raise AdapterError(f"显存不足：CosyVoice2 需要约 {VRAM_GB}GB。"
                           "请到「系统」页释放显存，或改用 HTTP 引擎（GPT-SoVITS/Fish）。")

    def _do_load():
        from cosyvoice.cli.cosyvoice import CosyVoice2  # 惰性导入（重依赖）

        return CosyVoice2(model_dir, load_jit=False, load_trt=False)

    return _slot.load(_do_load)


def shared_synthesize(text: str, voice: str, out_path: Path,
                      params: dict[str, Any],
                      progress: ProgressFn | None = None) -> dict[str, Any]:
    """共享推理：文本 + 音色 → WAV 文件。"""
    model_dir = str(params.get("model_dir") or params.get("cosyvoice_model_dir")
                    or "").strip()
    if not model_dir:
        raise AdapterError(
            "CosyVoice2 需要设置参数 model_dir（本地模型目录）。"
            "离线下载：python scripts/download_models.py --capability tts --local-dir ./models")
    if progress:
        progress("加载 CosyVoice2", 20.0)
    model = shared_load(model_dir, str(params.get("device") or "auto"))
    voice_map = dict(params.get("voice_map") or VOICE_MAP)
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
    duration = write_wav(out_path, wav.squeeze(0).tolist(), sr)
    if progress:
        progress("合成完成", 90.0)
    return {"duration": duration, "sample_rate": sr}


class CosyVoiceEngine(TTSEngineBase):
    name = "cosyvoice"
    label = "CosyVoice2（本地）"
    kind = "local"

    def ready(self, params: dict[str, Any]) -> tuple[bool, str]:
        import importlib.util

        if importlib.util.find_spec("cosyvoice") is None:
            return False, "未安装 CosyVoice（git clone + pip install -e CosyVoice 仓库）"
        model_dir = str(params.get("cosyvoice_model_dir") or params.get("model_dir")
                        or "").strip()
        if not model_dir:
            return False, "已装 cosyvoice 库但未配置 cosyvoice_model_dir（设置页填写模型目录）"
        return True, ""

    def synthesize(self, text: str, voice: str, out_path: Path,
                   params: dict[str, Any],
                   progress: ProgressFn | None = None) -> dict[str, Any]:
        merged = dict(params)
        merged.setdefault("model_dir", params.get("cosyvoice_model_dir"))
        return shared_synthesize(text, voice, out_path, merged, progress)

    def unload(self) -> None:
        _slot.unload()


engine = CosyVoiceEngine()

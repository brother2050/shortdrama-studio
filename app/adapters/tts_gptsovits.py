"""TTS 后端：GPT-SoVITS（本地库，声音克隆）。

官方 API（github.com/RVC-Boss/GPT-SoVITS，fast_inference_ 分支）：
- ``pip install -e /path/to/GPT-SoVITS`` 后
  ``from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config``。
- ``TTS(TTS_Config(configs))`` 加载 GPT + VITS + BERT + CNHuBERT。
- ``tts.run(req)`` 返回生成器，``next()`` 得 ``(sample_rate, np.ndarray)``，
  默认采样率 32000（由 VITS 权重决定）。

依赖：GPT-SoVITS 仓库源码安装（含 torch）；预训练模型 ModelScope ``AIDub/GPT-SoVITS``
放 ``GPT_SoVITS/pretrained_models/``。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               ProgressFn, register_adapter)
from app.adapters.tts_mock import write_wav
from app.vram import ModelSlot, check_vram, pick_device

VRAM_GB = 3.0
DEFAULT_SR = 32000


@register_adapter
class GPTSoVITSAdapter(AdapterBase):
    spec = AdapterSpec(
        name="gpt_sovits", capability="tts", display_name="GPT-SoVITS（本地）",
        description="声音克隆配音（github.com/RVC-Boss/GPT-SoVITS）：用参考音频+其文本"
                    "克隆音色，可按角色映射不同参考音频；少样本克隆效果好。",
        priority=25, requires=["GPT_SoVITS"],
        default_params={
            "config_path": "", "device": "auto", "is_half": "auto",
            "ref_audio": "", "prompt_text": "",
            "voice_refs": {}, "speed": 1.0,
        },
        param_docs={
            "config_path": "GPT-SoVITS configs/tts_infer.yaml 路径（空=官方默认权重）",
            "device": "推理设备 auto/cpu/cuda",
            "is_half": "半精度 auto/true/false（CPU 自动 false）",
            "ref_audio": "全局参考音频 wav 路径（克隆音色来源）",
            "prompt_text": "参考音频里说的文本（音色对齐用，必填）",
            "voice_refs": "音色 id → 参考音频路径映射（按角色克隆，优先于 ref_audio）",
            "speed": "语速（默认 1.0）",
        },
        vram_gb=VRAM_GB, license="MIT（GPT-SoVITS）",
    )

    _slot = ModelSlot("gpt_sovits", capability="tts")

    def _load(self, params: dict[str, Any]):
        if self._slot.is_loaded:
            return self._slot.model
        if not check_vram(VRAM_GB):
            raise AdapterError(f"显存不足：GPT-SoVITS 需要约 {VRAM_GB}GB，"
                               "请到「系统」页释放显存或改用 CPU。")
        device = pick_device(str(params.get("device") or "auto"), VRAM_GB)
        half_pref = str(params.get("is_half") or "auto")
        is_half = (device == "cuda") if half_pref == "auto" else half_pref == "true"
        config_path = str(params.get("config_path") or "").strip()

        def _do_load():
            from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config  # 惰性导入

            cfg = {"device": device, "is_half": is_half}
            if config_path:
                cfg = config_path
            try:
                return TTS(TTS_Config(cfg) if isinstance(cfg, dict) else cfg)
            except RuntimeError as exc:
                if "out of memory" in str(exc).lower() and device == "cuda":
                    # OOM 恢复：CPU + 全精度重载
                    fallback = TTS_Config({"device": "cpu", "is_half": False})
                    return TTS(fallback)
                raise

        return self._slot.load(_do_load)

    def run(self, ctx: dict[str, Any], progress: ProgressFn | None = None) -> dict[str, Any]:
        text = str(ctx.get("text", "")).strip()
        if not text:
            raise AdapterError("TTS 文本为空")
        out = Path(ctx["out_path"])
        voice = str(ctx.get("voice", "narrator"))
        refs = dict(self.params.get("voice_refs") or {})
        ref_audio = str(refs.get(voice)
                        or self.params.get("ref_audio") or "").strip()
        if not ref_audio:
            raise AdapterError(
                "GPT-SoVITS 需要参考音频：设置参数 ref_audio（全局 wav），"
                "或 voice_refs 按角色映射参考音频（克隆音色）。")
        if not Path(ref_audio).exists():
            raise AdapterError(f"参考音频不存在: {ref_audio}")
        prompt_text = str(self.params.get("prompt_text") or "").strip()
        if not prompt_text:
            raise AdapterError("GPT-SoVITS 需要参数 prompt_text（参考音频里说的文本）")
        if progress:
            progress("加载 GPT-SoVITS", 20.0)
        tts = self._load(self.params)
        if progress:
            progress(f"克隆音色 {voice}", 60.0)
        req = {
            "text": text, "text_lang": "zh",
            "ref_audio_path": ref_audio,
            "prompt_text": prompt_text, "prompt_lang": "zh",
            "text_split_method": "cut5", "batch_size": 1,
            "speed_factor": float(self.params.get("speed") or 1.0),
            "media_type": "wav", "streaming_mode": False,
        }
        sr, audio = next(tts.run(req))
        sr = int(sr) or DEFAULT_SR
        duration = write_wav(out, list(audio), sr)
        if progress:
            progress("合成完成", 90.0)
        return {"path": str(out), "duration": duration,
                "sample_rate": sr, "voice": voice}

    def unload(self) -> None:
        self._slot.unload()

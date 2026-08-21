"""TTS 后端：ChatTTS（本地库，对话感中文配音）。

官方 API（github.com/2noise/ChatTTS）：
- ``ChatTTS.Chat().load(source="custom", custom_path=..., device=...)``；
  未指定目录时用默认缓存（首次联网下载，之后离线）。
- ``infer()`` 返回 ``list[np.ndarray]``，采样率 24000 Hz。

依赖：``pip install ChatTTS``（模型离线：
``python scripts/download_models.py --capability tts --preset chattts``）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               ProgressFn, register_adapter)
from app.adapters.tts_mock import write_wav
from app.vram import ModelSlot, check_vram, pick_device

SAMPLE_RATE = 24000
VRAM_GB = 1.5

#: 音色 id → 随机种子（固定种子保证同一角色跨集音色一致）
VOICE_SEEDS = {
    "female_warm": 11, "female_bright": 23,
    "male_deep": 37, "male_warm": 53, "narrator": 71,
}
DEFAULT_SEED = 42


@register_adapter
class ChatTTSAdapter(AdapterBase):
    spec = AdapterSpec(
        name="chattts", capability="tts", display_name="ChatTTS（本地）",
        description="对话感中文配音（github.com/2noise/ChatTTS），角色音色固定种子，"
                    "同一角色跨集音色一致；CPU 可跑（慢），GPU 更快。",
        priority=20, requires=["ChatTTS"],
        default_params={"model_dir": "", "device": "auto"},
        param_docs={
            "model_dir": "ChatTTS 本地模型目录（空=默认缓存；离线填 download_models.py 下载路径）",
            "device": "推理设备 auto/cpu/cuda",
        },
        vram_gb=VRAM_GB, license="CC-BY-NC-4.0（ChatTTS）",
    )

    _slot = ModelSlot("chattts", capability="tts")
    _spk_cache: dict[str, str] = {}

    def _load(self, params: dict[str, Any]):
        if self._slot.is_loaded:
            return self._slot.model
        if not check_vram(VRAM_GB):
            raise AdapterError(f"显存不足：ChatTTS 需要约 {VRAM_GB}GB，"
                               "请到「系统」页释放显存或改用 CPU。")
        model_dir = str(params.get("model_dir") or "").strip()
        device = pick_device(str(params.get("device") or "auto"), VRAM_GB)

        def _do_load():
            import torch
            from ChatTTS import Chat  # 惰性导入（重依赖）

            chat = Chat()
            kwargs: dict[str, Any] = {"compile": False,
                                      "device": torch.device(device)}
            if model_dir:
                kwargs.update(source="custom", custom_path=model_dir)
            try:
                ok = chat.load(**kwargs)
            except RuntimeError as exc:
                if "out of memory" in str(exc).lower() and device == "cuda":
                    # OOM 恢复：回退 CPU 重载
                    kwargs["device"] = torch.device("cpu")
                    ok = chat.load(**kwargs)
                else:
                    raise
            if ok is False:
                raise AdapterError(
                    f"ChatTTS 模型加载失败（model_dir={model_dir or '默认缓存'}）")
            return chat

        return self._slot.load(_do_load)

    def run(self, ctx: dict[str, Any], progress: ProgressFn | None = None) -> dict[str, Any]:
        text = str(ctx.get("text", "")).strip()
        if not text:
            raise AdapterError("TTS 文本为空")
        out = Path(ctx["out_path"])
        voice = str(ctx.get("voice", "narrator"))
        if progress:
            progress("加载 ChatTTS", 20.0)
        chat = self._load(self.params)
        from ChatTTS import Chat

        if progress:
            progress(f"合成音色 {voice}", 60.0)
        spk = self._speaker(chat, voice)
        import torch

        torch.manual_seed(VOICE_SEEDS.get(voice, DEFAULT_SEED))
        wavs = chat.infer(
            [text],
            params_refine_text=Chat.RefineTextParams(prompt="[oral_2][laugh_0][break_6]"),
            params_infer_code=Chat.InferCodeParams(
                spk_emb=spk, temperature=0.3, top_P=0.7, top_K=20))
        if not wavs:
            raise AdapterError("ChatTTS 未返回音频")
        wav = wavs[0]
        samples = wav.squeeze().tolist() if hasattr(wav, "squeeze") else list(wav)
        duration = write_wav(out, samples, SAMPLE_RATE)
        if progress:
            progress("合成完成", 90.0)
        return {"path": str(out), "duration": duration,
                "sample_rate": SAMPLE_RATE, "voice": voice}

    def _speaker(self, chat, voice: str) -> str:
        """固定种子取 speaker embedding（同角色跨集一致）。"""
        cached = self._spk_cache.get(voice)
        if cached:
            return cached
        import torch

        torch.manual_seed(VOICE_SEEDS.get(voice, DEFAULT_SEED))
        spk = chat.sample_random_speaker()
        self._spk_cache[voice] = spk
        return spk

    def unload(self) -> None:
        self._slot.unload()
        self._spk_cache.clear()

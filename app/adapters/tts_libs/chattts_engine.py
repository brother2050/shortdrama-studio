"""引擎 1/4：ChatTTS 本地推理（对话感中文配音，pip install ChatTTS）。

移植要点（官方 API，github.com/2noise/ChatTTS）：
- ``ChatTTS.Chat().load(source="custom", custom_path=..., device=...)`` 支持离线模型目录；
  未指定目录时 ``load(compile=False)`` 使用默认缓存（首次联网下载，之后离线）。
- ``infer()`` 返回 ``list[np.ndarray]``，采样率固定 24000 Hz。
- 音色一致性（连续短剧关键需求）：同一角色固定随机种子 → 同一 speaker embedding，
  ``VOICE_SEEDS`` 为平台五个音色 id 预置种子。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.base import AdapterError
from app.adapters.tts_libs._base import ProgressFn, TTSEngineBase
from app.adapters.tts_mock import write_wav
from app.vram import ModelSlot, check_vram, pick_device

SAMPLE_RATE = 24000
VRAM_GB = 1.5

#: 音色 id → 随机种子（固定种子保证同一角色音色一致）
VOICE_SEEDS = {
    "female_warm": 11, "female_bright": 23,
    "male_deep": 37, "male_warm": 53, "narrator": 71,
}
DEFAULT_SEED = 42


class ChatTTSEngine(TTSEngineBase):
    name = "chattts"
    label = "ChatTTS（本地）"
    kind = "local"

    _slot = ModelSlot("tts_chattts")
    _spk_cache: dict[str, str] = {}

    def ready(self, params: dict[str, Any]) -> tuple[bool, str]:
        import importlib.util

        if importlib.util.find_spec("ChatTTS") is None:
            return False, "未安装 ChatTTS（pip install ChatTTS，模型可 scripts/download_models.py --capability tts --preset chattts 离线下载）"
        return True, ""

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

    def _load(self, params: dict[str, Any]):
        if self._slot.is_loaded:
            return self._slot.model
        if not check_vram(VRAM_GB):
            raise AdapterError(f"显存不足：ChatTTS 需要约 {VRAM_GB}GB。"
                               "请到「系统」页释放显存，或改用 HTTP 引擎（GPT-SoVITS/Fish）。")
        model_dir = str(params.get("chattts_model_dir") or "").strip()
        device = pick_device(str(params.get("device") or "auto"), VRAM_GB)

        def _do_load():
            import torch
            from ChatTTS import Chat  # 惰性导入（重依赖）

            chat = Chat()
            kwargs: dict[str, Any] = {"compile": False, "device": torch.device(device)}
            if model_dir:
                kwargs.update(source="custom", custom_path=model_dir)
            ok = chat.load(**kwargs)
            if ok is False:
                raise AdapterError(
                    f"ChatTTS 模型加载失败（model_dir={model_dir or '默认缓存'}）。"
                    "请检查模型目录，或执行 scripts/download_models.py --capability tts --preset chattts")
            return chat

        return self._slot.load(_do_load)

    def synthesize(self, text: str, voice: str, out_path: Path,
                   params: dict[str, Any],
                   progress: ProgressFn | None = None) -> dict[str, Any]:
        if progress:
            progress("加载 ChatTTS", 20.0)
        try:
            chat = self._load(params)
        except AdapterError:
            raise
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower():
                # OOM 恢复：释放后改用 CPU 重载
                self.unload()
                params = dict(params, device="cpu")
                chat = self._load(params)
            else:
                raise
        if progress:
            progress(f"合成音色 {voice}", 60.0)
        import torch
        from ChatTTS import Chat

        spk = self._speaker(chat, voice)
        infer_code = Chat.InferCodeParams(
            spk_emb=spk, temperature=0.3, top_P=0.7, top_K=20)
        refine = Chat.RefineTextParams(prompt="[oral_2][laugh_0][break_6]")
        torch.manual_seed(VOICE_SEEDS.get(voice, DEFAULT_SEED))
        wavs = chat.infer([text], params_refine_text=refine,
                          params_infer_code=infer_code)
        if not wavs:
            raise AdapterError("ChatTTS 未返回音频")
        wav = wavs[0]
        samples = wav.squeeze().tolist() if hasattr(wav, "squeeze") else list(wav)
        duration = write_wav(out_path, samples, SAMPLE_RATE)
        if progress:
            progress("合成完成", 90.0)
        return {"duration": duration, "sample_rate": SAMPLE_RATE}

    def unload(self) -> None:
        self._slot.unload()
        self._spk_cache.clear()


engine = ChatTTSEngine()

"""TTS 后端：Fish Speech（本地库，多语言配音/克隆）。

官方 API（github.com/fishaudio/fish-speech，main 分支 2.0）：
- ``pip install -e /path/to/fish-speech`` 后三件套本地加载：
  ``launch_thread_safe_queue``（LLM）+ ``load_model``（DAC codec）+
  ``TTSInferenceEngine``。
- ``engine.inference(ServeTTSRequest)`` 生成器，``result.code == "final"`` 时
  ``result.audio = (sample_rate, np.ndarray)``（采样率取自 codec 模型）。

依赖：fish-speech 仓库源码安装（含 torch/hydra/descript-audio-codec）；
模型 ModelScope ``fishaudio/fish-speech-1.5``（新体系 ``fishaudio/s2-pro``）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               ProgressFn, register_adapter)
from app.adapters.model_paths import ModelPathError, resolve_model_path
from app.adapters.tts_mock import write_wav
from app.vram import ModelSlot, check_vram, pick_device

VRAM_GB = 4.0


@register_adapter
class FishSpeechAdapter(AdapterBase):
    spec = AdapterSpec(
        name="fish_speech", capability="tts", display_name="Fish Speech（本地）",
        description="多语言配音与音色克隆（github.com/fishaudio/fish-speech）："
                    "LLM+Codec 架构，支持参考音频克隆；需 fish-speech 仓库源码安装。",
        priority=30, requires=["fish_speech"],
        default_params={
            "checkpoint_dir": "models/tts/fish-speech-1.5", "device": "auto",
            "ref_audio": "", "prompt_text": "", "voice_refs": {},
        },
        param_docs={
            "checkpoint_dir": "模型目录（含 codec.pth）：预设名（fish-speech-1.5）或 "
                              "models/tts/<预设名>（相对项目根，也可绝对路径）",
            "device": "推理设备 auto/cpu/cuda",
            "ref_audio": "全局参考音频 wav 路径（克隆音色来源）",
            "prompt_text": "参考音频里说的文本（克隆音色对齐用）",
            "voice_refs": "音色 id → 参考音频路径映射（按角色克隆，优先于 ref_audio）",
        },
        vram_gb=VRAM_GB, license="CC-BY-NC-SA-4.0（Fish Speech 权重）",
    )

    _slot = ModelSlot("fish_speech", capability="tts")

    def _load(self, params: dict[str, Any]):
        if self._slot.is_loaded:
            return self._slot.model
        try:
            ckpt_path = resolve_model_path(
                str(params.get("checkpoint_dir") or ""), "tts")
        except ModelPathError as exc:
            raise AdapterError(str(exc)) from exc
        if ckpt_path is None:
            raise AdapterError(
                "Fish Speech 需要参数 checkpoint_dir（模型目录，含 codec.pth）。"
                "离线下载：python scripts/download_models.py --capability tts --preset fish-speech-1.5")
        ckpt = str(ckpt_path)
        if not check_vram(VRAM_GB):
            raise AdapterError(f"显存不足：Fish Speech 需要约 {VRAM_GB}GB，"
                               "请到「系统」页释放显存或改用 CPU。")
        device = pick_device(str(params.get("device") or "auto"), VRAM_GB)

        def _do_load():
            import torch
            from fish_speech.inference_engine import TTSInferenceEngine  # 惰性导入
            from fish_speech.models.dac.inference import load_model as load_codec
            from fish_speech.models.text2semantic.inference import \
                launch_thread_safe_queue

            precision = torch.bfloat16 if device == "cuda" else torch.float32
            ckpt_path = Path(ckpt)
            try:
                llama_queue = launch_thread_safe_queue(
                    checkpoint_path=ckpt_path, device=device,
                    precision=precision, compile=False)
                codec = load_codec(config_name="modded_dac_vq",
                                   checkpoint_path=ckpt_path / "codec.pth",
                                   device=device)
            except RuntimeError as exc:
                if "out of memory" in str(exc).lower() and device == "cuda":
                    # OOM 恢复：CPU + 全精度重载
                    llama_queue = launch_thread_safe_queue(
                        checkpoint_path=ckpt_path, device="cpu",
                        precision=torch.float32, compile=False)
                    codec = load_codec(config_name="modded_dac_vq",
                                       checkpoint_path=ckpt_path / "codec.pth",
                                       device="cpu")
                else:
                    raise
            return TTSInferenceEngine(llama_queue=llama_queue,
                                      decoder_model=codec,
                                      compile=False, precision=precision)

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
        if ref_audio and not Path(ref_audio).exists():
            raise AdapterError(f"参考音频不存在: {ref_audio}")
        if progress:
            progress("加载 Fish Speech", 20.0)
        engine = self._load(self.params)
        if progress:
            progress(f"合成音色 {voice}", 60.0)
        from fish_speech.utils.schema import ServeReferenceAudio, ServeTTSRequest

        references = []
        if ref_audio:
            prompt_text = str(self.params.get("prompt_text") or "")
            references.append(ServeReferenceAudio(
                audio=Path(ref_audio).read_bytes(), text=prompt_text))
        req = ServeTTSRequest(
            text=text, references=references, format="wav", streaming=False,
            max_new_tokens=1024, chunk_length=300,
            top_p=0.8, temperature=0.8, repetition_penalty=1.1)
        sr, audio = None, None
        for result in engine.inference(req):
            if result.code == "final" and result.audio:
                sr, audio = result.audio
                break
            if result.code == "error" and result.error:
                raise AdapterError(f"Fish Speech 推理失败: {result.error}")
        if audio is None:
            raise AdapterError("Fish Speech 未返回音频")
        duration = write_wav(out, list(audio), int(sr or 24000))
        if progress:
            progress("合成完成", 90.0)
        return {"path": str(out), "duration": duration,
                "sample_rate": int(sr or 24000), "voice": voice}

    def unload(self) -> None:
        self._slot.unload()

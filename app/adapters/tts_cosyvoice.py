"""TTS 后端 3/3：cosyvoice（ModelScope iic/CosyVoice2-0.5B，完全离线）。

离线要点：
1. 联网时下载模型：
   ``python scripts/download_models.py --capability tts --local-dir ./models``
   （会同时下载 iic/CosyVoice2-0.5B 与 iic/CosyVoice-ttsfrd）
2. 安装 CosyVoice 仓库：``pip install -e /path/to/CosyVoice``
3. 本后端 ``params.model_dir`` 指向本地模型目录。

音色映射：voice id（female_warm 等）→ CosyVoice 内置音色（如中文女声），
保留 speaker 映射表，便于按角色换音色。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)
from app.adapters.tts_mock import write_wav

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
        description="阿里 CosyVoice2-0.5B（ModelScope iic/CosyVoice2-0.5B），"
                    "Flow Matching 架构，音质最佳；支持零样本克隆（需配置参考音频）。",
        priority=5, requires=["cosyvoice"],
        default_params={"model_dir": "", "device": "auto", "voice_map": VOICE_MAP},
        param_docs={
            "model_dir": "本地模型目录（scripts/download_models.py 下载后的路径）",
            "device": "推理设备 auto/cpu/cuda",
            "voice_map": "角色音色 id → CosyVoice 音色名映射",
        },
        vram_gb=2.0, license="Apache-2.0（CosyVoice2）",
    )

    _model = None

    def _load(self):
        model_dir = str(self.params.get("model_dir") or "").strip()
        if not model_dir:
            raise AdapterError(
                "cosyvoice 需要设置参数 model_dir（本地模型目录）。"
                "离线下载：python scripts/download_models.py --capability tts --local-dir ./models")
        if self._model is not None:
            return self._model
        import torch
        from cosyvoice.cli.cosyvoice import CosyVoice2
        device = self.params.get("device", "auto")
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model = CosyVoice2(model_dir, load_jit=False, load_trt=False)
        return self._model

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        text = str(ctx.get("text", "")).strip()
        if not text:
            raise AdapterError("TTS 文本为空")
        out = Path(ctx["out_path"])
        if progress:
            progress("加载 CosyVoice2", 20.0)
        model = self._load()
        voice = str(ctx.get("voice", "narrator"))
        voice_map = dict(self.params.get("voice_map") or VOICE_MAP)
        target = voice_map.get(voice, "中文女")
        if progress:
            progress(f"合成音色 {voice}→{target}", 60.0)
        # CosyVoice2 流式接口：取全部 chunk 拼接
        chunks = []
        for result in model.inference_sft(text, target, stream=False):
            chunks.append(result["tts_speech"])
        import torch
        wav = chunks[0] if len(chunks) == 1 else torch.cat(chunks, dim=1)
        duration = write_wav(out, wav.squeeze(0).tolist())
        if progress:
            progress("合成完成", 90.0)
        return {"path": str(out), "duration": duration,
                "sample_rate": 24000, "voice": voice}

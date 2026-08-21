"""TTS 后端 3/3：cosyvoice（ModelScope iic/CosyVoice2-0.5B，完全离线）。

推理逻辑抽取到 ``app/adapters/tts_libs/cosyvoice_engine.py`` 共享单例
（多引擎 mosaic 适配器复用同一份模型，显存只占一份），本适配器为薄封装。

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

from pathlib import Path
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)
from app.adapters.tts_libs import cosyvoice_engine
from app.adapters.tts_libs._base import ProgressFn
from app.adapters.tts_libs.cosyvoice_engine import VOICE_MAP


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

    def run(self, ctx: dict[str, Any], progress: ProgressFn | None = None) -> dict[str, Any]:
        text = str(ctx.get("text", "")).strip()
        if not text:
            raise AdapterError("TTS 文本为空")
        out = Path(ctx["out_path"])
        voice = str(ctx.get("voice", "narrator"))
        result = cosyvoice_engine.shared_synthesize(
            text, voice, out, self.params, progress)
        return {"path": str(out), "duration": result["duration"],
                "sample_rate": result["sample_rate"], "voice": voice}

    def unload(self) -> None:
        cosyvoice_engine.engine.unload()

"""TTS 后端：mosaic 多引擎路由（内置移植，无外部依赖）。

设计源自参考项目 brother2050/mosaic 的 TTS 节点（ChatTTS / Fish Speech /
GPT-SoVITS / CosyVoice 四后端路由）。本适配器已把四后端的核心调用代码
内置移植到 ``app/adapters/tts_libs/``，**不再依赖 mosaic 包**
（无需 ``pip install -e /path/to/mosaic``），只保留各引擎自身的最小依赖：

- ``cosyvoice``：``pip install -e /path/to/CosyVoice`` + 本地模型目录（离线）
- ``chattts``：``pip install ChatTTS``（模型可离线下载到本地目录）
- ``gpt_sovits``：GPT-SoVITS 仓库 ``python api_v2.py -p 9880``（HTTP，零依赖）
- ``fish_speech``：fish-speech 仓库 ``python tools/api_server.py``（HTTP，零依赖）

后端名保留 ``mosaic`` 以兼容既有配置；``engine`` 参数选择引擎（auto 自动路由）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.adapters import tts_libs
from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)
from app.adapters.tts_libs._base import ProgressFn


@register_adapter
class MosaicTTS(AdapterBase):
    spec = AdapterSpec(
        name="mosaic", capability="tts", display_name="多引擎 TTS（四后端内置）",
        description="内置 ChatTTS/CosyVoice2/GPT-SoVITS/Fish Speech 四引擎路由"
                    "（移植自 mosaic TTS 节点，无外部依赖）：engine=auto 按就绪状态"
                    "自动选择（本地库优先，离线友好），也可显式指定引擎。",
        priority=15, requires=[],
        default_params={
            "engine": "auto", "device": "auto",
            "chattts_model_dir": "",
            "cosyvoice_model_dir": "",
            "sovits_url": "http://127.0.0.1:9880",
            "sovits_ref_audio": "", "sovits_prompt_text": "",
            "sovits_voice_refs": {},
            "fish_url": "http://127.0.0.1:8080",
            "fish_reference_id": "", "fish_api_key": "",
            "fish_voice_refs": {},
        },
        param_docs={
            "engine": "引擎选择：auto（自动路由）/ cosyvoice / chattts / gpt_sovits / fish_speech",
            "device": "本地引擎推理设备 auto/cpu/cuda",
            "chattts_model_dir": "ChatTTS 本地模型目录（空=默认缓存，离线填下载路径）",
            "cosyvoice_model_dir": "CosyVoice2 模型目录（download_models.py --capability tts 下载路径）",
            "sovits_url": "GPT-SoVITS api_v2 服务地址（默认 http://127.0.0.1:9880）",
            "sovits_ref_audio": "GPT-SoVITS 参考音频路径（全局克隆音色）",
            "sovits_prompt_text": "参考音频里说的文本（音色对齐用，必填）",
            "sovits_voice_refs": "音色 id → 参考音频路径映射（按角色克隆）",
            "fish_url": "Fish Speech 服务地址（默认 http://127.0.0.1:8080）",
            "fish_reference_id": "Fish Speech 预录音色 id（全局）",
            "fish_api_key": "Fish Speech Bearer 认证密钥（服务未启用则留空）",
            "fish_voice_refs": "音色 id → reference_id 映射（按角色克隆）",
        },
        vram_gb=2.0, license="按所选引擎模型许可（ChatTTS 为 CC-BY-NC-4.0）",
    )

    @classmethod
    def _extra_available(cls) -> bool:
        return tts_libs.any_engine_ready(cls.spec.default_params)

    @classmethod
    def _unavailable_reason(cls) -> str:
        states = tts_libs.engine_status(cls.spec.default_params)
        parts = [f"{v['label']}: {v['reason']}" for v in states.values()
                 if not v["ready"]]
        return "四引擎均未就绪 —— " + "；".join(parts)

    def run(self, ctx: dict[str, Any], progress: ProgressFn | None = None) -> dict[str, Any]:
        text = str(ctx.get("text", "")).strip()
        if not text:
            raise AdapterError("TTS 文本为空")
        out = Path(ctx["out_path"])
        voice = str(ctx.get("voice", "narrator"))
        if progress:
            progress("选择 TTS 引擎", 10.0)
        name, eng = tts_libs.pick_engine(self.params)
        if progress:
            progress(f"引擎 {name}（{eng.label}）", 30.0)
        result = eng.synthesize(text, voice, out, self.params, progress)
        return {"path": str(out), "duration": result["duration"],
                "sample_rate": result["sample_rate"], "voice": voice,
                "engine": name}

    def unload(self) -> None:
        tts_libs.unload_all_engines()

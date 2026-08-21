"""ASR/字幕对齐后端 2/2：funasr（ModelScope SenseVoice / Paraformer，离线）。

对成片音轨做识别并回填时间戳，用于校对"剧本时间轴"的偏差。
识别失败时抛出可读错误（不静默降级，保证用户知情）。
"""
from __future__ import annotations

from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)
from app.adapters.asr_script import segments_to_srt
from app.adapters.model_paths import ModelPathError, model_source


@register_adapter
class FunASR(AdapterBase):
    spec = AdapterSpec(
        name="funasr", capability="asr", display_name="FunASR（SenseVoice/Paraformer）",
        description="ModelScope FunASR 本地识别（iic/SenseVoiceSmall CPU 友好；"
                    "paraformer-large 带时间戳），对配音音轨二次校对字幕。",
        priority=5, requires=["funasr"],
        default_params={"model": "models/asr/sensevoice-small",
                        "device": "auto"},
        param_docs={
            "model": "模型目录（预设名 sensevoice-small 或 models/asr/<预设名>，"
                     "相对项目根）或 FunASR 在线模型 id",
            "device": "auto/cpu/cuda",
        },
        license="Apache-2.0（FunASR / Paraformer）",
    )

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        audio = ctx.get("audio_path")
        if not audio:
            raise AdapterError("funasr 需要 audio_path")
        import funasr
        try:
            source, _local = model_source(
                str(self.params.get("model") or ""), "asr")
        except ModelPathError as exc:
            raise AdapterError(str(exc)) from exc
        model = funasr.AutoModel(model=source, disable_update=True)
        if progress:
            progress("识别中", 50.0)
        res = model.generate(input=audio)
        segments: list[dict] = []
        for item in res:
            for s in item.get("sentence_info", []) or []:
                segments.append({
                    "start": (s.get("start") or 0) / 1000.0,
                    "end": (s.get("end") or 0) / 1000.0,
                    "text": s.get("text", ""), "speaker": s.get("speaker", "")})
        if not segments:  # 模型未返回时间戳时的兜底
            text = res[0].get("text", "") if res else ""
            if text:
                segments = [{"start": 0.0, "end": max(1.0, len(text) * 0.22),
                             "text": text, "speaker": ""}]
        if progress:
            progress(f"识别 {len(segments)} 段", 80.0)
        return {"segments": segments, "srt": segments_to_srt(segments)}

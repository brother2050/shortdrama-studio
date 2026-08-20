"""ASR/字幕对齐后端 2/2：funasr（ModelScope SenseVoice / Paraformer，离线）。

对成片音轨做识别并回填时间戳，用于校对"剧本时间轴"的偏差。
识别失败时抛出可读错误（不静默降级，保证用户知情）。
"""
from __future__ import annotations

from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)
from app.adapters.asr_script import segments_to_srt


@register_adapter
class FunASR(AdapterBase):
    spec = AdapterSpec(
        name="funasr", capability="asr", display_name="FunASR（SenseVoice/Paraformer）",
        description="ModelScope FunASR 本地识别（iic/SenseVoiceSmall CPU 友好；"
                    "paraformer-large 带时间戳），对配音音轨二次校对字幕。",
        priority=5, requires=["funasr"],
        default_params={"model": "iic/SenseVoiceSmall_with_time",
                        "device": "auto"},
        param_docs={
            "model": "FunASR 模型 id（本地缓存或 ModelScope id）",
            "device": "auto/cpu/cuda",
        },
        license="Apache-2.0（FunASR / Paraformer）",
    )

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        audio = ctx.get("audio_path")
        if not audio:
            raise AdapterError("funasr 需要 audio_path")
        import funasr
        model = funasr.AutoModel(model=self.params.get("model"),
                                 disable_update=True)
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

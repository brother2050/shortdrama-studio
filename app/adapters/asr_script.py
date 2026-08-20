"""ASR/字幕对齐后端 1/2：script（默认，零依赖）。

直接用剧本对白 + TTS 实测时长计算字幕时间轴（顺序累计），
零漂移、零模型。适合"剧本即字幕"的短剧场景。
"""
from __future__ import annotations

from typing import Any

from app.adapters.base import AdapterBase, AdapterSpec, register_adapter


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def segments_to_srt(segments: list[dict]) -> str:
    """[{start,end,text,speaker}] → SRT 文本（可独立测试）。"""
    blocks = []
    for i, seg in enumerate(segments, 1):
        speaker = seg.get("speaker", "")
        text = seg["text"]
        line = f"{speaker}：{text}" if speaker and speaker != "旁白" else text
        blocks.append(f"{i}\n{_ts(seg['start'])} --> {_ts(seg['end'])}\n{line}\n")
    return "\n".join(blocks)


@register_adapter
class ScriptASR(AdapterBase):
    spec = AdapterSpec(
        name="script", capability="asr", display_name="剧本时间轴（零依赖）",
        description="用剧本对白 + 配音实测时长直接生成字幕时间轴，零漂移、零模型（默认）。",
        priority=10, requires=[], default_params={}, param_docs={},
        license="MIT（本项目内置）",
    )

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        segs_in = ctx.get("segments", [])
        out: list[dict] = []
        t = 0.0
        for seg in segs_in:
            dur = float(seg.get("duration") or 2.0)
            out.append({"start": round(t, 3), "end": round(t + dur, 3),
                        "text": seg.get("text", ""), "speaker": seg.get("speaker", "")})
            t += dur
        if progress:
            progress(f"对齐 {len(out)} 条字幕", 80.0)
        return {"segments": out}

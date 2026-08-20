"""TTS 后端 1/3：mock（内置，零依赖，默认兜底）。

按音色生成可听的和谐音波（基频 + 泛音 + 包络 + 轻微颤音），
时长按文本字数估算（中文 ≈ 0.22 秒/字），写入标准 WAV（stdlib wave）。
产物可被 ffmpeg 正常识别与合成，保证无模型环境全链路可用。
"""
from __future__ import annotations

import hashlib
import math
import struct
import wave
from pathlib import Path
from typing import Any

from app.adapters.base import AdapterBase, AdapterSpec, register_adapter

SAMPLE_RATE = 24000

#: 角色音色表（voice id → 基频/明亮度），音色分配见 app/continuity.assign_voices
VOICES = {
    "female_warm":   {"base": 235.0, "bright": 0.25, "label": "暖女声"},
    "female_bright": {"base": 285.0, "bright": 0.40, "label": "亮女声"},
    "male_deep":     {"base": 118.0, "bright": 0.15, "label": "沉男声"},
    "male_warm":     {"base": 142.0, "bright": 0.22, "label": "温男声"},
    "narrator":      {"base": 165.0, "bright": 0.18, "label": "旁白"},
}

_SECONDS_PER_CHAR = 0.22


def estimate_duration(text: str) -> float:
    """按字数估算配音时长（与 mock TTS 实际时长一致，供时长规划）。"""
    n = max(1, len([c for c in text if not c.isspace()]))
    return round(min(60.0, max(0.8, n * _SECONDS_PER_CHAR)), 3)


def synthesize_pcm(text: str, voice: str, sample_rate: int = SAMPLE_RATE) -> list[float]:
    """确定性音波合成（可测试）。"""
    conf = VOICES.get(voice, VOICES["narrator"])
    seed = int(hashlib.md5(f"{voice}:{text}".encode("utf-8")).hexdigest()[:8], 16)
    dur = estimate_duration(text)
    total = int(dur * sample_rate)
    base = conf["base"] * (1.0 + ((seed % 7) - 3) * 0.01)
    bright = conf["bright"]
    samples: list[float] = []
    for i in range(total):
        t = i / sample_rate
        env = min(1.0, t / 0.05, (dur - t) / 0.12)          # 起音/收尾包络
        vib = 1.0 + 0.004 * math.sin(2 * math.pi * 5.2 * t)  # 轻微颤音
        f = base * vib
        s = (math.sin(2 * math.pi * f * t)
             + bright * math.sin(2 * math.pi * 2 * f * t)
             + 0.12 * math.sin(2 * math.pi * 3 * f * t))
        # 按音节节律做轻微幅度起伏（模拟语句重音）
        syll = 0.75 + 0.25 * math.sin(2 * math.pi * 3.5 * t + seed)
        samples.append(max(-0.95, min(0.95, 0.32 * env * s * syll)))
    return samples


def write_wav(path: str | Path, samples: list[float],
              sample_rate: int = SAMPLE_RATE) -> float:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = b"".join(struct.pack("<h", int(s * 32767)) for s in samples)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(frames)
    return round(len(samples) / sample_rate, 3)


@register_adapter
class MockTTS(AdapterBase):
    spec = AdapterSpec(
        name="mock", capability="tts", display_name="内置合成器（离线）",
        description="零依赖音波合成（按音色区分频率），时长按字数估算。"
                    "产物为标准 WAV，供全链路演示与测试。",
        priority=10, requires=[],
        default_params={"sample_rate": SAMPLE_RATE},
        param_docs={"sample_rate": "采样率（默认 24000）"},
        license="MIT（本项目内置）",
    )

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        text = str(ctx.get("text", "")).strip() or "（静音）"
        voice = str(ctx.get("voice", "narrator"))
        out = Path(ctx["out_path"])
        sr = int(self.params.get("sample_rate", SAMPLE_RATE))
        if progress:
            progress(f"合成音色 {voice}", 50.0)
        samples = synthesize_pcm(text, voice, sr)
        duration = write_wav(out, samples, sr)
        if progress:
            progress("合成完成", 90.0)
        return {"path": str(out), "duration": duration, "sample_rate": sr,
                "voice": voice, "estimated": False}

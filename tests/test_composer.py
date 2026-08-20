"""合成器测试：音频填充 / 精确时长 / 字幕轨 / ffmpeg 缺失降级提示。"""
from __future__ import annotations

import json
import subprocess

import pytest

from app import composer
from app.composer import (ComposerError, compose_episode, pad_audio,
                          probe_duration)


def _make_wav(path, seconds: float, sr: int = 24000):
    import math
    import struct
    import wave

    n = int(seconds * sr)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(struct.pack("<" + "h" * n,
                                  *(int(8000 * math.sin(i / 20)) for i in range(n))))


def _make_png(path, size=64):
    import struct
    import zlib

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes([64, 90, 128] * size) for _ in range(size))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@pytest.fixture()
def one_shot(tmp_path):
    """一个镜头的素材（图→视频片段 + 配音 + 时长）。"""
    _make_png(tmp_path / "kf.png")
    ff = composer.which_ffmpeg()
    clip = tmp_path / "clip.mp4"
    subprocess.run([ff, "-y", "-loop", "1", "-i", str(tmp_path / "kf.png"),
                    "-t", "2.0", "-r", "12", "-vf", "scale=320:180",
                    "-pix_fmt", "yuv420p", "-an", str(clip)],
                   check=True, capture_output=True)
    vo = tmp_path / "vo.wav"
    _make_wav(vo, 1.2)
    return {"idx": 1, "clip": str(clip), "vo": str(vo), "duration": 2.0}


def test_pad_audio_extends_to_exact_duration(tmp_path):
    vo = tmp_path / "vo.wav"
    _make_wav(vo, 1.0)
    out = tmp_path / "padded.wav"
    pad_audio(vo, out, 2.5)
    assert abs(probe_duration(out) - 2.5) < 0.15


def test_pad_audio_without_vo_generates_silence(tmp_path):
    out = tmp_path / "silence.wav"
    pad_audio(tmp_path / "missing.wav", out, 1.5)
    assert abs(probe_duration(out) - 1.5) < 0.15


def test_compose_episode_with_subtitles(tmp_path, one_shot):
    srt = tmp_path / "episode.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n深夜便利店的相遇\n\n", "utf-8")
    out = compose_episode([one_shot], tmp_path, srt)
    assert out.exists() and out.stat().st_size > 0
    assert abs(probe_duration(out) - 2.0) < 0.4

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
         "-of", "json", str(out)], capture_output=True, text=True, check=True)
    streams = json.loads(probe.stdout)["streams"]
    kinds = {(s["codec_type"], s.get("codec_name")) for s in streams}
    assert ("video", "h264") in kinds
    assert ("audio", "aac") in kinds
    assert ("subtitle", "mov_text") in kinds  # 软字幕轨


def test_compose_episode_without_vo_and_srt(tmp_path, one_shot):
    one_shot["vo"] = str(tmp_path / "nope.wav")  # 配音缺失 → 静音轨
    out = compose_episode([one_shot], tmp_path, None)
    assert out.exists()
    assert abs(probe_duration(out) - 2.0) < 0.4


def test_compose_rejects_empty_shot_list(tmp_path):
    with pytest.raises(ComposerError, match="没有可合成"):
        compose_episode([], tmp_path, None)


def test_ffmpeg_missing_message(monkeypatch, tmp_path, one_shot):
    monkeypatch.setattr(composer, "which_ffmpeg", lambda: None)
    with pytest.raises(ComposerError, match="安装"):
        compose_episode([one_shot], tmp_path, None)

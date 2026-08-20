"""ffmpeg 成片合成器：镜头片段拼接 + 配音音轨 + 柔性字幕。

流程（全部可独立测试）：
1. 每镜头：配音（vo.wav）静音填充至镜头时长 → 分段音频；
2. 视频拼接：concat demuxer + 统一重编码（时间轴精准）；
3. 音频拼接：分段 WAV concat；
4. 封装：视频 + 音轨 + SRT 柔性字幕（mov_text，无需字体，跨平台）。

无字幕/无配音均可降级合成。ffmpeg 缺失时报可读错误。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from app.adapters.base import which_ffmpeg


class ComposerError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int = 600) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise ComposerError(f"ffmpeg 失败({proc.returncode}): {proc.stderr[-1200:]}")


def probe_duration(path: Path) -> float:
    """ffprobe 时长（秒）。"""
    ffprobe = which_ffmpeg().replace("ffmpeg", "ffprobe") \
        if which_ffmpeg() and which_ffmpeg().endswith("ffmpeg") else "ffprobe"
    proc = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                           "-of", "json", str(path)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise ComposerError(f"ffprobe 失败: {proc.stderr[-300:]}")
    return float(json.loads(proc.stdout)["format"]["duration"])


def pad_audio(vo_path: Path, out_path: Path, duration: float,
              sample_rate: int = 24000) -> None:
    """配音静音填充到精确时长（无配音则生成等长静音）。"""
    ff = which_ffmpeg()
    if vo_path.exists():
        _run([ff, "-y", "-i", str(vo_path), "-ar", str(sample_rate),
              "-ac", "1", "-af", "apad", "-t", f"{duration:.3f}", str(out_path)])
    else:
        _run([ff, "-y", "-f", "lavfi", "-i",
              f"anullsrc=r={sample_rate}:cl=mono", "-t", f"{duration:.3f}", str(out_path)])


def concat_files(paths: list[Path], list_path: Path) -> None:
    list_path.write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in paths), "utf-8")


def compose_episode(shots: list[dict[str, Any]], out_dir: Path,
                    srt_path: Path | None, cancel=None, progress=None) -> Path:
    """合成整集。

    shots: [{idx, clip: Path, vo: Path|None, duration: float}]（时间序）
    返回成片路径 episode.mp4。
    """
    ff = which_ffmpeg()
    if not ff:
        raise ComposerError("未找到 ffmpeg，无法合成成片。请安装：apt install ffmpeg")
    if not shots:
        raise ComposerError("没有可合成的镜头")
    work = out_dir / "_compose"
    work.mkdir(parents=True, exist_ok=True)

    # 1) 分段音频（配音填充至镜头时长）
    audio_parts: list[Path] = []
    for s in shots:
        if cancel is not None:
            cancel.should_cancel()
        if progress:
            progress(f"音轨填充 镜头{s['idx']}", 5 + s["idx"] * 20)
        part = work / f"a{s['idx']:03d}.wav"
        pad_audio(Path(s["vo"]), part, float(s["duration"]))
        audio_parts.append(part)
    concat_files(audio_parts, work / "alist.txt")
    full_audio = work / "audio.wav"
    if progress:
        progress("拼接音轨", 50.0)
    _run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(work / "alist.txt"),
          "-c", "copy", str(full_audio)])

    # 2) 视频拼接（统一重编码，避免不同源参数不一致）
    concat_files([Path(s["clip"]) for s in shots], work / "vlist.txt")
    silent_video = work / "video.mp4"
    if progress:
        progress("拼接镜头", 65.0)
    _run([ff, "-y", "-f", "concat", "-safe", "0", "-i", str(work / "vlist.txt"),
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
          "-an", str(silent_video)])

    # 3) 封装：音轨 + 柔性字幕
    #    注意：ffmpeg 要求所有 -i 输入集中在输出选项之前；这里用显式 -t 时长
    #    代替 -shortest（规避 mov_text 字幕流与 -shortest 组合时可能不结束的问题）。
    out = out_dir / "episode.mp4"
    cmd = [ff, "-y", "-i", str(silent_video), "-i", str(full_audio)]
    has_srt = srt_path is not None and Path(srt_path).exists()
    if has_srt:
        cmd += ["-i", str(srt_path)]
    try:
        duration = probe_duration(silent_video)
    except Exception:  # ffprobe 不可用时退回不裁剪
        duration = None
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    if has_srt:
        cmd += ["-map", "2:s:0", "-c:s", "mov_text",
                "-metadata:s:s:0", "language=chi"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-ar", "44100", str(out)]
    if progress:
        progress("封装成片", 85.0)
    _run(cmd)
    if not out.exists():
        raise ComposerError("合成完成但未找到产物 episode.mp4")
    if progress:
        progress("成片完成", 95.0)
    return out

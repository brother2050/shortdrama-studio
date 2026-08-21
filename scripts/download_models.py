#!/usr/bin/env python3
"""离线模型下载脚本（ModelScope）——一次下载，永久离线使用。

用法
----
列出全部可选模型：
    python scripts/download_models.py --list

按能力下载推荐（默认）模型：
    python scripts/download_models.py                 # 全部推荐档（体积见 --list）
    python scripts/download_models.py --capability llm
    python scripts/download_models.py --capability tts image

指定档位下载（名字见 --list 的「档位」列）：
    python scripts/download_models.py --capability llm --preset qwen2.5-1.5b

指定本地目录（默认 ./models，与设置页 model_path 对应）：
    python scripts/download_models.py --capability image --local-dir /data/models

设计要点
--------
- 只做下载不做重试编排：失败时打印可读原因，由用户手工重新运行
  （与平台"手工重试"的交互约定一致）。
- 下载完成后打印每个能力需要写入「设置」页的参数（model_path 等）。
- 未安装 modelscope 时给出准确安装指引（pip install -r requirements-models.txt）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 各能力模型目录：档位名 → (ModelScope repo_id, 磁盘占用约值 GB, 说明)
MODEL_CATALOG: dict[str, dict[str, tuple[str, float, str]]] = {
    "llm": {
        "qwen2.5-0.5b": ("qwen/Qwen2.5-0.5B-Instruct", 1.0,
                          "剧本/分镜生成入门档，CPU 可跑"),
        "qwen2.5-1.5b": ("qwen/Qwen2.5-1.5B-Instruct", 3.0, "推荐：质量/资源均衡"),
        "qwen2.5-7b": ("qwen/Qwen2.5-7B-Instruct", 15.0, "高质量档（需 GPU）"),
    },
    "tts": {
        "cosyvoice2-0.5b": ("iic/CosyVoice2-0.5B", 5.0,
                             "推荐：多音色中文配音（tts=cosyvoice）"),
        "chattts": ("pzc163/chatTTS", 2.0,
                     "对话感中文配音（tts=chattts，另需 pip install ChatTTS）"),
        "gpt-sovits": ("AIDub/GPT-SoVITS", 4.0,
                        "声音克隆配音（tts=gpt_sovits，另需 GPT-SoVITS 仓库源码安装）"),
        "fish-speech-1.5": ("fishaudio/fish-speech-1.5", 8.0,
                             "多语言配音/克隆（tts=fish_speech，另需 fish-speech 仓库源码安装）"),
    },
    "image": {
        "sd15": ("AI-ModelScope/stable-diffusion-v1-5", 5.0, "入门：快、省显存"),
        "sdxl": ("stabilityai/stable-diffusion-xl-base-1.0", 9.0,
                  "推荐：关键帧质量好（需 ≥10GB 显存）"),
        "flux-schnell": ("AI-ModelScope/FLUX.1-schnell", 12.0,
                          "高质量档，4 步出图（Apache-2.0）"),
        "qwen-image": ("Qwen/Qwen-Image", 40.0,
                        "Qwen-Image 文生图：中文语义强（需 ≥24GB 显存）"),
        "qwen-image-edit": ("Qwen/Qwen-Image-Edit-2509", 40.0,
                             "多图编辑：角色一致性（参考图锁定外貌）"),
    },
    "video": {
        "wan2.2-ti2v-5b": ("Wan-AI/Wan2.2-TI2V-5B", 15.0,
                             "推荐：图生视频（单模型 T2V+I2V，≈8GB 显存）"),
        "wan2.1-1.3b": ("Wan-AI/Wan2.1-T2V-1.3B", 8.0,
                          "轻量：文生视频/视频续写"),
        "wan2.2-i2v-a14b": ("Wan-AI/Wan2.2-I2V-A14B", 60.0,
                             "高质量 I2V（MoE，需 ≥24GB 显存）"),
        "wan2.1-flf2v-14b": ("Wan-AI/Wan2.1-FLF2V-14B-720P", 60.0,
                              "首尾帧过渡：镜头间平滑转场（需 ≥24GB 显存）"),
    },
    "asr": {
        "sensevoice-small": ("iic/SenseVoiceSmall", 1.0,
                              "推荐：中文语音识别（字幕校对）"),
    },
}

# 各能力的默认下载档位（不指定 --preset 时使用）
DEFAULT_PRESET = {"llm": "qwen2.5-1.5b", "tts": "cosyvoice2-0.5b",
                  "image": "sd15", "video": "wan2.2-ti2v-5b",
                  "asr": "sensevoice-small"}


def list_catalog() -> None:
    print(f"{'能力':<8}{'档位':<18}{'ModelScope 仓库':<46}{'约 GB':<8}说明")
    print("-" * 110)
    for cap, presets in MODEL_CATALOG.items():
        star = DEFAULT_PRESET[cap]
        for name, (repo, size, note) in presets.items():
            mark = "*" if name == star else " "
            print(f"{cap:<8}{name + mark:<18}{repo:<46}{size:<8.0f}{note}")
    print("\n* 为该能力默认档位（不加 --preset 时下载它）")


def download(cap: str, preset: str, local_dir: Path) -> Path | None:
    try:
        from modelscope import snapshot_download
    except ImportError:
        print("未安装 modelscope。先执行：\n"
              "    pip install -r requirements-models.txt\n"
              "或最小安装：pip install modelscope")
        return None

    repo, _, note = MODEL_CATALOG[cap][preset]
    target = local_dir / cap / preset
    print(f"[{cap}] {preset} ← {repo}\n  → {target}（{note}）")
    os.environ.setdefault("MODELSCOPE_CACHE", str(local_dir / "_ms_cache"))
    path = snapshot_download(repo, local_dir=str(target))
    print(f"  完成：{path}")
    return Path(path)


def print_usage_hint(results: dict[str, tuple[str, Path | None]]) -> None:
    print("\n==== 下载完成，请在「设置」页写入以下参数 ====")
    for cap, (preset, path) in results.items():
        if path is None:
            continue
        if cap == "llm":
            repo_id, _, _ = MODEL_CATALOG[cap][preset]
            backend, params = "modelscope", {"model_id": repo_id}
        elif cap == "tts":
            if "chatTTS" in str(path) or "chattts" in str(path):
                backend, params = "chattts", {"model_dir": str(path)}
            elif "GPT-SoVITS" in str(path):
                backend, params = "gpt_sovits", {"ref_audio": "填参考音频wav路径",
                                                  "prompt_text": "填参考音频文本"}
            elif "fish" in str(path):
                backend, params = "fish_speech", {"checkpoint_dir": str(path)}
            else:
                backend, params = "cosyvoice", {"model_dir": str(path)}
        elif cap == "image":
            backend, params = "diffsynth", {"model_preset": preset}
        elif cap == "video":
            backend, params = "diffsynth_wan", {"model_preset": preset}
        else:
            backend, params = "funasr", {"model": str(path)}
        print(f"  [{cap}] backend={backend}  params={params}")
    print("或直接编辑 data/config.json（人可读格式），改完即时生效。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ModelScope 离线模型下载")
    parser.add_argument("--list", action="store_true", help="列出全部可选模型")
    parser.add_argument("--capability", "-c",
                        choices=[*MODEL_CATALOG, "all"], default="all",
                        help="下载哪个能力（默认 all=全部默认档）")
    parser.add_argument("--preset", "-p", default=None,
                        help="档位名（--list 查看；默认用各能力推荐档）")
    parser.add_argument("--local-dir", default="./models", type=Path,
                        help="模型存放目录（默认 ./models）")
    args = parser.parse_args(argv)

    if args.list:
        list_catalog()
        return 0

    caps = list(MODEL_CATALOG) if args.capability == "all" else [args.capability]
    results: dict[str, tuple[str, Path | None]] = {}
    for cap in caps:
        preset = args.preset or DEFAULT_PRESET[cap]
        if preset not in MODEL_CATALOG[cap]:
            print(f"未知档位 {preset!r}（{cap}）。可选：{list(MODEL_CATALOG[cap])}")
            return 2
        results[cap] = (preset, download(cap, preset, args.local_dir))
    print_usage_hint(results)
    ok = all(p is not None for _, p in results.values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

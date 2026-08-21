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

模型统一存放在**项目根 models/** 目录（无论在哪个目录运行本脚本）：
    models/<能力>/<预设名>/          各预设模型文件
    models/<能力>/_shared/<组件>/    跨预设共享组件（如 Wan 的 umt5 tokenizer）
    models/_cache/                  ModelScope 下载缓存

也可用 ``--local-dir`` 或环境变量 ``STUDIO_MODELS_DIR`` 覆盖根目录。

设计要点
--------
- 预设目录唯一数据源是 ``app/models_registry.py``（与设置页/API 共用）；
- 只做下载不做重试编排：失败时打印可读原因，由用户手工重新运行
  （与平台"手工重试"的交互约定一致）；
- 下载完成后打印每个能力的推荐配置（backend + params，直接粘贴到设置页，
  或用设置页「模型预设」下拉自动填充）；
- 未安装 modelscope 时给出准确安装指引（pip install -r requirements-models.txt）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 让脚本在仓库任意位置可直接运行（python scripts/download_models.py）
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app import models_registry                       # noqa: E402
from app.models_registry import (DEFAULT_PRESET, ModelPreset,        # noqa: E402
                                 REGISTRY)


def list_catalog() -> None:
    print(f"{'能力':<8}{'档位':<18}{'ModelScope 仓库':<46}{'约 GB':<8}说明")
    print("-" * 110)
    for cap, presets in REGISTRY.items():
        star = DEFAULT_PRESET[cap]
        for preset in presets.values():
            mark = "*" if preset.name == star else " "
            print(f"{cap:<8}{preset.name + mark:<18}{preset.repo_id:<46}"
                  f"{preset.size_gb:<8.0f}{preset.desc}")
    print("\n* 为该能力默认档位（不加 --preset 时下载它）")
    print(f"模型根目录：{models_registry.paths.models_root()}")


def _download_repo(repo_id: str, target: Path,
                   file_pattern: str | None = None) -> Path | None:
    """下载一个 ModelScope 仓库（可选只下载部分文件）到 target。"""
    try:
        from modelscope import snapshot_download
    except ImportError:
        print("未安装 modelscope。先执行：\n"
              "    pip install -r requirements-models.txt\n"
              "或最小安装：pip install modelscope")
        return None

    target.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {"local_dir": str(target)}
    if file_pattern:
        kwargs["allow_file_pattern"] = file_pattern
    print(f"  ← {repo_id}"
          f"{f'（{file_pattern}）' if file_pattern else ''}\n  → {target}")
    path = snapshot_download(repo_id, **kwargs)
    print(f"  完成：{path}")
    return Path(path)


def download(preset: ModelPreset, root: Path) -> Path | None:
    """下载预设本体 + 其依赖的共享组件。"""
    print(f"[{preset.capability}] {preset.name} — {preset.desc}")
    os.environ.setdefault("MODELSCOPE_CACHE", str(root / "_cache"))

    # 共享组件（跨预设只下载一次；已存在则跳过）
    for shared in preset.shared:
        shared_dir = root / shared.into
        if shared_dir.exists() and any(shared_dir.iterdir()):
            print(f"  共享组件已存在，跳过：{shared_dir}")
            continue
        _download_repo(shared.repo_id, shared_dir, shared.file_pattern)

    return _download_repo(preset.repo_id, preset.local_dir())


def print_usage_hint(results: list[ModelPreset]) -> None:
    """打印可直接粘贴到设置页 / data/config.json 的推荐配置。"""
    print("\n==== 下载完成，推荐配置（设置页「模型预设」下拉可自动填充）====")
    for preset in results:
        print(f"  [{preset.capability}] backend={preset.backend}"
              f"  params={preset.params}")
    print("提示：设置页选择对应能力的「模型预设」后，JSON 参数会自动生成，"
          "可再手动微调。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ModelScope 离线模型下载")
    parser.add_argument("--list", action="store_true", help="列出全部可选模型")
    parser.add_argument("--capability", "-c",
                        choices=[*REGISTRY, "all"], default="all",
                        help="下载哪个能力（默认 all=全部默认档）")
    parser.add_argument("--preset", "-p", default=None,
                        help="档位名（--list 查看；默认用各能力推荐档）")
    parser.add_argument(
        "--local-dir", default=None, type=Path,
        help="模型根目录（默认：项目根 models/，与运行位置无关；"
             "也可用环境变量 STUDIO_MODELS_DIR 指定）")
    args = parser.parse_args(argv)

    if args.list:
        list_catalog()
        return 0

    # 根目录：--local-dir > STUDIO_MODELS_DIR > 项目根 models/
    if args.local_dir is not None:
        os.environ["STUDIO_MODELS_DIR"] = str(
            args.local_dir.expanduser().resolve())
    root = models_registry.paths.models_root()
    if args.capability == "all":
        caps = list(REGISTRY)
    else:
        caps = [args.capability]

    done: list[ModelPreset] = []
    ok = True
    for cap in caps:
        name = args.preset or DEFAULT_PRESET[cap]
        preset = models_registry.find_preset(cap, name)
        if preset is None:
            print(f"未知档位 {name!r}（{cap}）。可选：{list(REGISTRY[cap])}")
            return 2
        path = download(preset, root)
        if path is None:
            ok = False
        else:
            done.append(preset)

    if done:
        print_usage_hint(done)
    print(f"\n模型根目录：{root}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""适配器包：导入即注册全部内置后端，并扫描 plugins/ 目录自动加载插件。

新增后端的两种方式：
1. 内置：在本目录新建模块并 ``@register_adapter``，然后在下方 import。
2. 插件：把文件放到 ``app/adapters/plugins/``（无需修改任何现有代码）。
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path

from app.adapters.base import (AdapterBase, AdapterError, AdapterUnavailableError,
                               AdapterRegistry, AdapterSpec, CAPABILITIES,
                               registry, register_adapter, which_ffmpeg)

logger = logging.getLogger("app.adapters")

# -- 内置后端（导入即注册） ---------------------------------------------
from app.adapters import llm_mock          # noqa: E402,F401
from app.adapters import llm_ollama        # noqa: E402,F401
from app.adapters import llm_transformers  # noqa: E402,F401
from app.adapters import tts_mock          # noqa: E402,F401
from app.adapters import tts_mosaic        # noqa: E402,F401
from app.adapters import tts_cosyvoice     # noqa: E402,F401
from app.adapters import image_mock        # noqa: E402,F401
from app.adapters import image_diffusers   # noqa: E402,F401
from app.adapters import video_kenburns    # noqa: E402,F401
from app.adapters import video_wan         # noqa: E402,F401
from app.adapters import asr_script        # noqa: E402,F401
from app.adapters import asr_funasr        # noqa: E402,F401


def load_plugins() -> list[str]:
    """扫描 plugins/ 目录并导入（文件名即模块名，导入即注册）。"""
    loaded: list[str] = []
    plugin_dir = Path(__file__).parent / "plugins"
    if not plugin_dir.exists():
        return loaded
    for py in sorted(plugin_dir.glob("*.py")):
        if py.name.startswith("_"):
            continue
        mod = f"app.adapters.plugins.{py.stem}"
        try:
            importlib.import_module(mod)
            loaded.append(mod)
            logger.info("已加载适配器插件 %s", mod)
        except Exception as exc:  # 插件失败不影响主程序
            logger.warning("插件 %s 加载失败: %s", mod, exc)
    return loaded


load_plugins()

__all__ = [
    "AdapterBase", "AdapterSpec", "AdapterError", "AdapterUnavailableError",
    "AdapterRegistry", "registry", "register_adapter", "CAPABILITIES",
    "which_ffmpeg",
]

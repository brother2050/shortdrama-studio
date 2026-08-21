"""TTS 引擎契约：四引擎统一接口（移植自 mosaic TTS 节点的路由设计）。

约定：
- 每个引擎一个模块，实现 ``TTSEngineBase`` 子类并导出模块级单例 ``engine``。
- ``ready(params)``：轻量探测（importlib 检查 / 短超时 socket），不得加载模型。
- ``synthesize(text, voice, out_path, params, progress)``：产出标准 WAV 文件，
  返回 ``{"duration": float, "sample_rate": int}``。
- ``unload()``：释放本地引擎占用的显存（HTTP 引擎无操作）。
- 重依赖（torch / ChatTTS / cosyvoice）必须在 ``synthesize`` 内惰性导入，
  保证未安装时模块可导入、可被探测为"未就绪"。
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Any, Callable

from app.adapters.base import AdapterError

ProgressFn = Callable[[str, float], None]


class TTSEngineBase:
    """TTS 引擎基类：name / label 由子类声明。"""

    name: str = ""
    label: str = ""
    kind: str = "local"          # local=本地库推理 / http=外部服务

    def ready(self, params: dict[str, Any]) -> tuple[bool, str]:
        """探测引擎是否就绪（轻量，不加载模型）。返回 (就绪, 原因)。"""
        raise NotImplementedError

    def synthesize(self, text: str, voice: str, out_path: Path,
                   params: dict[str, Any],
                   progress: ProgressFn | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def unload(self) -> None:  # noqa: B027（HTTP 引擎无操作）
        """释放模型显存（本地引擎覆盖）。"""


def wav_info(path: Path) -> tuple[float, int]:
    """读取 WAV 文件头：返回 (时长秒, 采样率)。"""
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() / w.getframerate(), 3), w.getframerate()


def http_reachable(url: str, timeout: float = 0.3) -> bool:
    """探测 HTTP 服务端口是否可达（短超时，不发起业务请求）。"""
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_path_param(params: dict[str, Any], key: str,
                     engine_label: str, hint: str) -> str:
    """校验并返回非空路径参数（给用户可读的修复提示）。"""
    value = str(params.get(key) or "").strip()
    if not value:
        raise AdapterError(f"{engine_label} 需要设置参数 {key}。{hint}")
    return value

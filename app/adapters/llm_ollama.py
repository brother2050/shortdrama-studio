"""LLM 后端 3/3：ollama（本机 OpenAI 兼容服务，完全离线）。

依赖本机已运行 Ollama（`ollama serve`）并 `ollama pull qwen2.5:1.5b`。
使用标准库 urllib 直连 127.0.0.1，无额外 Python 依赖。
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@register_adapter
class OllamaLLM(AdapterBase):
    spec = AdapterSpec(
        name="ollama", capability="llm", display_name="Ollama（本机服务）",
        description="连接本机 Ollama 服务（OpenAI 兼容），如 qwen2.5:0.5b/1.5b/7b。"
                    "适合已有本地推理服务的环境，显存由 Ollama 管理。",
        priority=15, requires=[],
        default_params={
            "host": "127.0.0.1",
            "port": 11434,
            "model": "qwen2.5:1.5b",
            "max_new_tokens": 1024,
            "temperature": 0.8,
        },
        param_docs={
            "host": "Ollama 服务地址", "port": "端口（默认 11434）",
            "model": "模型标签（需先 ollama pull）",
            "max_new_tokens": "单次生成最大 token 数", "temperature": "采样温度",
        },
        license="遵循所拉取模型的许可",
    )

    @classmethod
    def _extra_available(cls) -> bool:
        return _port_open("127.0.0.1", 11434)

    @classmethod
    def _unavailable_reason(cls) -> str:
        return "本机 11434 端口无服务：请先运行 `ollama serve` 并 `ollama pull qwen2.5:1.5b`"

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        host = self.params.get("host", "127.0.0.1")
        port = int(self.params.get("port", 11434))
        messages = []
        if ctx.get("system"):
            messages.append({"role": "system", "content": ctx["system"]})
        messages += [m for m in ctx.get("messages", []) if m.get("role") != "system"]
        payload = {
            "model": self.params.get("model", "qwen2.5:1.5b"),
            "messages": messages, "stream": False,
            "options": {
                "num_predict": int(self.params.get("max_new_tokens", 1024)),
                "temperature": float(self.params.get("temperature", 0.8)),
            },
        }
        url = f"http://{host}:{port}/api/chat"
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        if progress:
            progress("请求 Ollama 生成", 30.0)
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise AdapterError(f"Ollama 请求失败（{url}）: {exc}") from exc
        text = (data.get("message") or {}).get("content", "").strip()
        if not text:
            raise AdapterError(f"Ollama 返回空内容: {data}")
        if progress:
            progress("生成完成", 90.0)
        return {"text": text}

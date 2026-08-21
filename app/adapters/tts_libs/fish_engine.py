"""引擎 4/4：Fish Speech（HTTP 服务方式，零 Python 依赖）。

移植要点（官方 tools/api_server.py，github.com/fishaudio/fish-speech）：
- 服务启动：``python tools/api_server.py --listen 127.0.0.1:8080``
  （可选 ``--api-key`` 启用 Bearer 认证）。
- ``POST /v1/tts`` 请求 JSON（FastAPI/pydantic 模型）：``text / format / reference_id``，
  可选 ``top_p / temperature / max_new_tokens / chunk_length``。
- 响应：音频二进制流（format=wav 时为 WAV 字节，默认采样率 44100）。
- 角色音色：``fish_voice_refs`` 把平台音色 id 映射到预录 reference_id（声音克隆）。

仅用标准库 urllib，不引入 requests / ormsgpack —— 依赖最小原则。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.adapters.base import AdapterError
from app.adapters.tts_libs._base import (ProgressFn, TTSEngineBase,
                                         http_reachable, wav_info)

DEFAULT_URL = "http://127.0.0.1:8080"


class FishSpeechEngine(TTSEngineBase):
    name = "fish_speech"
    label = "Fish Speech（服务）"
    kind = "http"

    def ready(self, params: dict[str, Any]) -> tuple[bool, str]:
        url = str(params.get("fish_url") or DEFAULT_URL)
        if not http_reachable(url):
            return False, f"Fish Speech 服务不可达（{url}）。启动：python tools/api_server.py --listen 127.0.0.1:8080"
        return True, ""

    def synthesize(self, text: str, voice: str, out_path: Path,
                   params: dict[str, Any],
                   progress: ProgressFn | None = None) -> dict[str, Any]:
        base = str(params.get("fish_url") or DEFAULT_URL).rstrip("/")
        refs = dict(params.get("fish_voice_refs") or {})
        reference_id = str(refs.get(voice)
                           or params.get("fish_reference_id") or "").strip()
        if not reference_id:
            raise AdapterError(
                "Fish Speech 需要音色 reference_id：设置参数 fish_reference_id（全局），"
                "或 fish_voice_refs 按音色映射角色（服务端预录的参考音色）。")
        headers = {"Content-Type": "application/json"}
        api_key = str(params.get("fish_api_key") or "").strip()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {
            "text": text, "format": "wav", "reference_id": reference_id,
            "latency": "normal", "streaming": False,
            "top_p": 0.8, "temperature": 0.8,
            "max_new_tokens": 1024, "chunk_length": 300,
        }
        if progress:
            progress(f"请求 Fish Speech（音色 {voice}）", 60.0)
        req = urllib.request.Request(
            f"{base}/v1/tts", data=json.dumps(body).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = resp.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "ignore")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise AdapterError(f"Fish Speech 返回 {exc.code}：{detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise AdapterError(
                f"Fish Speech 服务连接失败（{base}）：{exc.reason}。"
                "请确认已启动 tools/api_server.py（默认端口 8080）。") from exc
        if len(data) < 44 or data[:4] != b"RIFF":
            raise AdapterError("Fish Speech 未返回有效 WAV 音频（检查 reference_id 是否存在）")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        duration, sr = wav_info(out_path)
        if progress:
            progress("合成完成", 90.0)
        return {"duration": duration, "sample_rate": sr}


engine = FishSpeechEngine()

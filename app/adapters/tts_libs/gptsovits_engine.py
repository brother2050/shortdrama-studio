"""引擎 3/4：GPT-SoVITS（HTTP 服务方式，零 Python 依赖）。

移植要点（官方 api_v2.py，github.com/RVC-Boss/GPT-SoVITS）：
- 服务启动：``python api_v2.py -a 127.0.0.1 -p 9880``（默认端口 9880）。
- ``POST /tts`` 请求 JSON：必填 ``text / text_lang / ref_audio_path / prompt_lang``，
  可选 ``text_split_method / media_type / streaming_mode / speed_factor``。
- 响应：WAV 音频二进制流（失败时 HTTP 400 + JSON 错误）。
- 角色克隆音色：每个平台音色 id 可映射独立参考音频（``sovits_voice_refs``）。

仅用标准库 urllib，不引入 requests —— 依赖最小原则。
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

DEFAULT_URL = "http://127.0.0.1:9880"


class GPTSoVITSEngine(TTSEngineBase):
    name = "gpt_sovits"
    label = "GPT-SoVITS（服务）"
    kind = "http"

    def ready(self, params: dict[str, Any]) -> tuple[bool, str]:
        url = str(params.get("sovits_url") or DEFAULT_URL)
        if not http_reachable(url):
            return False, f"GPT-SoVITS 服务不可达（{url}）。启动：python api_v2.py -p 9880"
        return True, ""

    def synthesize(self, text: str, voice: str, out_path: Path,
                   params: dict[str, Any],
                   progress: ProgressFn | None = None) -> dict[str, Any]:
        base = str(params.get("sovits_url") or DEFAULT_URL).rstrip("/")
        refs = dict(params.get("sovits_voice_refs") or {})
        ref_audio = str(refs.get(voice)
                        or params.get("sovits_ref_audio") or "").strip()
        if not ref_audio:
            raise AdapterError(
                "GPT-SoVITS 需要参考音频：设置参数 sovits_ref_audio（全局参考 wav），"
                "或 sovits_voice_refs 按音色映射各角色参考音频（声音克隆）。")
        prompt_text = str(params.get("sovits_prompt_text") or "").strip()
        if not prompt_text:
            raise AdapterError(
                "GPT-SoVITS 需要参考音频的文本（参数 sovits_prompt_text，"
                "即参考音频里说的话，用于音色对齐）。")
        body = {
            "text": text, "text_lang": "zh",
            "ref_audio_path": ref_audio,
            "prompt_text": prompt_text, "prompt_lang": "zh",
            "text_split_method": str(params.get("sovits_split_method") or "cut5"),
            "media_type": "wav", "streaming_mode": False,
        }
        if progress:
            progress(f"请求 GPT-SoVITS（音色 {voice}）", 60.0)
        req = urllib.request.Request(
            f"{base}/tts", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = resp.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "ignore")[:300]
            except Exception:  # noqa: BLE001
                pass
            raise AdapterError(f"GPT-SoVITS 返回 {exc.code}：{detail or exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise AdapterError(
                f"GPT-SoVITS 服务连接失败（{base}）：{exc.reason}。"
                "请确认已启动 api_v2.py（默认端口 9880）。") from exc
        if len(data) < 44 or data[:4] != b"RIFF":
            raise AdapterError("GPT-SoVITS 未返回有效 WAV 音频")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        duration, sr = wav_info(out_path)
        if progress:
            progress("合成完成", 90.0)
        return {"duration": duration, "sample_rate": sr}


engine = GPTSoVITSEngine()

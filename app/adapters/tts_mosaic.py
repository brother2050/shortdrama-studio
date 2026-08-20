"""TTS 后端 2/3：mosaic（参考项目 brother2050/mosaic 的 TTS 节点，四后端路由）。

mosaic 的 TTS 节点内置 ChatTTS / Fish Speech / GPT-SoVITS / CosyVoice 四后端，
本适配器复用其节点接口（需 `pip install -e /path/to/mosaic`）。
重依赖全部惰性导入；未安装时自动探测为不可用。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.adapters.base import (AdapterBase, AdapterError, AdapterSpec,
                               register_adapter)
from app.adapters.tts_mock import SAMPLE_RATE, write_wav


@register_adapter
class MosaicTTS(AdapterBase):
    spec = AdapterSpec(
        name="mosaic", capability="tts", display_name="mosaic TTS（四后端路由）",
        description="复用 mosaic 框架的 TTS 节点：ChatTTS/Fish Speech/GPT-SoVITS/CosyVoice "
                    "按语言/克隆/显存需求自动路由。需安装 mosaic 包并准备好其模型。",
        priority=15, requires=["mosaic"],
        default_params={"model": "auto", "device": "auto", "sample_rate": SAMPLE_RATE},
        param_docs={
            "model": "mosaic TTS 模型名（auto 表示由 mosaic 自动选择）",
            "device": "推理设备 auto/cpu/cuda", "sample_rate": "输出采样率",
        },
        vram_gb=2.0, license="遵循所选后端模型许可（ChatTTS 为 CC-BY-NC-4.0）",
    )

    _node = None

    def _get_node(self):
        if self._node is not None:
            return self._node
        from mosaic.nodes.audio import TTS  # 惰性导入
        kwargs = {}
        if self.params.get("model", "auto") != "auto":
            kwargs["model"] = self.params["model"]
        if self.params.get("device", "auto") != "auto":
            kwargs["device"] = self.params["device"]
        self._node = TTS(**kwargs)
        return self._node

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        text = str(ctx.get("text", "")).strip()
        if not text:
            raise AdapterError("TTS 文本为空")
        out = Path(ctx["out_path"])
        tmp = out.with_suffix(".tmp.wav")
        if progress:
            progress("加载 mosaic TTS 节点", 20.0)
        node = self._get_node()
        if progress:
            progress("合成中", 60.0)
        try:
            result = node.run(text, output_path=str(tmp))
        except TypeError:
            result = node.run(text)
        # 归一化产物路径：mosaic 可能返回 dict / 对象 / None（已写 tmp）
        path = tmp
        if isinstance(result, dict):
            path = Path(result.get("path") or result.get("output_path") or tmp)
        elif result is not None and hasattr(result, "path"):
            path = Path(result.path)
        if not Path(path).exists():
            raise AdapterError(f"mosaic TTS 未产出文件（result={result!r}）")
        out.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, out)
        if progress:
            progress("合成完成", 90.0)
        return {"path": str(out), "duration": _wav_duration(out),
                "sample_rate": _wav_sample_rate(out), "voice": ctx.get("voice")}


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return round(w.getnframes() / w.getframerate(), 3)


def _wav_sample_rate(path: Path) -> int:
    with wave.open(str(path), "rb") as w:
        return w.getframerate()

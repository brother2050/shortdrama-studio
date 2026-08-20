"""图像后端 1/2：mock（内置，纯标准库 PNG 编码器，默认兜底）。

按提示词哈希生成确定性"电影场记卡"：渐变天空 + 地平线 + 圆形光源 +
电影黑边 + 底部字幕条 + 场记文字（E01/S03）。无任何第三方依赖
（zlib + struct 实现最小 PNG 写入器），保证无模型环境全链路可出片。
"""
from __future__ import annotations

import hashlib
import zlib
from pathlib import Path
from typing import Any

from app.adapters.base import AdapterBase, AdapterSpec, register_adapter

# 5x7 点阵字体（仅覆盖场记卡所需字符）
_FONT: dict[str, list[str]] = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "-": ["00000", "00000", "00000", "01110", "00000", "00000", "00000"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}

_PALETTES = [
    ((24, 28, 46), (72, 52, 92), (255, 196, 112)),   # 暮紫
    ((16, 34, 54), (36, 90, 110), (140, 220, 200)),   # 青夜
    ((46, 22, 26), (120, 60, 52), (255, 158, 106)),   # 暖橘
    ((20, 24, 30), (60, 76, 92), (200, 214, 228)),    # 雨蓝
]


def _lerp(c1: tuple, c2: tuple, t: float) -> tuple:
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _hash01(text: str) -> float:
    return int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF


def write_png(path: Path, width: int, height: int, rows: list[bytearray]) -> None:
    """最小 PNG 写入器（RGB8，zlib 为 C 实现，速度可接受）。"""
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (len(data).to_bytes(4, "big") + tag + data
                + zlib.crc32(tag + data).to_bytes(4, "big"))

    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    payload = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
               + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def render_slate(width: int, height: int, prompt: str, label: str) -> list[bytearray]:
    """渲染电影场记卡（返回 RGB 行数据，可独立测试）。"""
    h01 = _hash01(prompt)
    pal = _PALETTES[int(h01 * len(_PALETTES)) % len(_PALETTES)]
    top, bottom, accent = pal
    sun_r = int(min(width, height) * (0.10 + 0.06 * h01))
    sun_cx = int(width * (0.30 + 0.4 * ((h01 * 97) % 1.0)))
    sun_cy = int(height * (0.30 + 0.15 * ((h01 * 57) % 1.0)))
    horizon = int(height * 0.68)
    rows: list[bytearray] = []
    for y in range(height):
        t = y / max(1, height - 1)
        base = _lerp(top, bottom, t ** 1.2)
        if y > horizon:  # 地平线以下压暗
            k = 1.0 - 0.45 * min(1.0, (y - horizon) / max(1, height - horizon))
            base = tuple(int(v * k) for v in base)
        row = bytearray(bytes(base) * width)
        # 圆形光源
        dy = y - sun_cy
        span = sun_r * sun_r - dy * dy
        if span > 0:
            half = int(span ** 0.5)
            x0, x1 = max(0, sun_cx - half), min(width, sun_cx + half)
            if x1 > x0:
                row[x0 * 3:x1 * 3] = bytes(accent) * (x1 - x0)
        # 顶部/底部电影黑边
        if y < height // 18 or y > height - height // 9:
            row[:] = bytearray((12, 12, 16)) * width
        rows.append(row)

    # 场记文字（点阵缩放）
    scale = max(2, height // 45)
    x0 = width // 18
    y0 = height - height // 9 - 7 * scale - 8
    for i, ch in enumerate(label[:12]):
        glyph = _FONT.get(ch.upper(), _FONT[" "])
        gx = x0 + i * 6 * scale
        for gy, line in enumerate(glyph):
            yy = y0 + gy * scale
            if yy < 0 or yy >= height:
                continue
            for gx2, bit in enumerate(line):
                if bit == "1":
                    xx = gx + gx2 * scale
                    for sy in range(scale):
                        ry = yy + sy
                        if 0 <= ry < height and xx + scale <= width:
                            rows[ry][xx * 3:(xx + scale) * 3] = bytes((235, 240, 246)) * scale
    return rows


@register_adapter
class MockImage(AdapterBase):
    spec = AdapterSpec(
        name="mock", capability="image", display_name="内置场记卡（离线）",
        description="纯标准库 PNG 编码器，按提示词哈希生成确定性电影场记卡，"
                    "用于无模型环境的全链路演示与测试。",
        priority=10, requires=[],
        default_params={"width": 1280, "height": 720},
        param_docs={"width": "画面宽（默认 1280）", "height": "画面高（默认 720）"},
        license="MIT（本项目内置）",
    )

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        prompt = str(ctx.get("prompt", ""))
        out = Path(ctx["out_path"])
        width = int(ctx.get("width") or self.params.get("width", 1280))
        height = int(ctx.get("height") or self.params.get("height", 720))
        label = str(ctx.get("label", "SHORTDRAMA"))
        if progress:
            progress("渲染场记卡", 50.0)
        rows = render_slate(width, height, prompt, label)
        write_png(out, width, height, rows)
        if progress:
            progress("场记卡完成", 90.0)
        return {"path": str(out), "width": width, "height": height}

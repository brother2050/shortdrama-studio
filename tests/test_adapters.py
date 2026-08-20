"""适配器层测试：注册表 / auto 选择 / mock 后端产物可用性 / 重依赖惰性导入。"""
from __future__ import annotations

import json
import struct
import sys

import pytest

from app.adapters import registry
from app.adapters.base import AdapterError, AdapterUnavailableError


def test_registry_has_specs_for_all_capabilities():
    from app.adapters.base import CAPABILITIES

    zero_dep_fallback = {"llm": "mock", "tts": "mock", "image": "mock",
                         "video": "kenburns", "asr": "script"}
    for cap in CAPABILITIES:
        names = registry.names(cap)
        assert names, f"{cap} 无注册后端"
        assert zero_dep_fallback[cap] in names, f"{cap} 缺少离线兜底后端"


def test_auto_pick_offline_returns_runnable_backend():
    # 测试环境未装 torch 等重依赖：auto 必须选到可用后端（mock 系）
    for cap in ("llm", "tts", "image", "video"):
        picked = registry.auto_pick(cap)
        adapter = registry.resolve(cap, picked)
        assert adapter is not None


def test_resolve_unknown_backend_lists_alternatives():
    with pytest.raises(AdapterError, match="未注册"):
        registry.resolve("tts", "no_such_backend")


def test_resolve_explicit_unavailable_backend_gives_fix_hint():
    names = registry.names("tts")
    unavailable = None
    for n in names:
        cls = registry.get_class("tts", n)
        if cls and not cls.is_available():
            unavailable = n
            break
    if unavailable is None:
        pytest.skip("环境内全部 TTS 后端可用（罕见），跳过")
    with pytest.raises((AdapterUnavailableError, AdapterError)):
        registry.resolve("tts", unavailable)


def test_mock_llm_all_stages_return_json():
    from app.adapters.llm_mock import MockLLM

    llm = MockLLM()
    for stage, prompt in [
        ("worldview", "[STAGE:worldview] 剧名: 晚风\n题材: 都市情感\n创意: 便利店相遇\n集数: 3"),
        ("script", "[STAGE:script] 集数: 2\n本集标题: 第2集\n前情摘要: 两人重逢\n角色: 林晚, 陆则铭"),
        ("storyboard", "[STAGE:storyboard] 镜头数: 3"),
    ]:
        out = llm.run({"messages": [{"role": "user", "content": prompt}]})
        data = json.loads(out["text"])
        assert isinstance(data, dict)
    world = json.loads(llm.run({"messages": [
        {"role": "user", "content": "[STAGE:worldview] 剧名: 晚风"}]})["text"])
    assert len(world["characters"]) >= 2
    assert len(world["episode_outline"]) >= 1


def test_mock_llm_deterministic_same_seed():
    from app.adapters.llm_mock import MockLLM

    llm = MockLLM()
    ctx = {"messages": [{"role": "user", "content": "[STAGE:worldview] 剧名: X"}]}
    assert llm.run(ctx)["text"] == llm.run(ctx)["text"]


def test_mock_tts_wav_playable(tmp_path):
    from app.adapters.tts_mock import MockTTS

    out = tmp_path / "vo.wav"
    res = MockTTS({"sample_rate": 16000}).run({"text": "你好，晚风。", "out_path": str(out)})
    assert out.exists() and out.stat().st_size > 44
    assert out.read_bytes()[:4] == b"RIFF"          # WAV 魔数
    assert res["duration"] > 0.5
    sr = struct.unpack("<I", out.read_bytes()[24:28])[0]
    assert sr == 16000


def test_mock_image_png(tmp_path):
    from app.adapters.image_mock import MockImage

    out = tmp_path / "kf.png"
    res = MockImage().run({"prompt": "便利店夜景", "out_path": str(out),
                           "width": 64, "height": 48})
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (res["width"], res["height"]) == (64, 48)


@pytest.mark.skipif(not registry.get_class("video", "kenburns") or
                    not registry.get_class("video", "kenburns").is_available(),
                    reason="需要 ffmpeg")
def test_kenburns_video_clip(tmp_path):
    from app.adapters.image_mock import MockImage

    img = tmp_path / "kf.png"
    MockImage().run({"prompt": "x", "out_path": str(img), "width": 64, "height": 48})
    from app.adapters.video_kenburns import KenBurnsVideo

    out = tmp_path / "clip.mp4"
    res = KenBurnsVideo({"fps": 10}).run({"image_path": str(img), "out_path": str(out),
                                          "duration": 1.0, "motion": "zoom_in",
                                          "fps": 10, "width": 64, "height": 48})
    assert out.exists() and out.stat().st_size > 0
    assert 0.5 <= res["duration"] <= 3.0


def test_plugin_template_exists():
    """插件扩展入口存在（可拷贝的模板）。"""
    from pathlib import Path

    tpl = Path(__file__).parent.parent / "app/adapters/plugins/_TEMPLATE.py.example"
    assert tpl.exists() and "AdapterBase" in tpl.read_text("utf-8")


def test_heavy_dependencies_not_imported():
    """依赖最小性：导入全部适配器后，重依赖不得进入 sys.modules。"""
    import importlib

    for mod in ("app.adapters.llm_mock", "app.adapters.llm_ollama",
                "app.adapters.tts_mock", "app.adapters.tts_cosyvoice",
                "app.adapters.image_mock", "app.adapters.image_diffusers",
                "app.adapters.video_kenburns", "app.adapters.video_wan",
                "app.adapters.asr_script", "app.adapters.asr_funasr"):
        importlib.import_module(mod)
    for heavy in ("torch", "transformers", "diffusers", "modelscope", "funasr"):
        assert heavy not in sys.modules, f"{heavy} 被提前导入（违反惰性导入约定）"

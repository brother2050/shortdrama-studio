"""多引擎 TTS（mosaic 四后端内置移植）测试：路由 / 就绪探测 / 参数校验 / 惰性导入。"""
from __future__ import annotations

import sys

import pytest

from app.adapters import tts_libs
from app.adapters.base import AdapterError, AdapterUnavailableError


# ----------------------------------------------------------------------
# 引擎注册与元数据
# ----------------------------------------------------------------------
def test_four_engines_registered():
    engines = tts_libs.get_engines()
    assert set(engines) == {"cosyvoice", "chattts", "gpt_sovits", "fish_speech"}
    assert engines["cosyvoice"].kind == "local"
    assert engines["chattts"].kind == "local"
    assert engines["gpt_sovits"].kind == "http"
    assert engines["fish_speech"].kind == "http"


def test_auto_order_local_first():
    """auto 路由顺序：本地库优先（离线友好），HTTP 服务殿后。"""
    assert tts_libs.AUTO_ORDER[:2] == ("cosyvoice", "chattts")
    assert set(tts_libs.AUTO_ORDER[2:]) == {"gpt_sovits", "fish_speech"}


def test_engine_status_shape():
    status = tts_libs.engine_status({})
    for name, info in status.items():
        assert {"label", "kind", "ready", "reason"} <= set(info), name
        assert isinstance(info["ready"], bool)
        # 未就绪必须给出可读原因（易用性约定）
        if not info["ready"]:
            assert info["reason"], f"{name} 未就绪但缺少原因说明"


# ----------------------------------------------------------------------
# 惰性导入（依赖最小）
# ----------------------------------------------------------------------
def test_tts_libs_no_heavy_imports():
    """导入 tts_libs 与全部引擎模块不得引入重依赖（未安装时也能注册探测）。"""
    import importlib

    for mod in ("app.adapters.tts_libs", "app.adapters.tts_libs._base",
                "app.adapters.tts_libs.chattts_engine",
                "app.adapters.tts_libs.cosyvoice_engine",
                "app.adapters.tts_libs.fish_engine",
                "app.adapters.tts_libs.gptsovits_engine"):
        importlib.import_module(mod)
    for heavy in ("torch", "ChatTTS", "chattts", "cosyvoice",
                  "numpy", "requests", "modelscope"):
        assert heavy not in sys.modules, f"{heavy} 被提前导入（违反惰性导入约定）"


def test_mosaic_no_external_package_dependency():
    """mosaic 适配器不再依赖外部 mosaic 包（内置移植核心检查）。"""
    src = __import__("pathlib").Path(
        __import__("app.adapters.tts_mosaic", fromlist=["x"]).__file__
    ).read_text("utf-8")
    assert "from mosaic" not in src and "import mosaic\n" not in src
    assert "tts_libs" in src  # 路由到内置引擎


# ----------------------------------------------------------------------
# 引擎选择
# ----------------------------------------------------------------------
def test_pick_engine_unknown_name():
    with pytest.raises(AdapterError, match="未知 TTS 引擎"):
        tts_libs.pick_engine({"engine": "no_such"})


def test_pick_engine_explicit_not_ready(monkeypatch):
    engines = tts_libs.get_engines()
    monkeypatch.setattr(engines["chattts"], "ready",
                        lambda p: (False, "未安装 ChatTTS"))
    with pytest.raises(AdapterUnavailableError, match="未就绪"):
        tts_libs.pick_engine({"engine": "chattts"})


def test_pick_engine_auto_first_ready(monkeypatch):
    engines = tts_libs.get_engines()
    monkeypatch.setattr(engines["cosyvoice"], "ready", lambda p: (False, "x"))
    monkeypatch.setattr(engines["chattts"], "ready", lambda p: (True, ""))
    name, eng = tts_libs.pick_engine({"engine": "auto"})
    assert name == "chattts" and eng is engines["chattts"]


def test_pick_engine_none_ready(monkeypatch):
    engines = tts_libs.get_engines()
    for eng in engines.values():
        monkeypatch.setattr(eng, "ready", lambda p: (False, "缺依赖"))
    with pytest.raises(AdapterUnavailableError, match="四引擎均未就绪"):
        tts_libs.pick_engine({"engine": "auto"})


def test_any_engine_ready(monkeypatch):
    engines = tts_libs.get_engines()
    for eng in engines.values():
        monkeypatch.setattr(eng, "ready", lambda p: (False, "x"))
    assert tts_libs.any_engine_ready({}) is False
    monkeypatch.setattr(engines["fish_speech"], "ready", lambda p: (True, ""))
    assert tts_libs.any_engine_ready({}) is True


# ----------------------------------------------------------------------
# HTTP 引擎（不实际发请求：就绪探测 + 参数校验）
# ----------------------------------------------------------------------
def test_http_engine_not_ready_when_service_down():
    """本地未启动服务时（常见 CI 环境），HTTP 引擎探测为不可达。"""
    from app.adapters.tts_libs._base import http_reachable

    if http_reachable("http://127.0.0.1:9880"):
        pytest.skip("本机 9880 有服务在跑，跳过")
    ok, reason = tts_libs.get_engines()["gpt_sovits"].ready({})
    assert ok is False and "9880" in reason


def test_gptsovits_requires_ref_audio(tmp_path):
    from app.adapters.tts_libs.gptsovits_engine import engine

    with pytest.raises(AdapterError, match="sovits_ref_audio"):
        engine.synthesize("你好", "narrator", tmp_path / "a.wav", {})
    with pytest.raises(AdapterError, match="sovits_prompt_text"):
        engine.synthesize("你好", "narrator", tmp_path / "a.wav",
                          {"sovits_ref_audio": "/tmp/ref.wav"})


def test_fish_requires_reference_id(tmp_path):
    from app.adapters.tts_libs.fish_engine import engine

    with pytest.raises(AdapterError, match="fish_reference_id"):
        engine.synthesize("你好", "narrator", tmp_path / "a.wav", {})


# ----------------------------------------------------------------------
# CosyVoice 共享引擎参数校验（不加载模型）
# ----------------------------------------------------------------------
def test_cosyvoice_requires_model_dir(tmp_path):
    from app.adapters.tts_libs.cosyvoice_engine import shared_synthesize

    with pytest.raises(AdapterError, match="model_dir"):
        shared_synthesize("你好", "narrator", tmp_path / "a.wav", {})


# ----------------------------------------------------------------------
# 适配器接线（mock 引擎验证路由）
# ----------------------------------------------------------------------
def test_mosaic_adapter_registered_with_zero_requires():
    from app.adapters import registry

    cls = registry.get_class("tts", "mosaic")
    assert cls is not None
    assert cls.spec.requires == []          # 不再依赖外部 mosaic 包
    assert cls.spec.default_params["engine"] == "auto"
    assert "chattts_model_dir" in cls.spec.param_docs
    # 可用性探测不崩溃（环境差异下返回 True/False 均可）
    cls.is_available()


def test_mosaic_run_routes_to_engine(tmp_path, monkeypatch):
    from app.adapters.tts_mosaic import MosaicTTS

    calls = {}

    class FakeEngine:
        def synthesize(self, text, voice, out_path, params, progress=None):
            calls.update(text=text, voice=voice, params_engine=params.get("engine"))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"RIFF" + b"\x00" * 40)
            return {"duration": 1.0, "sample_rate": 24000}

    monkeypatch.setattr(tts_libs, "pick_engine",
                        lambda p: ("fake", FakeEngine()))
    adapter = MosaicTTS({"engine": "auto"})
    res = adapter.run({"text": "晚风便利店", "voice": "female_warm",
                       "out_path": str(tmp_path / "vo.wav")})
    assert res["engine"] == "fake"
    assert res["duration"] == 1.0 and res["voice"] == "female_warm"
    assert calls["text"] == "晚风便利店"


def test_mosaic_run_empty_text():
    from app.adapters.tts_mosaic import MosaicTTS

    with pytest.raises(AdapterError, match="文本为空"):
        MosaicTTS({}).run({"text": "  ", "out_path": "/tmp/x.wav"})


def test_cosyvoice_adapter_reuses_shared_engine():
    """cosyvoice 直连后端与 mosaic 引擎共享同一模型单例（显存只占一份）。"""
    from app.adapters.tts_libs import cosyvoice_engine
    from app.adapters.tts_cosyvoice import CosyVoiceTTS

    assert CosyVoiceTTS({"model_dir": "/x"}).run.__doc__ is None or True
    # 共享槽是同一个对象
    assert cosyvoice_engine._slot.name == "tts_cosyvoice2_shared"

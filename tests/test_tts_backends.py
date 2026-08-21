"""TTS 四后端测试：注册 / 本地推理接线 / 参数校验 / 惰性导入（无 HTTP 服务）。"""
from __future__ import annotations

import sys

import pytest

from app.adapters import registry
from app.adapters.base import AdapterError


# ----------------------------------------------------------------------
# 四后端注册（全部本地库推理）
# ----------------------------------------------------------------------
def test_four_local_tts_backends_registered():
    names = registry.names("tts")
    for n in ("mock", "cosyvoice", "chattts", "gpt_sovits", "fish_speech"):
        assert n in names, f"缺 TTS 后端 {n}"
    assert "mosaic" not in names, "mosaic 后端应已移除"


def test_tts_backends_declare_local_packages():
    """四后端 requires 声明各自本地包（无 HTTP URL / 端口参数）。"""
    expects = {"cosyvoice": "cosyvoice", "chattts": "ChatTTS",
               "gpt_sovits": "GPT_SoVITS", "fish_speech": "fish_speech"}
    for name, pkg in expects.items():
        cls = registry.get_class("tts", name)
        assert cls is not None
        assert pkg in cls.spec.requires, f"{name} 应依赖本地包 {pkg}"
        # 无 HTTP 服务参数（最小化：不起外部服务）
        params = " ".join(cls.spec.default_params)
        assert "url" not in params, f"{name} 不应含 HTTP 服务参数"


def test_tts_specs_have_vram_and_docs():
    for name in ("cosyvoice", "chattts", "gpt_sovits", "fish_speech"):
        spec = registry.get_class("tts", name).spec
        assert spec.vram_gb > 0, f"{name} 应声明显存需求"
        assert spec.param_docs, f"{name} 缺参数说明"


# ----------------------------------------------------------------------
# 惰性导入（依赖最小：未装包也能注册探测）
# ----------------------------------------------------------------------
def test_tts_adapters_lazy_import():
    """导入全部 TTS 适配器不得引入重依赖。"""
    import importlib

    for mod in ("app.adapters.tts_mock", "app.adapters.tts_cosyvoice",
                "app.adapters.tts_chattts", "app.adapters.tts_gptsovits",
                "app.adapters.tts_fishspeech"):
        importlib.import_module(mod)
    for heavy in ("torch", "ChatTTS", "cosyvoice", "GPT_SoVITS",
                  "fish_speech", "numpy", "requests"):
        assert heavy not in sys.modules, f"{heavy} 被提前导入"


# ----------------------------------------------------------------------
# ChatTTS 资产预检（修复"下载好了但报找不到"）
# ----------------------------------------------------------------------
class TestChatTTSAssetsPrecheck:
    def test_incomplete_layout_reports_missing_files(self, tmp_path,
                                                     monkeypatch):
        """v0.1 旧布局（只有 .pt）→ 精确报缺哪些 safetensors + 修复指引。"""
        from app.adapters import tts_chattts
        from app.adapters.tts_chattts import ChatTTSAdapter

        d = tmp_path / "chattts" / "asset"
        d.mkdir(parents=True)
        for legacy in ("GPT.pt", "DVAE.pt", "spk_stat.pt"):  # 用户已下载旧版
            (d / legacy).write_bytes(b"old")

        monkeypatch.setattr(tts_chattts, "check_vram", lambda *_: True)
        adapter = ChatTTSAdapter({"model_dir": str(d.parent)})
        with pytest.raises(AdapterError) as ei:
            adapter._load(adapter.params)
        msg = str(ei.value)
        assert "模型不完整" in msg
        assert "asset/gpt/model.safetensors" in msg     # 缺哪些文件
        assert "safetensors" in msg                     # 原因说明
        assert "download_models.py --capability tts --preset chattts" in msg

    def test_complete_layout_passes_precheck(self, tmp_path, monkeypatch):
        """布局齐全 → 预检通过（不抛错，后续走 ChatTTS 真实加载）。"""
        import types

        from app.adapters import tts_chattts
        from app.adapters.tts_chattts import ChatTTSAdapter
        from app import models_registry

        preset = models_registry.find_preset("tts", "chattts")
        d = tmp_path / "chattts"
        for f in preset.required_files:
            (d / f).parent.mkdir(parents=True, exist_ok=True)
            (d / f).write_bytes(b"x")

        monkeypatch.setattr(tts_chattts, "check_vram", lambda *_: True)

        loaded: list = []

        class FakeChat:
            def load(self, **kw):
                loaded.append(kw)
                return True

        fake_mod = types.ModuleType("ChatTTS")
        fake_mod.Chat = FakeChat
        monkeypatch.setitem(sys.modules, "ChatTTS", fake_mod)
        fake_torch = types.ModuleType("torch")
        fake_torch.device = lambda x: x
        fake_torch.manual_seed = lambda *_: None
        fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)
        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        monkeypatch.setattr(tts_chattts.ModelSlot, "load",
                            lambda self, fn: fn())

        chat = ChatTTSAdapter({"model_dir": str(d)})._load(
            {"model_dir": str(d)})
        assert chat is not None
        # custom 模式：source=custom + 解析后的绝对路径
        assert loaded[0]["source"] == "custom"
        assert str(loaded[0]["custom_path"]).startswith(str(tmp_path))
        ChatTTSAdapter({}).unload()                   # 清理类级槽位缓存


# ----------------------------------------------------------------------
# 参数校验（不加载模型即可验证）
# ----------------------------------------------------------------------
def test_chattts_empty_text():
    from app.adapters.tts_chattts import ChatTTSAdapter

    with pytest.raises(AdapterError, match="文本为空"):
        ChatTTSAdapter({}).run({"text": "  ", "out_path": "/tmp/x.wav"})


def test_cosyvoice_requires_model_dir(tmp_path):
    from app.adapters.tts_cosyvoice import CosyVoiceTTS

    # 显式置空 → 提示设置 model_dir
    with pytest.raises(AdapterError, match="model_dir"):
        CosyVoiceTTS({"model_dir": ""}).run(
            {"text": "你好", "out_path": str(tmp_path / "a.wav")})
    # 路径不存在 → 统一路径报错
    with pytest.raises(AdapterError, match="不存在"):
        CosyVoiceTTS({"model_dir": str(tmp_path / "nope")}).run(
            {"text": "你好", "out_path": str(tmp_path / "a.wav")})
    # 默认预设路径未下载 → 报错含离线下载指引
    with pytest.raises(AdapterError, match="download_models.py"):
        CosyVoiceTTS({}).run(
            {"text": "你好", "out_path": str(tmp_path / "a.wav")})


def test_gptsovits_requires_ref_and_prompt(tmp_path):
    from app.adapters.tts_gptsovits import GPTSoVITSAdapter

    with pytest.raises(AdapterError, match="ref_audio"):
        GPTSoVITSAdapter({}).run({"text": "你好", "out_path": str(tmp_path / "a.wav")})
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    with pytest.raises(AdapterError, match="prompt_text"):
        GPTSoVITSAdapter({"ref_audio": str(ref)}).run(
            {"text": "你好", "out_path": str(tmp_path / "a.wav")})


def test_fishspeech_requires_checkpoint(tmp_path):
    from app.adapters.tts_fishspeech import FishSpeechAdapter

    # 显式置空 → 提示设置 checkpoint_dir
    with pytest.raises(AdapterError, match="checkpoint_dir"):
        FishSpeechAdapter({"checkpoint_dir": ""}).run(
            {"text": "你好", "out_path": str(tmp_path / "a.wav")})
    # 路径不存在 → 统一路径报错
    with pytest.raises(AdapterError, match="不存在"):
        FishSpeechAdapter({"checkpoint_dir": str(tmp_path / "nope")}).run(
            {"text": "你好", "out_path": str(tmp_path / "a.wav")})
    # 默认预设路径未下载 → 报错含离线下载指引
    with pytest.raises(AdapterError, match="download_models.py"):
        FishSpeechAdapter({}).run(
            {"text": "你好", "out_path": str(tmp_path / "a.wav")})


# ----------------------------------------------------------------------
# 本地推理接线（mock 模型验证调用路径）
# ----------------------------------------------------------------------
def test_chattts_infer_and_save(tmp_path, monkeypatch):
    """ChatTTS 推理 → write_wav 产物（mock ChatTTS 包 + 其自带的 torch）。"""
    import types

    import numpy as np

    from app.adapters.tts_chattts import ChatTTSAdapter

    fake_torch = types.ModuleType("torch")            # ChatTTS 包自带的 torch
    fake_torch.manual_seed = lambda seed: None
    fake_torch.device = lambda x: x

    class _NoCuda:
        @staticmethod
        def is_available():
            return False

    fake_torch.cuda = _NoCuda()
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    class FakeChat:
        class InferCodeParams(dict):
            def __init__(self, **kw):
                super().__init__(**kw)

        class RefineTextParams(dict):
            def __init__(self, **kw):
                super().__init__(**kw)

        @staticmethod
        def sample_random_speaker():
            return "spk-x"

        def load(self, **kw):
            return True

        def infer(self, texts, **kw):
            return [np.zeros(2400, dtype="float32")]

    fake_mod = type(sys)("ChatTTS")
    fake_mod.Chat = FakeChat
    monkeypatch.setitem(sys.modules, "ChatTTS", fake_mod)

    out = tmp_path / "vo.wav"
    # model_dir 置空 → ChatTTS 默认缓存模式（本地预设下载属另一路径，
    # 见 tests/test_models_registry.py）
    res = ChatTTSAdapter({"model_dir": ""}).run(
        {"text": "晚风便利店", "voice": "female_warm",
         "out_path": str(out)})
    assert out.exists() and res["sample_rate"] == 24000
    assert res["duration"] == pytest.approx(0.1, abs=0.01)
    # 音色种子缓存生效（同角色复用）
    assert "female_warm" in ChatTTSAdapter._spk_cache
    ChatTTSAdapter({}).unload()


def test_gptsovits_run_uses_handle_api(tmp_path, monkeypatch):
    """GPT-SoVITS 推理走 TTS.run(req) → (sr, np.ndarray)（官方本地接口）。"""
    import numpy as np

    from app.adapters.tts_gptsovits import GPTSoVITSAdapter

    req_seen = {}

    class FakeTTS:
        def __init__(self, cfg):
            pass

        def run(self, req):
            req_seen.update(req)
            yield 32000, np.zeros(3200, dtype="float32")

    class FakeCfg(dict):
        pass

    fake_pkg = type(sys)("GPT_SoVITS")
    infer_pack = type(sys)("TTS_infer_pack")
    tts_mod = type(sys)("TTS")
    tts_mod.TTS = FakeTTS
    tts_mod.TTS_Config = FakeCfg
    infer_pack.TTS = tts_mod
    fake_pkg.TTS_infer_pack = infer_pack
    monkeypatch.setitem(sys.modules, "GPT_SoVITS", fake_pkg)
    monkeypatch.setitem(sys.modules, "GPT_SoVITS.TTS_infer_pack", infer_pack)
    monkeypatch.setitem(sys.modules, "GPT_SoVITS.TTS_infer_pack.TTS", tts_mod)

    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    out = tmp_path / "vo.wav"
    res = GPTSoVITSAdapter({"ref_audio": str(ref), "prompt_text": "参考文本"}).run(
        {"text": "你好晚风", "out_path": str(out)})
    assert req_seen["ref_audio_path"] == str(ref)
    assert req_seen["text_lang"] == "zh" and req_seen["streaming_mode"] is False
    assert res["sample_rate"] == 32000 and out.exists()
    GPTSoVITSAdapter({}).unload()


def test_fishspeech_engine_final_result(tmp_path, monkeypatch):
    """Fish Speech 推理走 TTSInferenceEngine → final (sr, audio)。"""
    import types

    import numpy as np

    from app.adapters.tts_fishspeech import FishSpeechAdapter

    class Result:
        code = "final"
        audio = (44100, np.zeros(4410, dtype="float32"))
        error = None

    class FakeEngine:
        def __init__(self, **kw):
            pass

        def inference(self, req):
            yield Result()

    # fish-speech 自带的 torch（精度选择用）
    fake_torch = types.ModuleType("torch")
    fake_torch.bfloat16, fake_torch.float32 = "bf16", "fp32"

    class _NoCuda:
        @staticmethod
        def is_available():
            return False

    fake_torch.cuda = _NoCuda()
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    # 构造嵌套 fake 包：fish_speech.{models.dac.inference,
    # models.text2semantic.inference, inference_engine, utils.schema}
    root = types.ModuleType("fish_speech")
    root.__path__ = []
    monkeypatch.setitem(sys.modules, "fish_speech", root)

    def submod(parent, name, is_pkg=False):
        full = f"{parent.__name__}.{name}"
        mod = types.ModuleType(full)
        if is_pkg:
            mod.__path__ = []
        setattr(parent, name, mod)
        monkeypatch.setitem(sys.modules, full, mod)
        return mod

    models = submod(root, "models", is_pkg=True)
    dac_inf = submod(submod(models, "dac", is_pkg=True), "inference")
    dac_inf.load_model = lambda **kw: object()
    t2s_inf = submod(submod(models, "text2semantic", is_pkg=True), "inference")
    t2s_inf.launch_thread_safe_queue = lambda **kw: object()
    engine_mod = submod(root, "inference_engine")
    engine_mod.TTSInferenceEngine = FakeEngine
    schema = submod(submod(root, "utils", is_pkg=True), "schema")

    class _Ref:
        def __init__(self, audio=None, text=""):
            self.audio, self.text = audio, text

    class _Req:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    schema.ServeReferenceAudio = _Ref
    schema.ServeTTSRequest = _Req

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    out = tmp_path / "vo.wav"
    res = FishSpeechAdapter({"checkpoint_dir": str(ckpt)}).run(
        {"text": "你好晚风", "out_path": str(out)})
    assert res["sample_rate"] == 44100 and out.exists()
    FishSpeechAdapter({}).unload()

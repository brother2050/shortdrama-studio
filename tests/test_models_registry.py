"""模型目录统一规范测试：注册表 / 路径解析 / 下载脚本 / API / 适配器。

核心约定（本次整改目标）：
1. 所有离线模型统一存放在项目根 ``models/``（STUDIO_MODELS_DIR 可覆盖）；
2. 布局规范：``models/<能力>/<预设名>/`` + ``models/<能力>/_shared/<组件>/``；
3. 预设名 kebab-case，杜绝"名字千奇百怪"；
4. 相对路径一律相对**项目根**解析（与 cwd 无关），杜绝"目录在各种地方"；
5. ``GET /api/system/models`` 为设置页预设下拉 + JSON 自动填充提供数据；
6. 适配器本地直载优先：``models/<cap>/<preset>/`` 存在即完全离线加载。

测试不依赖 torch/modelscope/diffsynth 真实安装（用替身模块注入）。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import models_registry, paths
from app.adapters.model_paths import (ModelPathError,
                                      ensure_diffsynth_base_path,
                                      model_source, resolve_model_path)
from app.main import app

client = TestClient(app)


def _fake_torch(monkeypatch):
    """注入 torch 替身（_do_load 只用 dtype 常量与 OOM 异常类）。"""
    fake = types.ModuleType("torch")
    fake.bfloat16 = "bfloat16"
    fake.float32 = "float32"
    fake.cuda = types.SimpleNamespace(
        OutOfMemoryError=type("OOM", (RuntimeError,), {}))
    monkeypatch.setitem(sys.modules, "torch", fake)
    return fake


def _fake_modelscope(monkeypatch, recorder: list):
    """注入 modelscope 替身：snapshot_download 落盘占位文件并记账。"""

    def fake_snapshot(repo_id, local_dir=None, **kwargs):
        recorder.append((repo_id, str(local_dir)))
        d = Path(local_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "placeholder.bin").write_bytes(b"x")
        return str(d)

    fake = types.ModuleType("modelscope")
    fake.snapshot_download = fake_snapshot
    monkeypatch.setitem(sys.modules, "modelscope", fake)
    return fake


def _fake_diffsynth(monkeypatch, pipe_module: str, pipe_class: str) -> dict:
    """注入 diffsynth 替身（core.ModelConfig + 对应 pipeline 类）。

    返回 holder dict：``holder["kw"]`` 为 FakePipe.from_pretrained 捕获的
    最近一次 kwargs（断言 model_configs / tokenizer_config 用）。
    """
    created: list = []

    class FakeModelConfig:
        def __init__(self, path=None, model_id=None,
                     origin_file_pattern=None, **kw):
            self.path = path
            self.model_id = model_id
            self.pattern = origin_file_pattern
            created.append(self)

    holder: dict = {}

    class FakePipe:
        @classmethod
        def from_pretrained(cls, **kw):
            holder["kw"] = kw
            return cls()

    core = types.ModuleType("diffsynth.core")
    core.ModelConfig = FakeModelConfig
    monkeypatch.setitem(sys.modules, "diffsynth.core", core)

    mod = types.ModuleType(pipe_module)
    setattr(mod, pipe_class, FakePipe)
    monkeypatch.setitem(sys.modules, pipe_module, mod)
    return holder


@pytest.fixture(autouse=True)
def isolated_models_dir(tmp_path, monkeypatch):
    """每个测试独立的模型根目录。"""
    root = tmp_path / "models"
    monkeypatch.setenv("STUDIO_MODELS_DIR", str(root))
    yield root


# ----------------------------------------------------------------------
# 注册表
# ----------------------------------------------------------------------
class TestRegistry:
    def test_covers_all_capabilities(self):
        assert set(models_registry.REGISTRY) == {"llm", "tts", "image",
                                                 "video", "asr"}

    def test_preset_names_are_kebab_case(self):
        """预设名（= 目录名）必须是 kebab-case，无空格/大写/中文。"""
        for cap, presets in models_registry.REGISTRY.items():
            for name in presets:
                assert name == name.lower()
                assert " " not in name
                assert all(c.isalnum() or c in ".-" for c in name)

    def test_every_preset_has_backend_and_params(self):
        for presets in models_registry.REGISTRY.values():
            for p in presets.values():
                assert p.backend, f"{p.capability}/{p.name} 缺 backend"
                assert isinstance(p.params, dict), \
                    f"{p.capability}/{p.name} params 必须是 dict"
                import json
                json.dumps(p.params)   # JSON 可序列化（前端自动填充用）

    def test_path_params_are_relative_models(self):
        """模板里的路径参数一律是 models/ 相对路径（跨机器可移植）。"""
        for presets in models_registry.REGISTRY.values():
            for p in presets.values():
                for key in ("model_id", "model_dir", "model_path",
                            "checkpoint_dir", "model"):
                    val = p.params.get(key)
                    if isinstance(val, str) and val:
                        assert val.startswith("models/"), \
                            f"{p.capability}/{p.name}.{key} 应为 models/ 相对路径"

    def test_shared_components_live_under_capability(self):
        for presets in models_registry.REGISTRY.values():
            for p in presets.values():
                for s in p.shared:
                    assert s.into.startswith(f"{p.capability}/_shared/")

    def test_find_preset(self):
        hit = models_registry.find_preset("llm", "qwen2.5-1.5b")
        assert hit is not None and hit.repo_id == "qwen/Qwen2.5-1.5B-Instruct"
        assert models_registry.find_preset("llm", "不存在") is None

    def test_is_downloaded(self, isolated_models_dir):
        p = models_registry.find_preset("llm", "qwen2.5-1.5b")
        assert p.is_downloaded() is False           # 未下载
        d = isolated_models_dir / "llm" / "qwen2.5-1.5b"
        d.mkdir(parents=True)
        assert p.is_downloaded() is False           # 空目录不算
        (d / "config.json").write_text("{}", "utf-8")
        assert p.is_downloaded() is True

    def test_catalog_structure(self):
        cat = models_registry.catalog()
        assert Path(cat["models_root"]) == paths.models_root()
        for cap, items in cat["capabilities"].items():
            assert items
            for it in items:
                assert {"name", "repo_id", "size_gb", "desc", "backend",
                        "params", "dir_rel", "downloaded",
                        "download_command", "default"} <= set(it)


# ----------------------------------------------------------------------
# 统一路径解析
# ----------------------------------------------------------------------
class TestResolveModelPath:
    def test_empty_without_preset_returns_none(self):
        assert resolve_model_path("", "llm") is None
        assert resolve_model_path(None, "tts") is None

    def test_preset_name_resolves_to_models_layout(self, isolated_models_dir):
        d = isolated_models_dir / "llm" / "qwen2.5-1.5b"
        d.mkdir(parents=True)
        assert resolve_model_path("qwen2.5-1.5b", "llm") == d

    def test_relative_path_resolved_against_repo_root(self, monkeypatch,
                                                      tmp_path):
        """相对路径相对项目根（非 cwd）解析——修复"目录千奇百怪"的关键。"""
        fake_root = tmp_path / "fake-repo"
        target = fake_root / "models" / "llm" / "qwen2.5-1.5b"
        target.mkdir(parents=True)
        monkeypatch.setattr(paths, "REPO_ROOT", fake_root)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)     # cwd 在项目外，仍应解析到项目根
        assert resolve_model_path("models/llm/qwen2.5-1.5b", "llm") == target

    def test_absolute_and_home_path(self, isolated_models_dir, monkeypatch):
        d = isolated_models_dir / "tts" / "cosyvoice2-0.5b"
        d.mkdir(parents=True)
        assert resolve_model_path(str(d), "tts") == d
        # ~ 展开
        monkeypatch.setenv("HOME", str(isolated_models_dir.parent))
        home = Path("~/_mymodel").expanduser()
        home.mkdir()
        assert resolve_model_path("~/_mymodel", "llm") == home

    def test_trailing_slash_and_backslash_normalized(self,
                                                     isolated_models_dir):
        d = isolated_models_dir / "llm" / "qwen2.5-1.5b"
        d.mkdir(parents=True)
        assert resolve_model_path("models/llm/qwen2.5-1.5b/", "llm") == d
        assert resolve_model_path("models\\llm\\qwen2.5-1.5b\\", "llm") == d

    def test_missing_raises_with_download_hint(self):
        with pytest.raises(ModelPathError) as ei:
            resolve_model_path("models/llm/none-exists", "llm")
        msg = str(ei.value)
        assert "不存在" in msg and "download_models.py" in msg \
            and "qwen2.5-1.5b" in msg          # 提示可选预设

    def test_model_source_local_vs_online(self, isolated_models_dir):
        d = isolated_models_dir / "asr" / "sensevoice-small"
        d.mkdir(parents=True)
        (d / "model.bin").write_bytes(b"x")    # 空目录不算已下载
        src, local = model_source("sensevoice-small", "asr")
        assert local and src == str(d)
        src, local = model_source("models/asr/sensevoice-small", "asr")
        assert local and src == str(d)
        # 在线仓库 id（org/repo 形式且本地不存在）
        src, local = model_source("qwen/Qwen2.5-7B-Instruct", "llm")
        assert not local and src == "qwen/Qwen2.5-7B-Instruct"

    def test_model_source_invalid_raises(self):
        with pytest.raises(ModelPathError):
            model_source("models/llm/none-exists", "llm")   # 不允许在线回退
        with pytest.raises(ModelPathError):
            model_source("", "llm")                          # 空值无 preset

    def test_ensure_diffsynth_base_path_anchors_models_root(
            self, isolated_models_dir, monkeypatch):
        import os
        monkeypatch.delenv("DIFFSYNTH_MODEL_BASE_PATH", raising=False)
        ensure_diffsynth_base_path()
        assert os.environ["DIFFSYNTH_MODEL_BASE_PATH"] == str(
            paths.models_root())
        os.environ.pop("DIFFSYNTH_MODEL_BASE_PATH", None)   # 不污染后续测试


# ----------------------------------------------------------------------
# 下载脚本
# ----------------------------------------------------------------------
class TestDownloadScript:
    def test_list_runs(self, capsys):
        from scripts import download_models as dm
        assert dm.main(["--list"]) == 0
        out = capsys.readouterr().out
        assert "qwen2.5-1.5b" in out and "wan2.2-ti2v-5b" in out

    def test_models_root_defaults_to_repo_root(self, monkeypatch):
        """无 STUDIO_MODELS_DIR 时，模型根 = 项目根/models（与 cwd 无关）。"""
        monkeypatch.delenv("STUDIO_MODELS_DIR", raising=False)
        assert paths.models_root() == paths.REPO_ROOT / "models"

    def test_unknown_preset_fails_fast(self):
        from scripts import download_models as dm
        assert dm.main(["--capability", "llm", "--preset", "nope"]) == 2

    def test_download_writes_shared_components(self, isolated_models_dir,
                                               monkeypatch):
        """下载预设时本体与共享组件都落到规范布局。"""
        from scripts import download_models as dm

        calls: list = []
        _fake_modelscope(monkeypatch, calls)

        preset = models_registry.find_preset("video", "wan2.2-ti2v-5b")
        assert dm.download(preset, isolated_models_dir) is not None
        # 预设本体
        assert (isolated_models_dir / "video" / "wan2.2-ti2v-5b"
                / "placeholder.bin").exists()
        # 共享 tokenizer
        assert (isolated_models_dir / "video" / "_shared" / "umt5-xxl"
                / "placeholder.bin").exists()
        repos = [r for r, _ in calls]
        assert "Wan-AI/Wan2.1-T2V-1.3B" in repos     # 共享组件源
        assert "Wan-AI/Wan2.2-TI2V-5B" in repos      # 预设本体

    def test_shared_components_downloaded_once(self, isolated_models_dir,
                                               monkeypatch):
        """两个预设共用 umt5-xxl 时只下载一次（第二次命中跳过）。"""
        from scripts import download_models as dm

        calls: list = []
        _fake_modelscope(monkeypatch, calls)

        p1 = models_registry.find_preset("video", "wan2.2-ti2v-5b")
        p2 = models_registry.find_preset("video", "wan2.1-t2v-1.3b")
        dm.download(p1, isolated_models_dir)
        dm.download(p2, isolated_models_dir)
        umt5_calls = [d for _, d in calls
                      if d.endswith("video/_shared/umt5-xxl")]
        assert len(umt5_calls) == 1


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
class TestModelsAPI:
    def test_models_catalog_endpoint(self):
        r = client.get("/api/system/models")
        assert r.status_code == 200
        data = r.json()
        assert "models_root" in data
        for cap in ("llm", "tts", "image", "video", "asr"):
            assert data["capabilities"][cap], f"{cap} 无预设"
        # 预设含自动填充模板与已下载状态
        item = data["capabilities"]["llm"][0]
        assert "params" in item and "downloaded" in item

    def test_downloaded_flag_reflects_disk(self, isolated_models_dir):
        d = isolated_models_dir / "asr" / "sensevoice-small"
        d.mkdir(parents=True)
        (d / "model.bin").write_bytes(b"x")
        data = client.get("/api/system/models").json()
        items = {i["name"]: i for i in data["capabilities"]["asr"]}
        assert items["sensevoice-small"]["downloaded"] is True


# ----------------------------------------------------------------------
# 适配器：本地直载优先（DiffSynth path= 模式）
# ----------------------------------------------------------------------
class TestDiffSynthLocalMode:
    """models/<cap>/<preset>/ 存在时走 ModelConfig(path=...) 本地直载。"""

    def test_image_local_mode_uses_path(self, isolated_models_dir,
                                        monkeypatch):
        from app.adapters import image_diffsynth

        d = isolated_models_dir / "image" / "sdxl"
        for sub in ("text_encoder", "text_encoder_2", "unet", "vae",
                    "tokenizer"):
            (d / sub).mkdir(parents=True)
        (d / "text_encoder" / "model.safetensors").write_bytes(b"x")
        (d / "text_encoder_2" / "model.safetensors").write_bytes(b"x")
        (d / "unet" / "diffusion_pytorch_model.safetensors").write_bytes(b"x")
        (d / "vae" / "diffusion_pytorch_model.safetensors").write_bytes(b"x")
        (d / "tokenizer" / "tokenizer.json").write_text("{}", "utf-8")

        _fake_torch(monkeypatch)
        holder = _fake_diffsynth(monkeypatch,
                                 "diffsynth.pipelines.stable_diffusion_xl",
                                 "StableDiffusionXLPipeline")
        monkeypatch.setattr(image_diffsynth, "check_vram", lambda *_: True)
        monkeypatch.setattr(image_diffsynth, "pick_device",
                            lambda *_a, **_k: "cpu")
        monkeypatch.setattr(image_diffsynth.ModelSlot, "load",
                            lambda self, fn: fn())

        image_diffsynth.DiffSynthImage({"model_preset": "sdxl"})._load()

        kw = holder["kw"]
        configs = kw["model_configs"]
        assert len(configs) == 4                     # sdxl 四组件
        # 全部为本地 path 模式（无 model_id 在线下载）
        for mc in configs:
            assert mc.path and not mc.model_id
        assert str(kw["tokenizer_config"].path).endswith("tokenizer")
        # 路径都落在统一模型根目录下
        for mc in configs:
            assert str(mc.path).startswith(str(isolated_models_dir))

    def test_video_local_mode_uses_path(self, isolated_models_dir,
                                        monkeypatch):
        from app.adapters import video_diffsynth

        d = isolated_models_dir / "video" / "wan2.2-ti2v-5b"
        d.mkdir(parents=True)
        (d / "diffusion_pytorch_model-00001-of-00002.safetensors") \
            .write_bytes(b"x")
        (d / "diffusion_pytorch_model-00002-of-00002.safetensors") \
            .write_bytes(b"x")
        (d / "models_t5_umt5-xxl-enc-bf16.pth").write_bytes(b"x")
        (d / "Wan2.2_VAE.pth").write_bytes(b"x")
        umt5 = isolated_models_dir / "video" / "_shared" / "umt5-xxl"
        umt5.mkdir(parents=True)
        (umt5 / "spiece.model").write_bytes(b"x")

        _fake_torch(monkeypatch)
        holder = _fake_diffsynth(monkeypatch, "diffsynth.pipelines.wan_video",
                                 "WanVideoPipeline")
        monkeypatch.setattr(video_diffsynth, "check_vram", lambda *_: True)
        monkeypatch.setattr(video_diffsynth, "pick_device",
                            lambda *_a, **_k: "cpu")
        monkeypatch.setattr(video_diffsynth.ModelSlot, "load",
                            lambda self, fn: fn())

        video_diffsynth.DiffSynthWanVideo(
            {"model_preset": "wan2.2-ti2v-5b"})._load()

        kw = holder["kw"]
        configs = kw["model_configs"]
        assert len(configs) == 3                     # diffusion + t5 + vae
        for c in configs:
            assert c.path and not c.model_id        # 全本地 path 模式
        assert str(kw["tokenizer_config"].path) == str(umt5)
        # 分片 safetensors 合并为一个 path 列表
        diffusion = [c for c in configs if isinstance(c.path, list)]
        assert len(diffusion) == 1 and len(diffusion[0].path) == 2
        # 所有文件都在统一模型根目录下
        for c in configs:
            plist = c.path if isinstance(c.path, list) else [c.path]
            for p in plist:
                assert str(p).startswith(str(isolated_models_dir))

    def test_missing_local_file_raises_with_hint(self, isolated_models_dir,
                                                 monkeypatch):
        from app.adapters import video_diffsynth

        d = isolated_models_dir / "video" / "wan2.2-ti2v-5b"
        d.mkdir(parents=True)
        (d / "placeholder").write_bytes(b"x")        # 目录非空但缺权重文件

        _fake_torch(monkeypatch)
        _fake_diffsynth(monkeypatch, "diffsynth.pipelines.wan_video",
                        "WanVideoPipeline")
        monkeypatch.setattr(video_diffsynth, "check_vram", lambda *_: True)
        monkeypatch.setattr(video_diffsynth, "pick_device",
                            lambda *_a, **_k: "cpu")

        adapter = video_diffsynth.DiffSynthWanVideo(
            {"model_preset": "wan2.2-ti2v-5b"})
        from app.adapters.base import AdapterError
        with pytest.raises(AdapterError) as ei:
            adapter._load()
        assert "download_models.py" in str(ei.value)   # 报错含下载指引


# ----------------------------------------------------------------------
# 适配器：直载型后端（llm/tts/asr）的统一解析
# ----------------------------------------------------------------------
class TestAdapterResolution:
    def test_llm_modelscope_local_preferred(self, isolated_models_dir):
        from app.adapters.llm_modelscope import ModelScopeLLM
        d = isolated_models_dir / "llm" / "qwen2.5-1.5b"
        d.mkdir(parents=True)
        adapter = ModelScopeLLM({"model_id": "models/llm/qwen2.5-1.5b"})
        src, local = adapter._resolve_source()
        assert local and src == str(d)

    def test_llm_transformers_accepts_preset_name(self, isolated_models_dir):
        from app.adapters.llm_transformers import TransformersQwenLLM
        d = isolated_models_dir / "llm" / "qwen2.5-0.5b"
        d.mkdir(parents=True)
        adapter = TransformersQwenLLM({"model_path": "qwen2.5-0.5b"})
        assert adapter._resolve_path() == str(d)

    def test_tts_cosyvoice_missing_dir_hint(self):
        from app.adapters.base import AdapterError
        from app.adapters.tts_cosyvoice import CosyVoiceTTS
        adapter = CosyVoiceTTS({"model_dir": "models/tts/cosyvoice2-0.5b"})
        with pytest.raises(AdapterError) as ei:
            adapter._load(adapter.params)
        assert "download_models.py" in str(ei.value)

    def test_asr_funasr_local(self, isolated_models_dir):
        """funasr 的 model 参数本地优先（只验证解析层，不需装 funasr）。"""
        d = isolated_models_dir / "asr" / "sensevoice-small"
        d.mkdir(parents=True)
        (d / "model.bin").write_bytes(b"x")
        src, local = model_source("sensevoice-small", "asr")
        assert local and src == str(d)

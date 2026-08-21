"""DiffSynth-Studio 增强测试：对照官方 examples 的参数正确性 + 流水线新能力。

覆盖三块：
1. 视频后端：参数名（input_image）、文件 pattern、预设表（Wan2.2-TI2V-5B /
   FLF2V）与官方 examples 一致；
2. 图像后端：Qwen-Image / Qwen-Image-Edit 预设、edit_image 列表约定；
3. 流水线：角色参考图（worldview→keyframes 传递）、镜头过渡（clips 传
   end_image_path）、设置校验与对话指令解析。

静态断言（读源码/规格）保证无 GPU 环境也能锁定契约不回退。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters import registry

_ADAPTERS_DIR = Path(__file__).parent.parent / "app" / "adapters"


# ----------------------------------------------------------------------
# 视频后端：参数与预设对照官方 examples
# ----------------------------------------------------------------------
def test_video_adapter_uses_input_image_param():
    """官方 examples 用 input_image（I2V）/input_image+end_image（FLF2V），
    不得出现 vace_reference_image（VACE 专用，普通 I2V 会直接报错）。"""
    src = (_ADAPTERS_DIR / "video_diffsynth.py").read_text("utf-8")
    assert "input_image" in src
    assert "vace_reference_image" not in src
    # FLF2V 过渡：end_image + sigma_shift=16（对照 Wan2.1-FLF2V-14B-720P.py）
    assert "end_image" in src and "sigma_shift" in src


def test_video_adapter_file_patterns_match_official_examples():
    """文件 pattern 必须与官方 examples 一致（T5 是 bf16 编码器文件、
    diffusion 权重在仓库根目录而非 models/ 子目录）。"""
    from app.adapters.video_diffsynth import _MODEL_PRESETS

    for conf in _MODEL_PRESETS.values():
        assert "models_t5_umt5-xxl-enc-bf16.pth" in conf["files"]
        assert any(p.startswith("diffusion_pytorch_model")
                   for p in conf["files"])
        # 旧错误 pattern 不得回归
        for p in conf["files"]:
            assert not p.startswith("models/"), f"错误前缀 models/: {p}"


def test_video_adapter_presets_cover_new_models():
    from app.adapters.video_diffsynth import _MODEL_PRESETS

    assert "wan2.2-ti2v-5b" in _MODEL_PRESETS          # 单模型 T2V+I2V
    assert "wan2.1-flf2v-14b" in _MODEL_PRESETS        # 首尾帧过渡
    assert _MODEL_PRESETS["wan2.2-ti2v-5b"]["model_id"] == "Wan-AI/Wan2.2-TI2V-5B"
    assert _MODEL_PRESETS["wan2.1-flf2v-14b"]["flf2v"] is True
    # FLF2V 额外需要 CLIP 权重（对照官方 example 文件列表）
    assert any("clip" in p for p in _MODEL_PRESETS["wan2.1-flf2v-14b"]["files"])


def test_video_adapter_default_fps_is_wan_standard():
    """Wan 官方 examples 统一 fps=15（此前误用 16）。"""
    cls = registry.get_class("video", "diffsynth_wan")
    assert cls is not None
    assert cls.spec.default_params["fps"] == 15
    assert cls.spec.default_params["model_preset"] == "wan2.2-ti2v-5b"


def test_video_adapter_flf2v_runtime_dispatch():
    """FLF2V 预设 + end_image 时走过渡分支（mock pipe 验证调用参数）。"""
    from PIL import Image

    import app.adapters.video_diffsynth as vd
    from app.adapters.video_diffsynth import DiffSynthWanVideo
    from app.vram import ModelSlot

    calls: dict = {}

    class _FakePipe:
        def __call__(self, **kw):
            calls.update(kw)
            return [Image.new("RGB", (64, 64)) for _ in range(33)]

    adapter = DiffSynthWanVideo({"model_preset": "wan2.1-flf2v-14b"})
    slot = ModelSlot("test_flf2v", capability="video")
    slot.load(lambda: _FakePipe())

    mp = pytest.MonkeyPatch()
    mp.setattr(DiffSynthWanVideo, "_slot", slot, raising=False)
    mp.setattr(vd, "_save_video", lambda *a, **k: None, raising=False)
    try:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            first = td / "first.png"
            second = td / "second.png"
            Image.new("RGB", (64, 64)).save(first)
            Image.new("RGB", (64, 64)).save(second)
            res = adapter.run({"image_path": str(first),
                               "end_image_path": str(second),
                               "out_path": td / "out.mp4",
                               "prompt": "转场", "width": 64, "height": 64})
    finally:
        mp.undo()
    assert "end_image" in calls, "FLF2V 模式必须传 end_image"
    assert "input_image" in calls
    assert calls.get("sigma_shift") == 16
    assert res["transition"] is True


# ----------------------------------------------------------------------
# 图像后端：Qwen-Image / Qwen-Image-Edit 预设
# ----------------------------------------------------------------------
def test_image_adapter_presets_cover_qwen_models():
    from app.adapters.image_diffsynth import _MODEL_PRESETS

    assert "qwen-image" in _MODEL_PRESETS
    assert "qwen-image-edit" in _MODEL_PRESETS
    assert _MODEL_PRESETS["qwen-image-edit"]["model_id"] == \
        "Qwen/Qwen-Image-Edit-2509"
    assert _MODEL_PRESETS["qwen-image-edit"]["edit"] is True


def test_image_adapter_edit_uses_processor_config():
    """Qwen-Image-Edit 用 processor_config（非 tokenizer_config）——官方
    examples 的关键差异，静态锁定。"""
    src = (_ADAPTERS_DIR / "image_diffsynth.py").read_text("utf-8")
    assert "processor_config" in src
    assert "edit_image" in src and "edit_image_auto_resize" in src


def test_image_adapter_edit_runtime_passes_ref_list():
    """ref_images → edit_image 列表（官方约定：必须是列表）。"""
    from PIL import Image

    from app.adapters.image_diffsynth import DiffSynthImage
    from app.vram import ModelSlot

    calls: dict = {}

    class _FakePipe:
        def __call__(self, **kw):
            calls.update(kw)
            return Image.new("RGB", (64, 64))

    adapter = DiffSynthImage({"model_preset": "qwen-image-edit"})
    slot = ModelSlot("test_edit", capability="image")
    slot.load(lambda: _FakePipe())

    mp = pytest.MonkeyPatch()
    mp.setattr(DiffSynthImage, "_slot", slot, raising=False)
    try:
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            ref = td / "ref.png"
            Image.new("RGB", (64, 64)).save(ref)
            adapter.run({"prompt": "保持角色", "out_path": td / "out.png",
                         "ref_images": [str(ref)], "width": 64, "height": 64})
    finally:
        mp.undo()
    assert isinstance(calls.get("edit_image"), list)
    assert calls.get("edit_image_auto_resize") is True


# ----------------------------------------------------------------------
# 流水线：角色参考图 + 镜头过渡
# ----------------------------------------------------------------------
def test_config_validates_new_episode_defaults():
    from app.config import Settings, SettingsError

    s = Settings()
    with pytest.raises(SettingsError):
        s.validate({"episode_defaults": {"transition": "bad-value"}})
    with pytest.raises(SettingsError):
        s.validate({"episode_defaults": {"character_refs": "yes"}})
    ok = s.validate({"episode_defaults": {"transition": "flf2v",
                                          "character_refs": False}})
    assert ok["episode_defaults"]["transition"] == "flf2v"


def test_config_defaults_include_new_settings():
    from app.config import get_settings

    ed = get_settings().as_dict()["episode_defaults"]
    assert ed["character_refs"] is True
    assert ed["transition"] == "none"


def test_worldview_skips_refs_for_mock_backend(small_project):
    """mock 后端不生成角色参考图（快速演示路径不受影响）。"""
    from app import paths
    from app.pipeline import run_pipeline
    from app.services import create_episode

    ep = create_episode(small_project["id"], "第1集")
    summary = run_pipeline(ep["id"], "worldview", force=False)
    assert summary["status"] == "succeeded"
    chars_dir = paths.project_dir(small_project["id"]) / "characters"
    assert not chars_dir.exists() or not list(chars_dir.glob("*.png"))


def test_keyframes_passes_ref_images_when_available(small_project, monkeypatch):
    """已有角色参考图时，keyframes ctx 必须带 ref_images（按出场角色过滤）。"""
    from app import paths
    from app.pipeline import resolve_adapter, stage_keyframes
    from app.services import create_episode
    from app.store import get_store

    ep = create_episode(small_project["id"], "第1集")
    from app.pipeline import run_pipeline

    for st in ("worldview", "script", "storyboard"):
        s = run_pipeline(ep["id"], st, force=False)
        assert s["status"] == "succeeded", s

    project = get_store().get_project(small_project["id"])
    episode = get_store().get_episode(ep["id"])
    assets = json.loads((paths.project_dir(small_project["id"])
                         / "project.json").read_text("utf-8"))
    name = assets["characters"][0]["name"]

    # 手工放置角色参考图（模拟 worldview 阶段产物）
    cdir = paths.project_dir(small_project["id"]) / "characters"
    cdir.mkdir(parents=True, exist_ok=True)
    from app.adapters.image_mock import MockImage

    MockImage().run({"prompt": "定妆照", "out_path": cdir / f"01-{name}.png",
                     "width": 32, "height": 32})

    captured: list[dict] = []

    class _SpyImage:
        def __init__(self):
            from app.adapters.image_mock import MockImage
            self._inner = MockImage()

        def run(self, ctx, progress=None):
            captured.append(dict(ctx))
            return self._inner.run(ctx, progress)

    def fake_resolve(capability, project):
        if capability == "image":
            return _SpyImage()
        return resolve_adapter(capability, project)

    monkeypatch.setattr("app.pipeline.resolve_adapter", fake_resolve)
    stage_keyframes(project, episode)
    assert captured, "keyframes 未调用图像后端"
    with_refs = [c for c in captured if c.get("ref_images")]
    assert with_refs, "有角色参考图时 ctx 必须包含 ref_images"
    assert all(Path(p).exists() for p in with_refs[0]["ref_images"])


def test_clips_passes_end_image_when_transition_enabled(small_project, monkeypatch):
    """transition=flf2v 时，非末尾镜头必须传下一镜关键帧作为 end_image_path。"""
    from app.pipeline import resolve_adapter, run_pipeline, stage_clips
    from app.services import create_episode, patch_project
    from app.store import get_store

    ep = create_episode(small_project["id"], "第1集")
    for st in ("worldview", "script", "storyboard", "keyframes"):
        s = run_pipeline(ep["id"], st, force=False)
        assert s["status"] == "succeeded", s

    project = get_store().get_project(small_project["id"])
    # 开启镜头过渡
    cfg = dict(project.get("config") or {})
    cfg.setdefault("episode_defaults", {})["transition"] = "flf2v"
    patch_project(project["id"], {"config": cfg})

    captured: list[dict] = []

    class _SpyVideo:
        def __init__(self):
            from app.adapters.video_kenburns import KenBurnsVideo
            self._inner = KenBurnsVideo({"width": 320, "height": 180, "fps": 12})

        def run(self, ctx, progress=None):
            captured.append(dict(ctx))
            return self._inner.run(ctx, progress)

    def fake_resolve(capability, project):
        if capability == "video":
            return _SpyVideo()
        return resolve_adapter(capability, project)

    monkeypatch.setattr("app.pipeline.resolve_adapter", fake_resolve)
    stage_clips(get_store().get_project(small_project["id"]),
                get_store().get_episode(ep["id"]))
    # small_project 固定 2 镜头：第 1 镜应有 end_image_path，末镜无
    with_end = [c for c in captured if c.get("end_image_path")]
    assert with_end, "transition=flf2v 时非末尾镜头必须传 end_image_path"
    assert all(Path(c["end_image_path"]).exists() for c in with_end)
    last_ctx = captured[-1]
    assert "end_image_path" not in last_ctx, "末尾镜头不应有 end_image_path"


def test_clips_no_end_image_when_transition_off(small_project, monkeypatch):
    """transition=none（默认）时不传 end_image_path。"""
    from app.pipeline import resolve_adapter, run_pipeline, stage_clips
    from app.services import create_episode
    from app.store import get_store

    ep = create_episode(small_project["id"], "第1集")
    for st in ("worldview", "script", "storyboard", "keyframes"):
        s = run_pipeline(ep["id"], st, force=False)
        assert s["status"] == "succeeded", s

    captured: list[dict] = []

    class _SpyVideo:
        def __init__(self):
            from app.adapters.video_kenburns import KenBurnsVideo
            self._inner = KenBurnsVideo({"width": 320, "height": 180, "fps": 12})

        def run(self, ctx, progress=None):
            captured.append(dict(ctx))
            return self._inner.run(ctx, progress)

    def fake_resolve(capability, project):
        if capability == "video":
            return _SpyVideo()
        return resolve_adapter(capability, project)

    monkeypatch.setattr("app.pipeline.resolve_adapter", fake_resolve)
    stage_clips(get_store().get_project(small_project["id"]),
                get_store().get_episode(ep["id"]))
    assert all("end_image_path" not in c for c in captured)


def test_character_ref_map_parses_names():
    """character_ref_map：文件名 01-角色名.png → 角色名映射。"""
    from app import paths
    from app.pipeline import character_ref_map

    cdir = paths.project_dir("p-refmap") / "characters"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "01-林晚.png").write_bytes(b"png")
    (cdir / "02-陆则铭.png").write_bytes(b"png")
    m = character_ref_map("p-refmap")
    assert set(m) == {"林晚", "陆则铭"}
    assert all(v.endswith(".png") for v in m.values())


# ----------------------------------------------------------------------
# 对话指令：过渡/参考图偏好解析
# ----------------------------------------------------------------------
def test_chat_parses_transition_and_ref_preferences():
    from app.chat import parse_intent_rules

    r1 = parse_intent_rules("开启镜头过渡")
    assert r1["intent"] == "set_preferences" and r1["transition"] == "flf2v"
    r2 = parse_intent_rules("用首尾帧转场")
    assert r2["transition"] == "flf2v"
    r3 = parse_intent_rules("关闭过渡")
    assert r3["transition"] == "none"
    r4 = parse_intent_rules("开启角色参考图")
    assert r4["character_refs"] is True
    r5 = parse_intent_rules("关闭参考图")
    assert r5["character_refs"] is False

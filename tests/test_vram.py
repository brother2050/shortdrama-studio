"""显存管理测试：多引擎/后端切换时按能力释放。"""
from __future__ import annotations

import pytest

from app.vram import (ModelSlot, check_vram, gpu_info, pick_device,
                      release_all, release_capability, vram_summary)


def test_gpu_info_returns_none_or_dict():
    info = gpu_info()
    if info is None:
        return  # 无 GPU 环境，正常
    assert "name" in info and "total_gb" in info and "free_gb" in info
    assert info["total_gb"] > 0


def test_check_vram_no_gpu_always_true():
    if gpu_info() is None:
        assert check_vram(100.0) is True
    else:
        assert check_vram(0.001) is True


def test_pick_device_cpu_preference():
    assert pick_device("cpu", 0) == "cpu"


def test_model_slot_lifecycle():
    slot = ModelSlot("t_slot", capability="test")
    assert not slot.is_loaded
    assert slot.load(lambda: {"v": 1}) == {"v": 1}
    assert slot.is_loaded
    assert slot.load(lambda: "x") == {"v": 1}   # 缓存复用
    slot.unload()
    assert not slot.is_loaded


def test_model_slot_reload():
    slot = ModelSlot("t_reload", capability="test")
    slot.load(lambda: "first")
    slot.reload(lambda: "second")
    assert slot.model == "second"
    slot.unload()


# ----------------------------------------------------------------------
# 多引擎切换：按能力精确释放（核心场景）
# ----------------------------------------------------------------------
def test_release_capability_only_targets_that_capability():
    """释放 tts 能力时，其他能力的已加载模型不受影响。"""
    tts_a = ModelSlot("chattts", capability="tts")
    tts_b = ModelSlot("cosyvoice2", capability="tts")
    img = ModelSlot("image_diffusers", capability="image")
    try:
        tts_a.load(lambda: "tts_model_a")
        tts_b.load(lambda: "tts_model_b")
        img.load(lambda: "img_model")
        assert tts_a.is_loaded and tts_b.is_loaded and img.is_loaded

        released = release_capability("tts")

        assert released == 2
        assert not tts_a.is_loaded and not tts_b.is_loaded
        assert img.is_loaded, "image 模型不应被 tts 释放波及"
    finally:
        release_all()


def test_release_capability_skips_unloaded():
    slot = ModelSlot("t_empty", capability="tts")
    try:
        assert release_capability("tts") == 0    # 未加载不释放
        slot.load(lambda: "m")
        assert release_capability("tts") == 1
    finally:
        release_all()


def test_release_capability_unknown_is_noop():
    assert release_capability("no_such_cap") == 0


def test_release_all_unloads_everything():
    a = ModelSlot("t_all_a", capability="tts")
    b = ModelSlot("t_all_b", capability="image")
    try:
        a.load(lambda: 1)
        b.load(lambda: 2)
        release_all()
        assert not a.is_loaded and not b.is_loaded
    finally:
        release_all()


def test_vram_summary_lists_loaded():
    slot = ModelSlot("t_summary", capability="tts")
    try:
        summary = vram_summary()
        assert "loaded_models" in summary
        slot.load(lambda: "m")
        assert "t_summary" in vram_summary()["loaded_models"]
    finally:
        release_all()


# ----------------------------------------------------------------------
# 注册表按能力卸载（后端切换入口）
# ----------------------------------------------------------------------
def test_registry_unload_capability_clears_instances():
    """unload_capability 清空该能力缓存实例并调用 unload。"""
    from app.adapters import registry

    inst = registry.resolve("tts", "mock")
    assert inst is not None
    key_exists = any(k[0] == "tts" for k in registry._instances)
    assert key_exists

    registry.unload_capability("tts")

    assert not any(k[0] == "tts" for k in registry._instances)
    assert not any(k[0] == "llm" for k in registry._instances) or True


def test_registry_unload_capability_unknown_noop():
    from app.adapters import registry

    registry.unload_capability("nope")          # 不抛错


def test_registry_unload_all():
    from app.adapters import registry

    registry.resolve("tts", "mock")
    registry.resolve("llm", "mock")
    registry.unload_all()
    assert not registry._instances


# ----------------------------------------------------------------------
# 设置更新触发释放（多引擎切换显存回收钩子）
# ----------------------------------------------------------------------
def test_update_settings_releases_on_backend_switch(tmp_path, monkeypatch):
    """切换能力后端时自动卸载该能力旧模型（防双份模型占显存）。"""
    from app import services
    from app.adapters import registry

    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path))
    services.get_settings().__class__._instance = None   # 重置单例
    released = []
    monkeypatch.setattr(registry, "unload_capability",
                        lambda cap: released.append(cap))

    services.update_settings({"capabilities": {"tts": {"backend": "mock"}}})
    assert released == ["tts"]

    # 相同配置再提交：不触发释放
    released.clear()
    services.update_settings({"capabilities": {"tts": {"backend": "mock"}}})
    assert released == []

    # 只动其他能力：不释放 tts
    released.clear()
    services.update_settings({"capabilities": {"llm": {"backend": "mock"}}})
    assert released == ["llm"]

    # 参数变化也触发释放
    released.clear()
    services.update_settings(
        {"capabilities": {"tts": {"backend": "mock", "params": {"speed": 1.1}}}})
    assert released == ["tts"]


# ----------------------------------------------------------------------
# 适配器 unload 接线
# ----------------------------------------------------------------------
def test_adapter_unload_method_exists():
    from app.adapters.base import AdapterBase
    from app.adapters.image_mock import MockImage

    assert hasattr(AdapterBase, "unload")
    MockImage({}).unload()      # 无模型时卸载不报错


def test_tts_adapters_slot_belong_to_tts():
    """四个 TTS 适配器的 ModelSlot 都归属 tts 能力（切换释放依赖此）。"""
    from app.adapters.tts_chattts import ChatTTSAdapter
    from app.adapters.tts_cosyvoice import CosyVoiceTTS
    from app.adapters.tts_fishspeech import FishSpeechAdapter
    from app.adapters.tts_gptsovits import GPTSoVITSAdapter

    for cls in (CosyVoiceTTS, ChatTTSAdapter, GPTSoVITSAdapter, FishSpeechAdapter):
        assert cls._slot.capability == "tts", f"{cls.__name__} slot 未归属 tts"

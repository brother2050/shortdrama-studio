"""显存管理模块测试。"""
import pytest

from app.vram import (ModelSlot, check_vram, gpu_info, pick_device,
                       release_all, vram_summary)


def test_gpu_info_returns_none_or_dict():
    """gpu_info 在无 CUDA 时返回 None，有 CUDA 时返回含必要字段的字典。"""
    info = gpu_info()
    if info is None:
        return  # 无 GPU 环境，正常
    assert "name" in info
    assert "total_gb" in info
    assert "free_gb" in info
    assert "used_gb" in info
    assert isinstance(info["total_gb"], (int, float))
    assert info["total_gb"] > 0


def test_check_vram_no_gpu_always_true():
    """无 GPU 时 check_vram 始终返回 True（CPU 模式不卡显存）。"""
    info = gpu_info()
    if info is None:
        assert check_vram(100.0) is True
    else:
        assert check_vram(0.001) is True


def test_pick_device_no_gpu_returns_cpu():
    """无 CUDA 时 pick_device 返回 'cpu'。"""
    info = gpu_info()
    if info is None:
        assert pick_device("auto", 0) == "cpu"
        assert pick_device("cuda", 0) == "cpu"
        assert pick_device("cpu", 0) == "cpu"


def test_pick_device_cpu_preference():
    """preference='cpu' 始终返回 'cpu'。"""
    assert pick_device("cpu", 0) == "cpu"


def test_model_slot_lifecycle():
    """ModelSlot 加载/卸载生命周期。"""
    slot = ModelSlot("test_slot")
    assert not slot.is_loaded
    assert slot.model is None

    loaded = slot.load(lambda: {"test": True})
    assert slot.is_loaded
    assert loaded == {"test": True}
    assert slot.model == {"test": True}

    # 再次 load 返回缓存
    assert slot.load(lambda: "should_not_call") == {"test": True}

    slot.unload()
    assert not slot.is_loaded
    assert slot.model is None


def test_model_slot_reload():
    """ModelSlot reload 先卸载旧模型再加载新的。"""
    slot = ModelSlot("test_reload")
    slot.load(lambda: "first")
    assert slot.model == "first"
    slot.reload(lambda: "second")
    assert slot.model == "second"
    slot.unload()


def test_release_all():
    """release_all 卸载所有已注册的 ModelSlot。"""
    s1 = ModelSlot("test_release_1")
    s2 = ModelSlot("test_release_2")
    s1.load(lambda: "model1")
    s2.load(lambda: "model2")
    assert s1.is_loaded
    assert s2.is_loaded

    release_all()

    assert not s1.is_loaded
    assert not s2.is_loaded


def test_vram_summary():
    """vram_summary 返回包含必要字段的字典。"""
    summary = vram_summary()
    assert "available" in summary
    if summary["available"]:
        assert "device" in summary
        assert "total_gb" in summary
        assert "free_gb" in summary
        assert "loaded_models" in summary
    else:
        assert "device" in summary


def test_adapter_unload_method_exists():
    """AdapterBase 有 unload 方法（默认空实现）。"""
    from app.adapters.base import AdapterBase
    assert hasattr(AdapterBase, "unload")
    # mock 适配器不需要卸载
    from app.adapters.image_mock import MockImage
    inst = MockImage({})
    inst.unload()  # 不应报错


def test_registry_unload_all():
    """registry.unload_all 清空缓存并调用适配器 unload。"""
    from app.adapters import registry
    # 加载一个 mock 适配器
    inst = registry.resolve("llm", "mock")
    assert inst is not None
    # unload_all 不应报错
    registry.unload_all()
    # 重新 resolve 会创建新实例
    inst2 = registry.resolve("llm", "mock")
    assert inst2 is not None

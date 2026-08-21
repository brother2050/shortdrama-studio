"""显存（VRAM）管理模块：设备探测、余量检查、模型生命周期管理。

核心功能：
1. ``gpu_info()``：探测 GPU 型号、总显存、已用/可用显存（无 CUDA 时返回 None）。
2. ``check_vram(required_gb)``：检查当前可用显存是否足够，不够则给出可读错误。
3. ``pick_device(preference, required_gb)``：智能设备选择——优先 CUDA，不够则回退 CPU。
4. ``ModelSlot``：模型生命周期管理器（capability 归属 + 显式卸载），全部槽位登记在案。
5. ``release_capability(capability)``：按能力精确释放（多引擎/后端切换时调用）。
6. ``release_all()``：释放全部模型（阶段间 / 手动一键释放）。

设计原则：
- 所有 torch/diffsynth 调用都应有 OOM 恢复：先 CUDA，失败回退 CPU。
- 切换后端/引擎时必须先释放旧模型（settings 更新钩子调用 release_capability）。
- 无 CUDA 环境全部静默降级到 CPU，不阻塞流程。
"""
from __future__ import annotations

import gc
import logging
import threading
from typing import Any

logger = logging.getLogger("app.vram")


def _get_torch():
    """惰性导入 torch（未安装时返回 None）。"""
    try:
        import torch
        return torch
    except ImportError:
        return None


def gpu_info() -> dict[str, Any] | None:
    """探测 GPU 信息（无 CUDA 时返回 None）。"""
    torch = _get_torch()
    if torch is None or not torch.cuda.is_available():
        return None
    try:
        idx = torch.cuda.current_device()
        props = torch.cuda.get_device_properties(idx)
        allocated = torch.cuda.memory_allocated(idx) / 1024 ** 3
        reserved = torch.cuda.memory_reserved(idx) / 1024 ** 3
        total = props.total_memory / 1024 ** 3
        return {
            "name": props.name,
            "index": idx,
            "total_gb": round(total, 2),
            "used_gb": round(allocated, 2),
            "reserved_gb": round(reserved, 2),
            "free_gb": round(total - reserved, 2),
            "cuda_version": torch.version.cuda or "unknown",
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("GPU 探测失败: %s", exc)
        return None


def check_vram(required_gb: float) -> bool:
    """检查可用显存是否足够（无 CUDA 视为 CPU 模式，始终返回 True）。"""
    info = gpu_info()
    if info is None:
        return True
    if info["free_gb"] < required_gb:
        logger.warning("显存不足：需要 %.1fGB，可用 %.1fGB（总共 %.1fGB）",
                       required_gb, info["free_gb"], info["total_gb"])
        return False
    return True


def pick_device(preference: str = "auto", required_gb: float = 0.0) -> str:
    """智能设备选择：优先 CUDA，显存不足回退 CPU（附警告）。"""
    torch = _get_torch()
    if torch is None or not torch.cuda.is_available():
        return "cpu"
    if preference == "cpu":
        return "cpu"
    if preference in ("cuda", "auto"):
        if check_vram(required_gb):
            return "cuda"
        logger.warning("显存不足（需 %.1fGB），回退到 CPU（速度会慢很多）", required_gb)
    return "cpu"


def unload_model(obj: Any) -> None:
    """释放单个模型占用的显存：移回 CPU → 触发回收 → 清空 CUDA 缓存。

    调用方随后应置空自己的引用（ModelSlot.unload 已处理）。
    """
    torch = _get_torch()
    if obj is not None and torch is not None and torch.cuda.is_available():
        if hasattr(obj, "to"):
            try:
                obj.to("cpu")
            except Exception:  # noqa: BLE001 —— to() 失败仍继续回收
                pass
    gc.collect()
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


class ModelSlot:
    """模型生命周期管理器：加载缓存 + 显式卸载 + 按能力归属登记。

    - ``load(fn)``：首次调用加载模型，后续返回缓存。
    - ``unload()``：显式卸载（切后端 / 阶段结束 / 手动释放时调用）。
    - 全部槽位登记在类级 ``_slots``，``release_capability``/``release_all`` 统一调度。
    """

    _slots: list["ModelSlot"] = []
    _slots_lock = threading.Lock()

    def __init__(self, name: str = "", capability: str = ""):
        self.name = name
        self.capability = capability          # 归属能力（tts/image/...），用于精确释放
        self._model: Any = None
        self._loaded: bool = False
        with ModelSlot._slots_lock:
            ModelSlot._slots.append(self)

    def load(self, fn) -> Any:
        """加载模型（fn 返回模型对象），已加载则直接返回缓存。"""
        if self._loaded and self._model is not None:
            return self._model
        self._model = fn()
        self._loaded = True
        return self._model

    @property
    def is_loaded(self) -> bool:
        return self._loaded and self._model is not None

    @property
    def model(self) -> Any:
        return self._model

    def unload(self) -> None:
        """卸载模型并释放显存。"""
        if not self._loaded:
            return
        unload_model(self._model)
        self._model = None
        self._loaded = False
        logger.info("模型槽 %s（%s）已卸载", self.name or "unnamed",
                    self.capability or "-")

    def reload(self, fn) -> Any:
        """重新加载（先卸载旧的）。"""
        self.unload()
        return self.load(fn)


def release_all() -> None:
    """卸载所有已注册的 ModelSlot（阶段间 / 手动一键释放）。"""
    with ModelSlot._slots_lock:
        slots = list(ModelSlot._slots)
    for slot in slots:
        if slot.is_loaded:
            slot.unload()


def release_capability(capability: str) -> int:
    """卸载指定能力的全部模型，返回释放的槽数。

    多引擎/后端切换时调用：先释放旧后端模型再加载新后端，
    避免两份模型同时占显存导致 OOM。
    """
    with ModelSlot._slots_lock:
        targets = [s for s in ModelSlot._slots
                   if s.is_loaded and s.capability == capability]
    for slot in targets:
        slot.unload()
    return len(targets)


def vram_summary() -> dict[str, Any]:
    """显存状态摘要（系统健康页用）。"""
    info = gpu_info()
    with ModelSlot._slots_lock:
        loaded = [s.name for s in ModelSlot._slots if s.is_loaded]
    if info is None:
        return {"available": False, "device": "CPU（无 CUDA/GPU）",
                "loaded_models": loaded}
    return {
        "available": True,
        "device": info["name"],
        "total_gb": info["total_gb"],
        "used_gb": info["used_gb"],
        "free_gb": info["free_gb"],
        "cuda_version": info["cuda_version"],
        "loaded_models": loaded,
    }

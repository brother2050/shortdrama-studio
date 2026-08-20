"""能力适配器基类与注册表（参考 mosaic TTSBackendRegistry 模式）。

五大能力与 run() 契约
--------------------
**llm**  ctx: ``{messages: [{role, content}], system: str}`` → ``{"text": str}``
**tts**  ctx: ``{text, voice, out_path}`` → ``{"path", "duration", "sample_rate"}``
**image** ctx: ``{prompt, negative_prompt|None, out_path, width, height}`` → ``{"path", "width", "height"}``
**video** ctx: ``{image_path, out_path, duration, motion, fps, width, height}`` → ``{"path", "duration"}``
**asr**  ctx: ``{audio_path, segments: [{start, end, text, speaker}]}`` → ``{"segments": [...]}``

约定：
- 重依赖（torch/transformers/diffusers 等）必须在 ``run()`` 内部惰性导入，
  保证未安装时模块仍可导入、可注册、可被探测为"不可用"。
- ``is_available()`` 由注册表调用：探测 requires 中的 Python 包 + 子类钩子
  ``_extra_available()``（如 ffmpeg 二进制、ollama 端口）。
- 每个适配器通过 ``AdapterSpec.default_params`` 声明默认参数（不选也有默认值），
  ``param_docs`` 提供人可读说明（设置页自动渲染表单）。
"""
from __future__ import annotations

import importlib.util
import shutil
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Type

CAPABILITIES = ("llm", "tts", "image", "video", "asr")


@dataclass
class AdapterSpec:
    """适配器规格：注册表据此做可用性探测、auto 选择与设置页渲染。"""
    name: str
    capability: str
    display_name: str = ""
    description: str = ""
    priority: int = 100              # 数值越小优先级越高（auto 模式排序依据）
    requires: list[str] = field(default_factory=list)   # 需要可导入的 Python 包
    default_params: dict[str, Any] = field(default_factory=dict)
    param_docs: dict[str, str] = field(default_factory=dict)  # 参数名 → 中文说明
    vram_gb: float = 0.0             # 0 表示无需 GPU
    license: str = ""


ProgressFn = Callable[[str, float], None]   # (消息, 0~100)


class AdapterError(RuntimeError):
    """适配器执行失败（信息面向用户，可读）。"""


class AdapterUnavailableError(AdapterError):
    """后端不可用（缺依赖/缺二进制/服务未启动）。"""


class AdapterBase:
    """适配器基类：子类设置 ``spec`` 类属性并实现 ``run()``。"""

    spec: AdapterSpec = AdapterSpec(name="base", capability="llm")

    # ------------------------------------------------------------------
    # 可用性探测
    # ------------------------------------------------------------------
    @classmethod
    def is_available(cls) -> bool:
        for pkg in cls.spec.requires:
            if importlib.util.find_spec(pkg) is None:
                return False
        return cls._extra_available()

    @classmethod
    def _extra_available(cls) -> bool:
        """子类钩子：探测外部二进制/服务（默认通过）。"""
        return True

    @classmethod
    def availability(cls) -> dict[str, Any]:
        missing = [p for p in cls.spec.requires
                   if importlib.util.find_spec(p) is None]
        reason = ""
        ok = not missing and cls._extra_available()
        if missing:
            reason = f"缺少 Python 依赖: {', '.join(missing)}"
        elif not ok:
            reason = cls._unavailable_reason()
        return {
            "name": cls.spec.name, "capability": cls.spec.capability,
            "display_name": cls.spec.display_name, "description": cls.spec.description,
            "available": ok, "reason": reason, "priority": cls.spec.priority,
            "vram_gb": cls.spec.vram_gb, "license": cls.spec.license,
            "default_params": dict(cls.spec.default_params),
            "param_docs": dict(cls.spec.param_docs),
        }

    @classmethod
    def _unavailable_reason(cls) -> str:
        return "外部依赖不可用"

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------
    def __init__(self, params: dict[str, Any] | None = None) -> None:
        merged = dict(self.spec.default_params)
        merged.update(params or {})
        self.params: dict[str, Any] = merged

    def run(self, ctx: dict[str, Any], progress: ProgressFn | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def unload(self) -> None:
        """卸载模型释放显存（子类按需覆盖）。"""
        pass


# ----------------------------------------------------------------------
# 注册表
# ----------------------------------------------------------------------
class AdapterRegistry:
    """能力适配器注册表（单例）。

    - ``register_adapter`` 装饰器注册（同一 name 后注册覆盖前者，便于插件替换）。
    - ``resolve(capability, backend, params)``：
      * backend="auto" → 按 priority 升序选第一个可用后端；
      * 显式名称 → 必须可用，否则抛 AdapterUnavailableError（附修复建议）。
    - 实例按 ``(能力, 名称, 参数指纹)`` 缓存（重模型只加载一次），线程安全。
    """

    def __init__(self) -> None:
        self._classes: dict[tuple[str, str], Type[AdapterBase]] = {}
        self._instances: dict[tuple[str, str, str], AdapterBase] = {}
        self._lock = threading.Lock()

    def register(self, cls: Type[AdapterBase]) -> Type[AdapterBase]:
        spec = cls.spec
        if spec.capability not in CAPABILITIES:
            raise ValueError(f"未知能力: {spec.capability}")
        if not spec.name or not spec.name.replace("_", "").isalnum():
            raise ValueError(f"非法后端名: {spec.name!r}")
        with self._lock:
            self._classes[(spec.capability, spec.name)] = cls
        return cls

    def names(self, capability: str) -> list[str]:
        with self._lock:
            return sorted(k[1] for k in self._classes if k[0] == capability)

    def get_class(self, capability: str, name: str) -> Type[AdapterBase] | None:
        with self._lock:
            return self._classes.get((capability, name))

    def list_specs(self, capability: str) -> list[dict[str, Any]]:
        with self._lock:
            classes = [c for (cap, _), c in self._classes.items() if cap == capability]
        infos = [c.availability() for c in classes]
        return sorted(infos, key=lambda x: (x["priority"], x["name"]))

    def auto_pick(self, capability: str) -> str:
        infos = self.list_specs(capability)
        for info in infos:
            if info["available"]:
                return info["name"]
        raise AdapterUnavailableError(
            f"能力 {capability} 没有任何可用后端（检查依赖安装或选择 mock 后端）")

    def resolve(self, capability: str, backend: str = "auto",
                params: dict[str, Any] | None = None) -> AdapterBase:
        if capability not in CAPABILITIES:
            raise AdapterError(f"未知能力: {capability}")
        if backend in ("", "auto"):
            backend = self.auto_pick(capability)
        cls = self.get_class(capability, backend)
        if cls is None:
            known = ", ".join(self.names(capability))
            raise AdapterError(f"未注册的 {capability} 后端: {backend!r}（可选: {known}）")
        if not cls.is_available():
            info = cls.availability()
            raise AdapterUnavailableError(
                f"{capability} 后端 {backend} 不可用：{info['reason']}。"
                f"请在设置页更换后端，或安装依赖（见 requirements-models.txt）")
        import json as _json
        try:
            param_key = _json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            param_key = str(sorted(params or {}))
        key = (capability, backend, param_key)
        with self._lock:
            inst = self._instances.get(key)
            if inst is None:
                inst = cls(params)
                self._instances[key] = inst
            return inst

    def unload_all(self) -> None:
        """卸载所有缓存适配器实例（释放显存）。"""
        with self._lock:
            instances = list(self._instances.values())
            self._instances.clear()
        for inst in instances:
            if hasattr(inst, "unload"):
                try:
                    inst.unload()
                except Exception:
                    pass
        from app.vram import release_all
        release_all()


registry = AdapterRegistry()


def register_adapter(cls: Type[AdapterBase]) -> Type[AdapterBase]:
    """类装饰器：注册适配器。"""
    return registry.register(cls)


def which_ffmpeg() -> str | None:
    """探测 ffmpeg 二进制（kenburns/composer 依赖）。"""
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")

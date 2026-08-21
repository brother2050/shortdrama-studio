"""全局设置：默认值 + JSON 持久化 + 深度合并 + 校验。

设计要点（对应需求"可以选择不同的模型、不同的参数，不选也有默认的值"）：
- 每个能力（llm/tts/image/video/asr）配置 ``backend``（"auto" 或注册名）与 ``params``。
- 未配置的键自动回退默认值；params 与适配器自身的 default_params 合并。
- 修改立即保存到 ``data/config.json``（人可读、可直接手工编辑）。
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any

from app import paths

CAPABILITIES = ("llm", "tts", "image", "video", "asr")

DEFAULT_SETTINGS: dict[str, Any] = {
    "capabilities": {
        cap: {"backend": "auto", "params": {}}
        for cap in CAPABILITIES
    },
    # 画幅与帧率（合成与 mock 图像共用）
    "video_output": {"width": 1280, "height": 720, "fps": 24},
    # 分集默认值（对话/创建项目时可覆盖）
    "episode_defaults": {
        "shots_per_episode": 4,          # 每集镜头数
        "target_clip_seconds": 5.0,      # 单镜头目标时长（配音更长则顺延）
        "style": "电影感, 自然光, 高清, 浅景深",  # 全局风格提示词（锁视觉一致性）
        "character_refs": True,          # 角色参考图：世界观阶段生成肖像，关键帧锁定外貌
        "transition": "none",            # 镜头过渡：none / flf2v（首尾帧转场，需 diffsynth_wan flf2v 预设）
    },
}


def deep_merge(base: dict, override: dict) -> dict:
    """深度合并：override 覆盖 base（返回新字典，不修改入参）。"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class SettingsError(ValueError):
    """设置校验失败。"""


class Settings:
    """线程安全的设置管理器（单文件 JSON 持久化）。"""

    def __init__(self, path=None) -> None:
        self._path = paths.config_path() if path is None else path
        self._lock = threading.RLock()  # 可重入：reload() 内部会调用 save()
        self._data: dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.reload()

    # -- 持久化 -----------------------------------------------------------
    def reload(self) -> None:
        with self._lock:
            if self._path.exists():
                try:
                    raw = json.loads(self._path.read_text("utf-8"))
                except json.JSONDecodeError as exc:  # 文件损坏时回退默认并保留备份
                    self._path.rename(self._path.with_suffix(".broken.json"))
                    raise SettingsError(f"config.json 解析失败，已备份：{exc}") from exc
                self._data = self.validate(deep_merge(DEFAULT_SETTINGS, raw))
            else:
                self._data = dict(DEFAULT_SETTINGS)
                self.save()

    def save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), "utf-8"
            )

    # -- 读写 -------------------------------------------------------------
    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data, ensure_ascii=False))

    def update(self, partial: dict[str, Any]) -> dict[str, Any]:
        """合并更新并持久化（先校验后生效）。"""
        merged = self.validate(deep_merge(self.as_dict(), partial or {}))
        with self._lock:
            self._data = merged
        self.save()
        return self.as_dict()

    # -- 校验 -------------------------------------------------------------
    @staticmethod
    def validate(data: dict[str, Any]) -> dict[str, Any]:
        caps = data.get("capabilities", {})
        unknown = set(caps) - set(CAPABILITIES)
        if unknown:
            raise SettingsError(f"未知能力: {sorted(unknown)}，可选: {list(CAPABILITIES)}")
        for cap in CAPABILITIES:
            conf = caps.get(cap, {})
            backend = conf.get("backend", "auto")
            if not isinstance(backend, str) or not backend:
                raise SettingsError(f"capabilities.{cap}.backend 必须是非空字符串")
            if backend != "auto" and not re.match(r'^[a-zA-Z0-9_]+$', backend):
                raise SettingsError(f"capabilities.{cap}.backend 包含非法字符: {backend}")
            params = conf.get("params", {})
            if not isinstance(params, dict):
                raise SettingsError(f"capabilities.{cap}.params 必须是对象")
        vo = data.get("video_output", {})
        for key in ("width", "height", "fps"):
            if key in vo and (not isinstance(vo[key], int) or vo[key] <= 0):
                raise SettingsError(f"video_output.{key} 必须是正整数")
        ed = data.get("episode_defaults", {})
        if "shots_per_episode" in ed:
            sp = ed["shots_per_episode"]
            if not isinstance(sp, int) or sp < 1 or sp > 50:
                raise SettingsError("episode_defaults.shots_per_episode 必须是 1-50 的整数")
        if "target_clip_seconds" in ed:
            tc = ed["target_clip_seconds"]
            if not isinstance(tc, (int, float)) or tc < 1.0 or tc > 60.0:
                raise SettingsError("episode_defaults.target_clip_seconds 必须是 1.0-60.0 的数")
        if "style" in ed and not isinstance(ed["style"], str):
            raise SettingsError("episode_defaults.style 必须是字符串")
        if "character_refs" in ed and not isinstance(ed["character_refs"], bool):
            raise SettingsError("episode_defaults.character_refs 必须是布尔值")
        if "transition" in ed and ed["transition"] not in ("none", "flf2v"):
            raise SettingsError("episode_defaults.transition 可选值: none / flf2v")
        return data

    # -- 便捷访问 -----------------------------------------------------------
    def capability(self, cap: str, project_override: dict | None = None) -> dict[str, Any]:
        """返回某能力生效配置（项目级覆盖优先）。"""
        if cap not in CAPABILITIES:
            raise SettingsError(f"未知能力: {cap}")
        conf = self.as_dict()["capabilities"][cap]
        if project_override and cap in project_override:
            conf = deep_merge(conf, project_override[cap])
        return conf


_settings: Settings | None = None


def get_settings() -> Settings:
    """进程级单例（测试通过 STUDIO_DATA_DIR 隔离）。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """重置单例（测试用）。"""
    global _settings
    _settings = None

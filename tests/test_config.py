"""配置模块测试：默认值 / 深度合并 / 校验 / 持久化 / 线程安全。"""
from __future__ import annotations

import json
import threading

import pytest

from app import paths
from app.config import (CAPABILITIES, DEFAULT_SETTINGS, Settings,
                        SettingsError, deep_merge, get_settings)


def test_defaults_cover_all_capabilities():
    for cap in CAPABILITIES:
        conf = DEFAULT_SETTINGS["capabilities"][cap]
        assert conf["backend"] == "auto"
        assert isinstance(conf["params"], dict)


def test_first_load_writes_readable_config():
    s = get_settings()
    path = paths.config_path()
    assert path.exists()
    raw = json.loads(path.read_text("utf-8"))
    assert raw["capabilities"]["llm"]["backend"] == "auto"
    assert "shots_per_episode" in raw["episode_defaults"]  # 人可读、可手工编辑


def test_update_persists_and_roundtrip():
    s = get_settings()
    s.update({"capabilities": {"tts": {"backend": "mock", "params": {"speed": 1.2}}}})
    raw = json.loads(paths.config_path().read_text("utf-8"))
    assert raw["capabilities"]["tts"]["backend"] == "mock"
    assert raw["capabilities"]["tts"]["params"]["speed"] == 1.2
    # 重新加载实例可恢复
    s2 = Settings()
    assert s2.capability("tts")["backend"] == "mock"


def test_capability_with_project_override():
    s = get_settings()
    s.update({"capabilities": {"image": {"params": {"width": 640}}}})
    merged = s.capability("image", project_override={
        "image": {"params": {"height": 360}}})
    assert merged["params"]["width"] == 640     # 保留全局
    assert merged["params"]["height"] == 360    # 项目覆盖
    assert merged["backend"] == "auto"


def test_validation_rejects_unknown_capability():
    with pytest.raises(SettingsError, match="未知能力"):
        get_settings().update({"capabilities": {"foo": {}}})


def test_validation_rejects_bad_types():
    with pytest.raises(SettingsError, match="backend"):
        get_settings().update({"capabilities": {"llm": {"backend": ""}}})
    with pytest.raises(SettingsError, match="params"):
        get_settings().update({"capabilities": {"llm": {"params": [1]}}})
    with pytest.raises(SettingsError, match="fps"):
        get_settings().update({"video_output": {"fps": -1}})


def test_reset_restores_defaults_and_persists():
    """恢复默认：自定义修改全部回退并写盘（设置页「恢复默认」按钮）。"""
    s = get_settings()
    s.update({"capabilities": {"tts": {"backend": "mock", "params": {"speed": 2}}},
              "video_output": {"width": 1920, "height": 1080},
              "episode_defaults": {"shots_per_episode": 10, "style": "赛博朋克"}})
    assert s.capability("tts")["backend"] == "mock"

    result = s.reset()
    assert result == DEFAULT_SETTINGS                      # 回到默认
    assert s.capability("tts") == {"backend": "auto", "params": {}}
    assert s.capability("llm")["backend"] == "auto"
    assert s.as_dict()["video_output"]["width"] == 1280
    assert s.as_dict()["episode_defaults"]["shots_per_episode"] == 4
    assert s.as_dict()["episode_defaults"]["style"] == \
        DEFAULT_SETTINGS["episode_defaults"]["style"]
    # 持久化：磁盘上的 config.json 也回默认
    raw = json.loads(paths.config_path().read_text("utf-8"))
    assert raw["capabilities"]["tts"]["backend"] == "auto"
    assert raw["video_output"]["width"] == 1280
    # 新实例加载后同样是默认值
    assert Settings().capability("tts")["backend"] == "auto"


def test_deep_merge_nested_and_scalar():
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    out = deep_merge(base, {"a": {"y": 9}, "b": 4, "c": 5})
    assert out == {"a": {"x": 1, "y": 9}, "b": 4, "c": 5}
    assert base == {"a": {"x": 1, "y": 2}, "b": 3}   # 不改入参


def test_reload_deadlock_regression():
    """回归：reload() 内部调用 save()（嵌套加锁），必须用可重入锁。

    历史 bug：threading.Lock 不可重入 → 首次构造 Settings（写默认配置）即死锁。
    """
    from app.config import reset_settings

    reset_settings()
    paths.config_path().unlink(missing_ok=True)  # 强制走"文件不存在 → save"分支
    result: dict = {}

    def worker():
        try:
            get_settings()
            result["ok"] = True
        except Exception as exc:  # pragma: no cover
            result["ok"] = False
            result["err"] = str(exc)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "Settings 构造死锁（reload→save 嵌套锁）"
    assert result.get("ok") is True

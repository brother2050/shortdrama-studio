"""测试公共夹具：每个测试独立的 STUDIO_DATA_DIR + 全部单例重置。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """数据目录隔离到临时目录，并复位 settings/store/task 单例。"""
    monkeypatch.setenv("STUDIO_DATA_DIR", str(tmp_path / "data"))
    from app import config, store, tasks

    config.reset_settings()
    store.reset_store()
    tasks.reset_task_manager()
    yield
    tasks.reset_task_manager()
    store.reset_store()
    config.reset_settings()


@pytest.fixture()
def small_project():
    """小体量项目（2 镜头、320x180）——加快端到端测试。"""
    from app.services import create_project

    return create_project(
        name="测试剧", genre="都市情感", premise="深夜便利店的相遇",
        config={
            "episode_defaults": {"shots_per_episode": 2},
            "image": {"params": {"width": 320, "height": 180}},
            "video": {"params": {"width": 320, "height": 180, "fps": 12}},
        },
    )


def wait_terminal(episode_id: str, timeout: float = 240.0) -> str:
    """轮询分集进入终态（ready/failed），返回状态。"""
    import time

    from app.store import get_store

    deadline = time.time() + timeout
    while time.time() < deadline:
        ep = get_store().get_episode(episode_id)
        if ep["status"] in ("ready", "failed"):
            return ep["status"]
        time.sleep(0.2)
    return get_store().get_episode(episode_id)["status"]

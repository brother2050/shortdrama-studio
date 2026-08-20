"""路径解析：数据目录 / Web 目录。

数据目录优先级：环境变量 STUDIO_DATA_DIR > 仓库根目录下的 data/。
测试中通过环境变量或工厂参数指向临时目录。
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """运行时数据目录（自动创建）。"""
    root = Path(os.environ.get("STUDIO_DATA_DIR", REPO_ROOT / "data"))
    (root / "projects").mkdir(parents=True, exist_ok=True)
    return root


def projects_dir() -> Path:
    p = data_dir() / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_dir(project_id: str) -> Path:
    p = projects_dir() / project_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def episode_dir(project_id: str, idx: int) -> Path:
    p = project_dir(project_id) / "episodes" / f"e{idx:02d}"
    (p / "shots").mkdir(parents=True, exist_ok=True)
    return p


def web_dir() -> Path:
    return REPO_ROOT / "web"


def config_path() -> Path:
    return data_dir() / "config.json"

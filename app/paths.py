"""路径解析：数据目录 / 模型目录 / Web 目录。

数据目录优先级：环境变量 STUDIO_DATA_DIR > 仓库根目录下的 data/。
模型根目录优先级：环境变量 STUDIO_MODELS_DIR > 仓库根目录下的 models/
（统一规范：所有离线模型一律存放在项目根 models/ 下，与运行时 cwd 无关）。
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


def models_root() -> Path:
    """离线模型根目录（项目根 models/，可被 STUDIO_MODELS_DIR 覆盖）。

    统一布局规范（详见 app/models_registry.py）：
    - ``models/<能力>/<预设名>/``     各预设模型文件（download_models.py 下载）
    - ``models/<能力>/_shared/<组件>/`` 跨预设共享组件（如 Wan 的 umt5 tokenizer）
    - ``models/_cache/``              ModelScope 下载缓存
    """
    return Path(os.environ.get("STUDIO_MODELS_DIR", REPO_ROOT / "models"))


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

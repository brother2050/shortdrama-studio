"""模型路径统一解析（全平台唯一入口）。

解决问题：模型目录"位置各异、路径格式千奇百怪"——
- 相对路径一律相对**项目根**解析（不再依赖运行时 cwd）；
- 支持 ``~`` 展开、尾部斜杠清理、正反斜杠；
- 支持直接填预设名（如 ``qwen2.5-1.5b`` → models/llm/qwen2.5-1.5b）；
- 路径不存在时报错附带下载指引（预设名 + 命令），不静默失败。

所有适配器的模型参数（model_path / model_id / model_dir /
checkpoint_dir / model）都经过 :func:`resolve_model_path`。
"""
from __future__ import annotations

import os
from pathlib import Path

from app import models_registry, paths


class ModelPathError(ValueError):
    """模型路径解析失败（中文可读，含下载指引）。"""


def resolve_model_path(raw: str | None, capability: str,
                       preset: str | None = None,
                       *, must_exist: bool = True) -> Path | None:
    """把用户填写的模型路径/预设名解析为绝对路径。

    解析顺序：
    1. 空 → 有 preset 时取 ``models/<cap>/<preset>/``，否则返回 None；
    2. 注册表预设名（如 ``qwen2.5-1.5b``）→ ``models/<cap>/<名字>/``；
    3. 绝对路径 → 展开 ``~``；
    4. 相对路径 → 相对**项目根**（REPO_ROOT）解析；以 ``models/`` 开头且
       设置了 ``STUDIO_MODELS_DIR`` 时，额外尝试覆盖位置（取首个存在者）；
    5. ``must_exist`` 时校验存在，失败抛 :class:`ModelPathError`。

    返回 None 仅当输入为空且无法从 preset 推断。
    """
    text = _normalize(raw, preset)
    if text is None:
        return None

    # 注册表预设名 → models/<cap>/<name>/
    hit = models_registry.find_preset(capability, text)
    if hit is not None:
        target = hit.local_dir()
    else:
        cands = _candidates(text)
        # 取首个实际存在的候选；均不存在时保留首选（供报错/判存用）
        target = next((c for c in cands if c.exists()), cands[0])

    if must_exist and not target.exists():
        raise ModelPathError(_not_found_message(capability, text, target))
    return target


def _normalize(raw: str | None, preset: str | None) -> str | None:
    """清洗用户输入：去空白、统一斜杠、去尾部分隔符；空值回退 preset。"""
    text = str(raw or "").strip().rstrip("/\\").replace("\\", "/")
    if text:
        return text
    return str(preset).strip() if preset else None


def _candidates(text: str) -> list[Path]:
    """相对路径的候选解析位置（按优先级，绝对路径仅一个）。"""
    p = Path(text).expanduser()
    if p.is_absolute():
        return [p]
    cands = [paths.REPO_ROOT / p]
    # "models/..." 前缀：显式设置 STUDIO_MODELS_DIR 时也尝试覆盖位置
    # （未设置时 models_root() == REPO_ROOT/models，两者本就相同）
    if text.startswith("models/") and os.environ.get("STUDIO_MODELS_DIR"):
        cands.append(paths.models_root() / text[len("models/"):])
    return cands


def model_source(raw: str | None, capability: str,
                 preset: str | None = None) -> tuple[str, bool]:
    """解析"本地目录 or 在线 repo id"二元来源（llm/asr 等直载型后端用）。

    返回 ``(source, is_local)``：
    - 本地路径存在 → ``(绝对路径字符串, True)``；
    - 形如 ``org/repo`` 且本地不存在 → ``(原文, False)`` 在线加载；
    - 其余情况抛 :class:`ModelPathError`（附下载指引）。
    """
    text = _normalize(raw, preset)
    if text is None:
        raise ModelPathError(
            f"请先在设置页填写模型（{capability}），或用模型预设下拉自动填充。"
            f"下载：python scripts/download_models.py --capability {capability}")

    hit = models_registry.find_preset(capability, text)
    if hit is not None:
        if hit.is_downloaded():
            return str(hit.local_dir()), True
        raise ModelPathError(_not_found_message(capability, text,
                                                hit.local_dir()))

    local = resolve_model_path(text, capability, must_exist=False)
    if local is not None and local.exists():
        return str(local), True
    # 形如 org/repo 的 ModelScope 仓库 id → 允许在线加载
    if "/" in text and not text.startswith(("models/", "./", "../", "~/")):
        return text, False
    raise ModelPathError(_not_found_message(capability, text, local))


def ensure_diffsynth_base_path() -> None:
    """让 DiffSynth 自动下载也落到项目根 models/（而非 cwd/models）。

    DiffSynth 的 ``ModelConfig(model_id=...)`` 下载目录由
    ``DIFFSYNTH_MODEL_BASE_PATH`` 决定，默认 ``./models``（相对 cwd，
    正是"模型散落各处"的根源）。统一锚定到项目根 models/。
    """
    os.environ.setdefault("DIFFSYNTH_MODEL_BASE_PATH", str(paths.models_root()))


def _not_found_message(capability: str, raw: str, target: Path | None) -> str:
    known = ", ".join(models_registry.REGISTRY.get(capability, {}))
    cmd = (f"python scripts/download_models.py --capability {capability}"
           f" --preset <预设名>")
    where = f"（解析为 {target}）" if target else ""
    return (f"模型路径不存在: {raw}{where}。"
            f"可用预设: {known}。离线下载: {cmd}；"
            f"模型统一存放在项目根 models/ 目录下。")

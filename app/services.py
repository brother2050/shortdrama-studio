"""业务服务层：REST 路由与对话编排共用的同一套操作（保证行为一致、可测）。"""
from __future__ import annotations

import logging
import shutil
import sys
from typing import Any

from app import paths
from app.adapters import registry, which_ffmpeg
from app.adapters.base import CAPABILITIES
from app.config import SettingsError, get_settings
from app.continuity import load_assets
from app.events import get_bus
from app.pipeline import (STAGES, PipelineError, episode_stage_statuses,
                          load_durations, load_script, load_storyboard,
                          make_stage_fn, start_pipeline, sync_episode_status)
from app.store import get_store
from app.tasks import TaskManager, get_task_manager
from app.vram import vram_summary

logger = logging.getLogger("app.services")


class ServiceError(ValueError):
    """面向用户的业务错误（中文、可操作）。"""


# ----------------------------------------------------------------------
# 项目
# ----------------------------------------------------------------------
def create_project(name: str, genre: str = "", style: str = "", premise: str = "",
                   episodes_planned: int = 3, config: dict | None = None) -> dict:
    if not name or not name.strip():
        raise ServiceError("剧名不能为空")
    cfg = dict(config or {})
    if episodes_planned:
        cfg.setdefault("episode_defaults", {})["episodes_planned"] = int(episodes_planned)
    store = get_store()
    project = store.create_project(name.strip(), genre.strip(), style.strip(),
                                   premise.strip(), cfg)
    get_bus().publish("project", project_id=project["id"], event="created")
    return project


def project_detail(pid: str) -> dict:
    store = get_store()
    project = store.get_project(pid)
    if not project:
        raise ServiceError(f"项目不存在: {pid}")
    episodes = []
    for ep in store.list_episodes(pid):
        eps = dict(ep)
        eps["stages"] = episode_stage_statuses(project, ep)
        last = store.list_tasks(episode_id=ep["id"], limit=1)
        eps["last_task"] = last[0] if last else None
        episodes.append(eps)
    return {"project": project, "assets": load_assets(pid), "episodes": episodes}


def delete_project(pid: str) -> None:
    store = get_store()
    if not store.get_project(pid):
        raise ServiceError(f"项目不存在: {pid}")
    store.delete_project(pid)
    shutil.rmtree(paths.project_dir(pid), ignore_errors=True)
    get_bus().publish("project", project_id=pid, event="deleted")


def patch_project(pid: str, fields: dict) -> dict:
    store = get_store()
    if not store.get_project(pid):
        raise ServiceError(f"项目不存在: {pid}")
    return store.update_project(pid, **fields)


# ----------------------------------------------------------------------
# 分集
# ----------------------------------------------------------------------
def create_episode(pid: str, title: str = "", synopsis: str = "") -> dict:
    store = get_store()
    if not store.get_project(pid):
        raise ServiceError(f"项目不存在: {pid}")
    return store.create_episode(pid, title.strip(), synopsis.strip())


def episode_detail(pid: str, idx: int) -> dict:
    store = get_store()
    project = store.get_project(pid)
    if not project:
        raise ServiceError(f"项目不存在: {pid}")
    ep = store.get_episode_by_idx(pid, idx)
    if not ep:
        raise ServiceError(f"第 {idx} 集不存在")
    sb = load_storyboard(pid, idx) or {"shots": []}
    edir = paths.episode_dir(pid, idx)
    durations = load_durations(pid, idx)
    shots = []
    for s in sb.get("shots", []):
        sd = edir / "shots" / f"s{s['idx']:03d}"
        vo_dur = durations.get(int(s["idx"]))
        if vo_dur is not None:
            s = {**s, "vo_duration": vo_dur}
        shots.append({
            **s,
            "keyframe": str(sd / "keyframe.png") if (sd / "keyframe.png").exists() else None,
            "clip": str(sd / "clip.mp4") if (sd / "clip.mp4").exists() else None,
            "vo": str(sd / "vo.wav") if (sd / "vo.wav").exists() else None,
        })
    script = load_script(pid, idx)
    artifacts = {
        "worldview_md": str(paths.project_dir(pid) / "worldview.md"),
        "script_md": str(edir / "script.md") if (edir / "script.md").exists() else None,
        "episode_srt": str(edir / "episode.srt") if (edir / "episode.srt").exists() else None,
        "episode_mp4": str(edir / "episode.mp4") if (edir / "episode.mp4").exists() else None,
        "timeline": str(edir / "timeline.json") if (edir / "timeline.json").exists() else None,
    }
    return {"project": project, "episode": ep,
            "stages": episode_stage_statuses(project, ep), "shots": shots,
            "script": script, "artifacts": artifacts,
            "tasks": store.list_tasks(episode_id=ep["id"])}


def generate_episode(eid: str, stage: str = "all", force: bool = False) -> dict:
    store = get_store()
    ep = store.get_episode(eid)
    if not ep:
        raise ServiceError(f"分集不存在: {eid}")
    return start_pipeline(eid, stage, force)


# ----------------------------------------------------------------------
# 任务（手工重试 / 取消）
# ----------------------------------------------------------------------
def retry_task(tid: str, tm: TaskManager | None = None) -> dict:
    """手工重试任务（唯一重试入口；代码中无自动重试）。

    优先复用任务首次提交的执行函数（保证与首次执行完全相同的代码路径）；
    函数已丢失（服务重启过）时按阶段重建（force 重跑）。
    """
    tm = tm or get_task_manager()
    store = get_store()
    task = store.get_task(tid)
    if not task:
        raise ServiceError(f"任务不存在: {tid}")
    if task["stage"] not in STAGES:
        raise ServiceError(f"任务阶段 {task['stage']} 不支持重试")
    project = store.get_project(task["project_id"])
    if not project:
        raise ServiceError("项目不存在")
    episode = store.get_episode(task["episode_id"]) if task["episode_id"] else None
    if task["stage"] != "worldview" and episode is None:
        raise ServiceError("分集不存在，无法重试")

    def build(t: dict):
        stored = tm.stored_fn(tid)
        if stored is not None:
            return stored
        return make_stage_fn(project, episode, t["stage"], force=True)

    return tm.retry(tid, build)


def retry_failed(project_id: str | None = None) -> dict:
    """重试最近一个失败任务（对话「重试失败的任务」入口）。"""
    store = get_store()
    failed = store.list_tasks(project_id=project_id, status="failed", limit=1)
    if not failed:
        failed = store.list_tasks(status="failed", limit=1)
    if not failed:
        raise ServiceError("没有失败的任务可重试")
    return retry_task(failed[0]["id"])


def cancel_task(tid: str) -> dict:
    tm = get_task_manager()
    try:
        task = tm.cancel(tid)
    except ValueError as exc:
        raise ServiceError(str(exc)) from exc
    if task.get("episode_id"):
        sync_episode_status(task["episode_id"])
    return task


def task_list(**filters) -> list[dict]:
    return get_store().list_tasks(**filters)


# ----------------------------------------------------------------------
# 设置 / 系统
# ----------------------------------------------------------------------
def update_settings(partial: dict) -> dict:
    """合并更新设置；能力的后端/参数变化时释放其旧模型（多引擎切换显存回收）。"""
    try:
        settings = get_settings()
        before = {cap: settings.capability(cap) for cap in CAPABILITIES}
        result = settings.update(partial)
    except SettingsError as exc:
        raise ServiceError(str(exc)) from exc
    for cap in CAPABILITIES:
        after = settings.capability(cap)
        if before[cap]["backend"] != after["backend"] or \
           before[cap]["params"] != after["params"]:
            registry.unload_capability(cap)
    return result


def reset_settings() -> dict:
    """恢复出厂默认设置，并释放所有已加载模型（配置全部回退）。"""
    settings = get_settings()
    result = settings.reset()
    registry.unload_all()
    logger.info("设置已恢复默认（全部能力回到 auto，参数清空）")
    return result


def system_health() -> dict:
    import platform

    settings = get_settings()
    store = get_store()
    caps: dict[str, Any] = {}
    for cap in ("llm", "tts", "image", "video", "asr"):
        specs = registry.list_specs(cap)
        conf = settings.capability(cap)
        try:
            resolved = registry.resolve(cap, conf["backend"], conf["params"])
            active = type(resolved).spec.name
        except Exception as exc:  # noqa: BLE001 —— 健康页需要展示而非抛出
            active = f"（不可用）{exc}"
        caps[cap] = {"configured": conf["backend"], "active": str(active),
                     "backends": specs}
    data = paths.data_dir()
    free_gb = -1.0
    try:
        usage = shutil.disk_usage(data)
        free_gb = round(usage.free / 1024 ** 3, 1)
    except OSError:
        pass
    tm = get_task_manager()
    return {
        "app": "shortdrama-studio",
        "version": sys.modules["app"].__version__,
        "python": platform.python_version(),
        "ffmpeg": which_ffmpeg() or "（未安装）",
        "data_dir": str(data),
        "disk_free_gb": free_gb,
        "tasks": tm.status_summary(),
        "counts": {"projects": len(store.list_projects())},
        "vram": vram_summary(),
        "capabilities": caps,
    }

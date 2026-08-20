"""任务接口：列表 / 手工重试 / 取消。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.pipeline import STAGE_LABELS
from app.services import ServiceError, cancel_task, retry_task, task_list

router = APIRouter(prefix="/api", tags=["tasks"])


@router.get("/tasks")
def get_tasks(project_id: str | None = None, episode_id: str | None = None,
              status: str | None = None, limit: int = 200):
    return task_list(project_id=project_id, episode_id=episode_id,
                     status=status, limit=limit)


@router.get("/tasks/stages")
def stages_meta():
    """阶段元信息（前端流水线条渲染用）。"""
    return [{"stage": k, "label": v} for k, v in STAGE_LABELS.items()]


@router.post("/tasks/{tid}/retry")
def post_retry(tid: str):
    try:
        task = retry_task(tid)
    except ServiceError as exc:
        code = 404 if "不存在" in str(exc) else 409
        raise HTTPException(code, str(exc)) from exc
    return task


@router.post("/tasks/{tid}/cancel")
def post_cancel(tid: str):
    try:
        return cancel_task(tid)
    except (ServiceError, KeyError) as exc:
        code = 404 if "不存在" in str(exc) else 409
        raise HTTPException(code, str(exc)) from exc

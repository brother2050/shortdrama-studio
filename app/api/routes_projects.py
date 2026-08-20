"""项目与分集接口：创建/详情/删除/分集/生成。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.pipeline import PipelineError
from app.schemas import EpisodeCreate, GenerateRequest, ProjectCreate, ProjectPatch
from app.services import (ServiceError, create_episode, create_project,
                          episode_detail, generate_episode, patch_project,
                          project_detail)
from app.store import get_store

router = APIRouter(prefix="/api", tags=["projects"])


def _svc(exc: Exception) -> HTTPException:
    return HTTPException(404 if "不存在" in str(exc) else 400, str(exc))


@router.get("/projects")
def list_projects():
    return get_store().list_projects()


@router.post("/projects", status_code=201)
def post_project(req: ProjectCreate):
    try:
        return create_project(**req.model_dump())
    except ServiceError as exc:
        raise _svc(exc) from exc


@router.get("/projects/{pid}")
def get_project(pid: str):
    try:
        return project_detail(pid)
    except ServiceError as exc:
        raise _svc(exc) from exc


@router.patch("/projects/{pid}")
def patch(pid: str, req: ProjectPatch):
    try:
        return patch_project(pid, req.model_dump(exclude_none=True))
    except ServiceError as exc:
        raise _svc(exc) from exc


@router.delete("/projects/{pid}", status_code=204)
def delete_project(pid: str):
    from app.services import delete_project as _del
    try:
        _del(pid)
    except ServiceError as exc:
        raise _svc(exc) from exc


@router.get("/projects/{pid}/episodes")
def list_episodes(pid: str):
    return get_store().list_episodes(pid)


@router.post("/projects/{pid}/episodes", status_code=201)
def post_episode(pid: str, req: EpisodeCreate):
    try:
        return create_episode(pid, req.title, req.synopsis)
    except ServiceError as exc:
        raise _svc(exc) from exc


@router.get("/projects/{pid}/episodes/{idx}")
def get_episode(pid: str, idx: int):
    try:
        return episode_detail(pid, idx)
    except ServiceError as exc:
        raise _svc(exc) from exc


@router.post("/episodes/{eid}/generate")
def post_generate(eid: str, req: GenerateRequest):
    try:
        return generate_episode(eid, req.stage, req.force)
    except (ServiceError, PipelineError) as exc:
        raise _svc(exc) from exc

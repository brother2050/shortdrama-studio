"""pydantic 数据模型：API 请求/响应与内部结构。

仅描述"形状"，业务校验放在 services 层（便于返回带上下文的中文错误）。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Stage = Literal[
    "worldview", "script", "storyboard", "voiceover",
    "keyframes", "clips", "subtitles", "compose",
]
TaskStatus = Literal["pending", "running", "succeeded", "failed", "canceled"]


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="剧名")
    genre: str = Field("都市情感", description="题材")
    style: str = Field("", description="视觉风格描述，空则用全局默认")
    premise: str = Field("", description="一句话创意/梗概")
    episodes_planned: int = Field(3, ge=1, le=52, description="计划集数")
    config: dict[str, Any] = Field(default_factory=dict, description="项目级能力覆盖")


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    genre: Optional[str] = None
    style: Optional[str] = None
    premise: Optional[str] = None
    config: Optional[dict[str, Any]] = None


class EpisodeCreate(BaseModel):
    title: str = Field("", description="本集标题，空则自动")
    synopsis: str = Field("", description="本集梗概，空则由 LLM 依据前情续写")


class GenerateRequest(BaseModel):
    stage: str = Field("all", description='阶段名或 "all"')
    force: bool = Field(False, description="强制重跑（忽略断点）")


class ChatRequest(BaseModel):
    project_id: Optional[str] = None
    message: str = Field(..., min_length=1, max_length=2000)


class SettingsUpdate(BaseModel):
    settings: dict[str, Any]


class ActionCard(BaseModel):
    """对话中执行的动作卡片（前端可渲染、测试可断言）。"""
    intent: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True


class ChatResponse(BaseModel):
    reply: str
    actions: list[ActionCard] = Field(default_factory=list)
    project_id: Optional[str] = None
    message_id: Optional[str] = None

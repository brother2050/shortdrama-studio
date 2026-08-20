"""对话接口：POST /api/chat（意图 → 动作 → 回复）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.chat import handle_chat, parse_intent
from app.schemas import ChatRequest, ChatResponse
from app.store import get_store

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        result = handle_chat(req.project_id, req.message.strip())
    except Exception as exc:  # noqa: BLE001 —— 对话永不 500，错误转回复
        raise HTTPException(500, f"对话处理异常: {exc}") from exc
    return result


@router.get("/chat/intent")
def intent_preview(message: str):
    """意图解析预览（调试/测试用）。"""
    return parse_intent(message)


@router.get("/projects/{pid}/chat")
def chat_history(pid: str, limit: int = 100):
    return get_store().list_chat(pid, limit)

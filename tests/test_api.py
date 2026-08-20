"""REST API 测试：项目/生成/对话/任务/设置/系统/媒体/SSE（TestClient）。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import get_store
from tests.conftest import wait_terminal

client = TestClient(app)


@pytest.fixture()
def project_id():
    res = client.post("/api/projects", json={
        "name": "API剧", "genre": "都市情感", "premise": "深夜便利店",
        "config": {"episode_defaults": {"shots_per_episode": 2},
                   "image": {"params": {"width": 320, "height": 180}},
                   "video": {"params": {"width": 320, "height": 180}}}})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_version_endpoint():
    res = client.get("/api/version")
    assert res.status_code == 200
    assert res.json()["app"] == "shortdrama-studio"
    assert "version" in res.json()


def test_index_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "ShortDrama Studio" in res.text


# -- 项目 -----------------------------------------------------------------
def test_project_crud_and_validation(project_id):
    assert client.get(f"/api/projects/{project_id}").status_code == 200
    res = client.patch(f"/api/projects/{project_id}", json={"genre": "悬疑"})
    assert res.json()["genre"] == "悬疑"
    # 空名 → 400
    assert client.post("/api/projects", json={"name": " "}).status_code == 400
    assert client.get("/api/projects/nope").status_code == 404
    assert client.delete(f"/api/projects/{project_id}").status_code == 204
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_episodes_and_generate(project_id):
    res = client.post(f"/api/projects/{project_id}/episodes", json={"title": "第1集"})
    assert res.status_code == 201
    eid = res.json()["id"]

    gen = client.post(f"/api/episodes/{eid}/generate", json={"stage": "all"})
    assert gen.status_code == 200, gen.text
    assert wait_terminal(eid) == "ready"

    detail = client.get(f"/api/projects/{project_id}/episodes/1").json()
    assert all(v == "ready" for v in detail["stages"].values())
    assert len(detail["shots"]) == 2
    assert detail["artifacts"]["episode_mp4"]

    # 单阶段强制重跑
    again = client.post(f"/api/episodes/{eid}/generate",
                        json={"stage": "subtitles", "force": True})
    assert again.status_code == 200

    # 非法阶段
    bad = client.post(f"/api/episodes/{eid}/generate", json={"stage": "x"})
    assert bad.status_code in (400, 422)


# -- 对话 -----------------------------------------------------------------
def test_chat_endpoint_roundtrip():
    res = client.post("/api/chat", json={"message": "创建一部 2 集的悬疑短剧，名字叫《谜》",
                                         "project_id": None})
    assert res.status_code == 200
    body = res.json()
    assert "《谜》" in body["reply"]
    assert body["project_id"]

    res2 = client.post("/api/chat", json={
        "message": "现在什么进度", "project_id": body["project_id"]})
    assert res2.status_code == 200

    # 历史回放
    hist = client.get(f"/api/projects/{body['project_id']}/chat")
    assert hist.status_code == 200 and hist.json()

    # 意图预览（调试端点）
    intent = client.get("/api/chat/intent", params={"message": "重试配音"}).json()
    assert intent["intent"] == "regenerate_stage"


# -- 任务 -----------------------------------------------------------------
def test_tasks_endpoints(project_id):
    res = client.post(f"/api/projects/{project_id}/episodes", json={"title": "第1集"})
    eid = res.json()["id"]
    client.post(f"/api/episodes/{eid}/generate", json={"stage": "all"})
    wait_terminal(eid)

    tasks = client.get("/api/tasks").json()
    assert len(tasks) >= 8
    stages = client.get("/api/tasks/stages").json()
    assert [s["stage"] for s in stages] == ["worldview", "script", "storyboard",
                                            "voiceover", "keyframes", "clips",
                                            "subtitles", "compose"]
    # 过滤
    ok = client.get("/api/tasks", params={"status": "succeeded"}).json()
    assert all(t["status"] == "succeeded" for t in ok)

    # 成功任务重试允许；取消已终结任务 → 409
    tid = tasks[0]["id"]
    assert client.post(f"/api/tasks/{tid}/cancel").status_code == 409
    assert client.post("/api/tasks/nope/retry").status_code == 404


# -- 设置 -----------------------------------------------------------------
def test_settings_roundtrip_and_validation():
    got = client.get("/api/settings").json()
    assert got["capabilities"]["llm"]["backend"] == "auto"

    put = client.put("/api/settings", json={"settings": {
        "capabilities": {"tts": {"backend": "mock", "params": {}}},
        "episode_defaults": {"shots_per_episode": 6}}})
    assert put.status_code == 200
    assert put.json()["episode_defaults"]["shots_per_episode"] == 6
    assert put.json()["episode_defaults"]["target_clip_seconds"] == 5.0  # 深度合并保留默认

    bad = client.put("/api/settings", json={"settings": {
        "capabilities": {"nope": {}}}})
    assert bad.status_code == 400


# -- 系统 -----------------------------------------------------------------
def test_system_health_and_backends():
    health = client.get("/api/system/health").json()
    for cap in ("llm", "tts", "image", "video", "asr"):
        assert cap in health["capabilities"]
        assert "active" in health["capabilities"][cap]

    backends = client.get("/api/system/backends").json()
    assert "llm" in backends and any(b["name"] == "mock" for b in backends["llm"])


# -- 媒体 -----------------------------------------------------------------
def test_media_serving_and_traversal_guard(project_id):
    res = client.post(f"/api/projects/{project_id}/episodes", json={"title": "第1集"})
    eid = res.json()["id"]
    client.post(f"/api/episodes/{eid}/generate", json={"stage": "all"})
    wait_terminal(eid)

    ok = client.get(f"/api/projects/{project_id}/media/worldview.md")
    assert ok.status_code == 200
    assert "世界" in ok.text or len(ok.text) > 0

    mp4 = client.get(f"/api/projects/{project_id}/media/episodes/e01/episode.mp4")
    assert mp4.status_code == 200
    assert mp4.content[4:8] == b"ftyp"          # MP4 ftyp 盒
    assert len(mp4.content) > 1000

    missing = client.get(f"/api/projects/{project_id}/media/episodes/e01/none.mp4")
    assert missing.status_code == 404

    # 目录穿越被拦截
    evil = client.get(f"/api/projects/{project_id}/media/../../config.json")
    assert evil.status_code in (403, 404)
    evil2 = client.get(f"/api/projects/{project_id}/media/..%2F..%2Fdata%2Fstudio.db")
    assert evil2.status_code in (403, 404)


# -- SSE ------------------------------------------------------------------
def test_sse_stream_emits_hello_and_unsubscribes():
    """直接消费 SSE 生成器：验证 hello 事件、媒体类型与订阅清理。

    （不走 TestClient.stream：该版本客户端会先跑完整个 ASGI 应用，
    无限事件流会导致其永久阻塞。）
    """
    import asyncio

    from app.api.routes_system import sse_events

    async def consume():
        resp = await sse_events()
        assert resp.media_type.startswith("text/event-stream")
        assert resp.headers["Cache-Control"] == "no-cache"
        agen = resp.body_iterator
        chunk = await asyncio.wait_for(agen.__anext__(), timeout=5)
        await agen.aclose()
        return chunk

    chunk = asyncio.run(consume())
    assert "event: hello" in chunk

    # 事件流真实推送（publish → 订阅队列收到）
    from app.events import get_bus

    async def receive_one():
        resp = await sse_events()
        agen = resp.body_iterator
        await agen.__anext__()                      # 先取 hello
        get_bus().publish("task", task_id="t1", stage="script", status="running")
        second = await asyncio.wait_for(agen.__anext__(), timeout=5)
        await agen.aclose()
        return second

    second = asyncio.run(receive_one())
    assert '"task"' in second and "running" in second

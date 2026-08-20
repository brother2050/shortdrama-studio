"""SQLite 存储：标准库 sqlite3，WAL 模式，线程安全。

所有 JSON 字段以 TEXT 存储（ensure_ascii=False，人可直接读库）。
时间统一 ISO8601（UTC）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    genre       TEXT NOT NULL DEFAULT '',
    style       TEXT NOT NULL DEFAULT '',
    premise     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'draft',
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    synopsis    TEXT NOT NULL DEFAULT '',
    summary     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'draft',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(project_id, idx)
);
CREATE TABLE IF NOT EXISTS tasks (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    episode_id     TEXT REFERENCES episodes(id) ON DELETE CASCADE,
    stage          TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    params_json    TEXT NOT NULL DEFAULT '{}',
    error          TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT '',
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL,
    started_at     TEXT NOT NULL DEFAULT '',
    finished_at    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id          TEXT PRIMARY KEY,
    project_id  TEXT,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    actions_json TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodes_project ON episodes(project_id, idx);
CREATE INDEX IF NOT EXISTS idx_tasks_project   ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_episode   ON tasks(episode_id, stage, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_project    ON chat_messages(project_id, created_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _loads(text: str, default: Any):
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


class Store:
    """数据访问对象（进程内单例风格，测试中可独立实例化）。"""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- 通用 ---------------------------------------------------------------
    def _exec(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, args)
            self._conn.commit()

    def _query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # -- projects -----------------------------------------------------------
    def create_project(self, name: str, genre: str, style: str, premise: str,
                       config: dict | None = None) -> dict:
        pid, ts = new_id(), now()
        self._exec(
            "INSERT INTO projects(id,name,genre,style,premise,status,config_json,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (pid, name, genre, style, premise, "active", _dumps(config or {}), ts, ts),
        )
        return self.get_project(pid)

    def get_project(self, pid: str) -> dict | None:
        rows = self._query("SELECT * FROM projects WHERE id=?", (pid,))
        return self._project_row(rows[0]) if rows else None

    def list_projects(self) -> list[dict]:
        return [self._project_row(r) for r in
                self._query("SELECT * FROM projects ORDER BY created_at DESC")]

    def update_project(self, pid: str, **fields: Any) -> dict | None:
        allowed = {"name", "genre", "style", "premise", "status"}
        sets, args = [], []
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            sets.append(f"{k}=?")
            args.append(v)
        if "config" in fields and fields["config"] is not None:
            sets.append("config_json=?")
            args.append(_dumps(fields["config"]))
        if not sets:
            return self.get_project(pid)
        sets.append("updated_at=?")
        args.append(now())
        args.append(pid)
        self._exec(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", tuple(args))
        return self.get_project(pid)

    def delete_project(self, pid: str) -> None:
        """级联删除：对话消息无外键约束，需显式清理；其余靠 FK ON DELETE CASCADE。"""
        with self._lock:
            self._conn.execute("DELETE FROM chat_messages WHERE project_id=?", (pid,))
            self._conn.execute("DELETE FROM projects WHERE id=?", (pid,))
            self._conn.commit()

    @staticmethod
    def _project_row(r: dict) -> dict:
        r["config"] = _loads(r.pop("config_json"), {})
        return r

    # -- episodes -------------------------------------------------------------
    def create_episode(self, project_id: str, title: str, synopsis: str) -> dict:
        rows = self._query(
            "SELECT COALESCE(MAX(idx),0)+1 AS nx FROM episodes WHERE project_id=?",
            (project_id,))
        idx = int(rows[0]["nx"])
        eid, ts = new_id(), now()
        self._exec(
            "INSERT INTO episodes(id,project_id,idx,title,synopsis,summary,status,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (eid, project_id, idx, title, synopsis, "", "draft", ts, ts))
        return self.get_episode(eid)

    def get_episode(self, eid: str) -> dict | None:
        rows = self._query("SELECT * FROM episodes WHERE id=?", (eid,))
        return rows[0] if rows else None

    def get_episode_by_idx(self, project_id: str, idx: int) -> dict | None:
        rows = self._query(
            "SELECT * FROM episodes WHERE project_id=? AND idx=?", (project_id, idx))
        return rows[0] if rows else None

    def list_episodes(self, project_id: str) -> list[dict]:
        return self._query(
            "SELECT * FROM episodes WHERE project_id=? ORDER BY idx", (project_id,))

    def update_episode(self, eid: str, **fields: Any) -> dict | None:
        allowed = {"title", "synopsis", "summary", "status"}
        sets, args = [], []
        for k, v in fields.items():
            if k in allowed and v is not None:
                sets.append(f"{k}=?")
                args.append(v)
        if not sets:
            return self.get_episode(eid)
        sets.append("updated_at=?")
        args.append(now())
        args.append(eid)
        self._exec(f"UPDATE episodes SET {', '.join(sets)} WHERE id=?", tuple(args))
        return self.get_episode(eid)

    # -- tasks ----------------------------------------------------------------
    def create_task(self, project_id: str, episode_id: str | None, stage: str,
                    params: dict | None = None) -> dict:
        tid, ts = new_id(), now()
        self._exec(
            "INSERT INTO tasks(id,project_id,episode_id,stage,status,params_json,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (tid, project_id, episode_id, stage, "pending", _dumps(params or {}), ts))
        return self.get_task(tid)

    def get_task(self, tid: str) -> dict | None:
        rows = self._query("SELECT * FROM tasks WHERE id=?", (tid,))
        return self._task_row(rows[0]) if rows else None

    def list_tasks(self, project_id: str | None = None, episode_id: str | None = None,
                   status: str | None = None, limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM tasks WHERE 1=1", []
        if project_id:
            sql += " AND project_id=?"
            args.append(project_id)
        if episode_id:
            sql += " AND episode_id=?"
            args.append(episode_id)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        return [self._task_row(r) for r in self._query(sql, tuple(args))]

    def latest_task(self, episode_id: str, stage: str) -> dict | None:
        rows = self._query(
            "SELECT * FROM tasks WHERE episode_id=? AND stage=?"
            " ORDER BY created_at DESC LIMIT 1", (episode_id, stage))
        return self._task_row(rows[0]) if rows else None

    def update_task(self, tid: str, **fields: Any) -> dict | None:
        colmap = {"status": "status", "error": "error", "note": "note",
                  "params": "params_json", "artifacts": "artifacts_json"}
        sets, args = [], []
        for k, v in fields.items():
            if k == "params" and v is not None:
                sets.append("params_json=?")
                args.append(_dumps(v))
            elif k == "artifacts" and v is not None:
                sets.append("artifacts_json=?")
                args.append(_dumps(v))
            elif k in colmap and v is not None:
                sets.append(f"{colmap[k]}=?")
                args.append(str(v))
        if not sets:
            return self.get_task(tid)
        if "running" in (fields.get("status"),):
            sets.append("started_at=?")
            args.append(now())
        if fields.get("status") in ("succeeded", "failed", "canceled"):
            sets.append("finished_at=?")
            args.append(now())
        args.append(tid)
        self._exec(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", tuple(args))
        return self.get_task(tid)

    @staticmethod
    def _task_row(r: dict) -> dict:
        r["params"] = _loads(r.pop("params_json"), {})
        r["artifacts"] = _loads(r.pop("artifacts_json"), [])
        return r

    # -- chat -----------------------------------------------------------------
    def add_chat(self, project_id: str | None, role: str, content: str,
                 actions: list | None = None) -> dict:
        mid, ts = new_id(), now()
        self._exec(
            "INSERT INTO chat_messages(id,project_id,role,content,actions_json,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (mid, project_id, role, content, _dumps(actions or []), ts))
        return {"id": mid, "project_id": project_id, "role": role,
                "content": content, "actions": actions or [], "created_at": ts}

    def list_chat(self, project_id: str, limit: int = 100) -> list[dict]:
        """按插入顺序返回最近 limit 条（rowid 递增，秒级时间戳相同时仍稳定）。"""
        rows = self._query(
            "SELECT rowid AS _rid, * FROM chat_messages WHERE project_id=?"
            " ORDER BY _rid DESC LIMIT ?",
            (project_id, limit))
        rows.reverse()
        for r in rows:
            r.pop("_rid", None)
            r["actions"] = _loads(r.pop("actions_json"), [])
        return rows


_store: Store | None = None


def get_store() -> Store:
    """进程级单例（数据目录由 STUDIO_DATA_DIR 决定）。"""
    global _store
    if _store is None:
        from app import paths
        _store = Store(paths.data_dir() / "studio.db")
    return _store


def reset_store() -> None:
    global _store
    if _store is not None:
        _store.close()
    _store = None

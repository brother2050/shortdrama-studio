"""连续性：多集连贯的核心（参考 Jellyfish 资产一致性 + Huobao 外貌/音色锁定）。

四层连续性：
1. 资产层：角色（人设/外貌/音色）与场景库存于 project.json，跨集复用；
2. 剧情层：前 N-1 集摘要滚动传递给剧本生成；
3. 视觉层：关键帧提示词自动锁定角色外貌 + 全局风格；
4. 听觉层：角色 → 音色一次性分配，各集 TTS 复用。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import paths

VOICE_POOL = ["female_warm", "male_deep", "female_bright", "male_warm", "narrator"]


def load_assets(project_id: str) -> dict[str, Any]:
    """读取项目资产（worldview 阶段产出）。"""
    f = paths.project_dir(project_id) / "project.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text("utf-8"))


def save_assets(project_id: str, assets: dict[str, Any]) -> None:
    f = paths.project_dir(project_id) / "project.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(assets, ensure_ascii=False, indent=2), "utf-8")


def assign_voices(characters: list[dict]) -> list[dict]:
    """为角色分配确定性音色（女主→暖女声，男主→沉男声，其余按池轮转）。"""
    used: list[str] = []
    pool_i = 0
    for ch in characters:
        if ch.get("voice"):
            voice = ch["voice"]
        else:
            role = ch.get("role", "")
            if "旁白" in role:
                voice = "narrator"
            elif "女主" in role or (role and role[-1] == "女"):
                voice = "female_warm"
            elif "男主" in role or (role and role[-1] == "男"):
                voice = "male_deep"
            else:
                voice = VOICE_POOL[pool_i % len(VOICE_POOL)]
                pool_i += 1
        while voice in used and voice != "narrator":
            voice = VOICE_POOL[(VOICE_POOL.index(voice) + 1) % len(VOICE_POOL)]
        used.append(voice)
        ch["voice"] = voice
    return characters


def character_brief(assets: dict) -> str:
    """角色卡简述（拼入剧本/分镜提示词，保证人设连贯）。"""
    lines = []
    for ch in assets.get("characters", []):
        lines.append(f"- {ch.get('name','?')}（{ch.get('role','')}）："
                     f"{ch.get('persona','')}；外貌固定为「{ch.get('appearance','')}」")
    return "\n".join(lines) or "- （暂无角色）"


def lock_prompt(shot: dict, assets: dict, global_style: str) -> str:
    """关键帧最终提示词 = 分镜画面 + 出场角色外貌锁定 + 全局风格。"""
    parts: list[str] = [shot.get("image_prompt") or shot.get("description", "")]
    appear = []
    for name in shot.get("characters", []):
        for ch in assets.get("characters", []):
            if ch.get("name") == name and ch.get("appearance"):
                appear.append(f"{name}: {ch['appearance']}")
    if appear:
        parts.append("角色外貌锁定（保持一致）: " + "; ".join(appear))
    if global_style:
        parts.append(f"风格: {global_style}")
    return "，".join(p for p in parts if p)


def previous_summaries(store, project_id: str, before_idx: int,
                       limit: int = 3) -> list[str]:
    """前情摘要（最近 limit 集，滚动窗口）。"""
    eps = store.list_episodes(project_id)
    prev = [e for e in eps if e["idx"] < before_idx]
    out = []
    for e in prev[-limit:]:
        s = e.get("summary") or e.get("synopsis") or ""
        if s:
            out.append(f"第{e['idx']}集《{e['title'] or ''}》：{s}")
    return out


def worldview_markdown(assets: dict) -> str:
    """世界观 → 人可读 Markdown。"""
    chs = "\n".join(
        f"| {c.get('name')} | {c.get('role','')} | {c.get('persona','')} | "
        f"{c.get('appearance','')} | {c.get('voice','')} |"
        for c in assets.get("characters", []))
    scs = "\n".join(f"- **{s['name']}**：{s.get('description','')}"
                    for s in assets.get("scenes", []))
    ols = "\n".join(f"{i+1}. {o}" for i, o in enumerate(assets.get("episode_outline", [])))
    return f"""# 《{assets.get('title','')}》世界观

- **一句话故事**：{assets.get('logline','')}
- **题材**：{assets.get('genre','')}　**风格**：{assets.get('style','')}
- **背景设定**：{assets.get('setting','')}

## 角色

| 姓名 | 定位 | 人设 | 外貌（锁定） | 音色 |
|---|---|---|---|---|
{chs or '| | | | | |'}

## 场景库

{scs or '（暂无）'}

## 分集大纲

{ols or '（暂无）'}
"""


def script_markdown(script: dict) -> str:
    """剧本 → 人可读 Markdown。"""
    blocks = [f"# {script.get('title','')}\n"]
    for sc in script.get("scenes", []):
        blocks.append(f"## {sc.get('name','')}（{sc.get('location','')}）\n")
        if sc.get("action"):
            blocks.append(f"> {sc['action']}\n")
        for ln in sc.get("lines", []):
            blocks.append(f"**{ln.get('speaker','旁白')}**（{ln.get('emotion','')}）：{ln.get('text','')}")
        blocks.append("")
    return "\n".join(blocks)

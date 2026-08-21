"""八阶段流水线：worldview → script → storyboard → voiceover → keyframes → clips → subtitles → compose。

执行模型：
- 每个阶段一个 Task（TaskManager 管理），阶段间顺序等待；
- 产物落盘即检查点：阶段开始时若产物齐备且未要求 force，则直接跳过（任务
  置 succeeded + note="skipped"），实现断点续跑；
- 失败即停（**无任何自动重试**），由用户通过 REST/UI/对话手工重试
  （重试单阶段或重跑整条流水线均从断点继续）；
- 适配器在阶段执行时才解析（设置修改即时生效）。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable

from app import paths
from app.adapters import registry
from app.adapters.asr_script import segments_to_srt
from app.composer import compose_episode
from app.config import get_settings, deep_merge
from app.continuity import (assign_voices, character_brief, load_assets,
                            lock_prompt, previous_summaries, save_assets,
                            script_markdown, worldview_markdown)
from app.events import get_bus
from app.store import get_store
from app.tasks import VALID_STAGES, get_task_manager

logger = logging.getLogger("app.pipeline")

STAGES: list[str] = list(VALID_STAGES)

STAGE_LABELS = {
    "worldview": "世界观", "script": "剧本", "storyboard": "分镜",
    "voiceover": "配音", "keyframes": "关键帧", "clips": "镜头片段",
    "subtitles": "字幕", "compose": "合成",
}


class PipelineError(RuntimeError):
    pass


# ----------------------------------------------------------------------
# 适配器解析（执行时）
# ----------------------------------------------------------------------
def resolve_adapter(capability: str, project: dict):
    conf = get_settings().capability(capability, project.get("config") or {})
    return registry.resolve(capability, conf["backend"], conf["params"])


def episode_effective_settings(project: dict) -> dict:
    gs = get_settings().as_dict()
    return deep_merge(gs.get("episode_defaults", {}),
                      (project.get("config") or {}).get("episode_defaults", {}))


def output_geometry() -> dict:
    vo = get_settings().as_dict().get("video_output", {})
    return {"width": int(vo.get("width", 1280)), "height": int(vo.get("height", 720)),
            "fps": int(vo.get("fps", 24))}


# ----------------------------------------------------------------------
# 产物读写与检查点
# ----------------------------------------------------------------------
def _read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text("utf-8"))


def _write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return path


def load_storyboard(project_id: str, idx: int) -> dict | None:
    return _read_json(paths.episode_dir(project_id, idx) / "storyboard.json")


def load_script(project_id: str, idx: int) -> dict | None:
    return _read_json(paths.episode_dir(project_id, idx) / "script.json")


def load_durations(project_id: str, idx: int) -> dict[int, float]:
    """配音阶段实测的镜头时长（镜头序号 → 秒）。无则空字典。"""
    data = _read_json(paths.episode_dir(project_id, idx) / "voiceover.json", {}) or {}
    return {int(k): float(v) for k, v in (data.get("durations") or {}).items()}


def shot_seconds(shot: dict, eff: dict, durations: dict[int, float]) -> float:
    """镜头时长优先级：配音实测 > 分镜提示 > 全局默认。"""
    return float(durations.get(int(shot.get("idx", 0)))
                 or shot.get("vo_duration")
                 or shot.get("duration_hint")
                 or eff.get("target_clip_seconds", 5.0))


def stage_complete(project: dict, episode: dict, stage: str) -> bool:
    """阶段产物是否齐备（断点检查点）。"""
    pid, idx = project["id"], episode["idx"]
    edir = paths.episode_dir(pid, idx)
    if stage == "worldview":
        assets = load_assets(pid)
        return bool(assets.get("characters"))
    if stage == "script":
        s = load_script(pid, idx)
        return bool(s and s.get("scenes"))
    if stage == "storyboard":
        sb = load_storyboard(pid, idx)
        return bool(sb and sb.get("shots"))
    sb = load_storyboard(pid, idx) or {"shots": []}
    shots = sb.get("shots", [])
    if not shots:
        return False
    if stage == "voiceover":
        return all((edir / "shots" / f"s{s['idx']:03d}" / "vo.wav").exists()
                   for s in shots)
    if stage == "keyframes":
        return all((edir / "shots" / f"s{s['idx']:03d}" / "keyframe.png").exists()
                   for s in shots)
    if stage == "clips":
        return all((edir / "shots" / f"s{s['idx']:03d}" / "clip.mp4").exists()
                   for s in shots)
    if stage == "subtitles":
        return (edir / "episode.srt").exists()
    if stage == "compose":
        return (edir / "episode.mp4").exists()
    return False


def episode_stage_statuses(project: dict, episode: dict) -> dict[str, str]:
    """各阶段状态：ready(产物齐备) / missing。供前端流水线条渲染。"""
    return {s: ("ready" if stage_complete(project, episode, s) else "missing")
            for s in STAGES}


# ----------------------------------------------------------------------
# LLM JSON 调用
# ----------------------------------------------------------------------
_SYSTEM = ("你是资深短剧总编剧，擅长连续短剧的多集连贯叙事。"
           "只输出一个合法 JSON 对象，不要输出解释、Markdown 代码块或其他文字。")


def _extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise PipelineError(f"LLM 未返回 JSON：{text[:200]}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise PipelineError(f"LLM JSON 解析失败: {exc}；原文片段: {text[start:start+200]}") from exc


def llm_json(stage: str, user_prompt: str, project: dict,
             progress: Callable | None = None) -> dict:
    llm = resolve_adapter("llm", project)
    resp = llm.run({"system": _SYSTEM, "messages": [
        {"role": "user", "content": f"[STAGE:{stage}]\n{user_prompt}"}]},
        progress=progress)
    return _extract_json(resp.get("text", ""))


# ----------------------------------------------------------------------
# 各阶段实现（fn(cancel, progress) -> artifacts）
# ----------------------------------------------------------------------
def stage_worldview(project: dict, cancel=None, progress=None) -> list[str]:
    pdir = paths.project_dir(project["id"])
    prompt = (f"剧名：《{project['name']}》\n题材：{project.get('genre') or '都市情感'}\n"
              f"创意：{project.get('premise') or '由你自由发挥'}\n"
              f"视觉风格：{project.get('style') or '电影感'}\n\n"
              "请输出世界观 JSON，字段："
              '{"title": str, "logline": str, "genre": str, "style": str, '
              '"setting": str, "characters": [{"name": str, "role": str, '
              '"persona": str, "appearance": str}], '
              '"scenes": [{"name": str, "description": str, "mood": str}], '
              '"episode_outline": [str]}（2-3 个主要角色，外貌描述具体可画）')
    if progress:
        progress("生成世界观与角色资产", 20.0)
    assets = llm_json("worldview", prompt, project, progress)
    if not assets.get("characters"):
        raise PipelineError("worldview 缺少 characters 字段")
    assets["title"] = assets.get("title") or project["name"]
    assets["characters"] = assign_voices(assets["characters"])
    save_assets(project["id"], assets)
    md = pdir / "worldview.md"
    md.write_text(worldview_markdown(assets), "utf-8")
    outs = [str(md), str(pdir / "project.json")]

    # 角色参考图（视觉一致性基础）：真实图像后端时为每个主要角色生成肖像，
    # keyframes 阶段作为参考图传入（Qwen-Image-Edit 等编辑模型可锁定外貌）。
    eff = episode_effective_settings(project)
    image_conf = get_settings().capability("image", project.get("config") or {})
    backend = image_conf.get("backend", "auto")
    if eff.get("character_refs", True) and backend not in ("mock", "auto"):
        try:
            outs.extend(_generate_character_refs(project, assets))
        except Exception as exc:  # 参考图失败不阻塞主线（关键帧退化为纯文生图）
            logger.warning("角色参考图生成失败（已跳过）：%s", exc)
    if progress:
        progress("世界观完成", 90.0)
    return outs


def _character_ref_dir(project_id: str) -> Path:
    return paths.project_dir(project_id) / "characters"


def _generate_character_refs(project: dict, assets: dict) -> list[str]:
    """为每个主要角色生成肖像参考图（characters/NAME.png，跨集复用）。"""
    image = resolve_adapter("image", project)
    eff = episode_effective_settings(project)
    geo = output_geometry()
    cdir = _character_ref_dir(project["id"])
    outs: list[str] = []
    for i, ch in enumerate(assets.get("characters", []), 1):
        name = str(ch.get("name") or f"角色{i}").strip()
        out = cdir / f"{i:02d}-{_safe_name(name)}.png"
        if out.exists():  # 已有参考图则复用（跨集一致性）
            outs.append(str(out))
            continue
        prompt = (f"角色定妆照：{name}（{ch.get('role', '')}）。"
                  f"外貌：{ch.get('appearance', '')}。"
                  f"正面半身像，视线直视镜头，中性表情，纯色背景，"
                  f"细节清晰。风格：{eff.get('style', '')}")
        res = image.run({"prompt": prompt, "out_path": out,
                         "width": geo["width"], "height": geo["height"],
                         "label": f"REF-{name}"})
        outs.append(res["path"])
    return outs


def _safe_name(name: str) -> str:
    """文件名安全化（保留中英文数字，其余替换为下划线）。"""
    import re as _re
    return _re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name).strip("_") or "char"


def character_ref_map(project_id: str) -> dict[str, str]:
    """角色名 → 参考图路径（不存在参考图时为空字典）。"""
    cdir = _character_ref_dir(project_id)
    result: dict[str, str] = {}
    if not cdir.exists():
        return result
    for f in sorted(cdir.glob("*.png")):
        # 文件名格式 "01-角色名.png"：去掉序号前缀取角色名
        stem = f.stem
        name = stem.split("-", 1)[1] if "-" in stem else stem
        result[name] = str(f)
    return result


def stage_script(project: dict, episode: dict,
                 cancel=None, progress=None) -> list[str]:
    assets = load_assets(project["id"])
    if not assets.get("characters"):
        raise PipelineError("缺少世界观资产，请先执行 worldview 阶段（或重试该阶段）")
    prev = previous_summaries(get_store(), project["id"], episode["idx"])
    names = ", ".join(c["name"] for c in assets["characters"])
    prompt = (f"剧名：《{assets.get('title', project['name'])}》\n"
              f"题材：{assets.get('genre', '')}\n世界观：{assets.get('setting', '')}\n"
              f"角色：{names}\n{character_brief(assets)}\n"
              f"集数：{episode['idx']}\n本集标题：{episode.get('title') or '（自拟）'}\n"
              f"本集梗概：{episode.get('synopsis') or '承接前情，推进主线'}\n"
              f"前情摘要：{'；'.join(prev) if prev else '（第一集，无前情）'}\n\n"
              "请输出本集剧本 JSON，字段："
              '{"title": str, "summary": str(<=120字，供下集续写), '
              '"scenes": [{"name": str, "location": str, "mood": str, '
              '"action": str, "lines": [{"speaker": str(角色名或旁白), '
              '"text": str, "emotion": str}]}]}（2-3 场，每场 3-5 句对白）')
    if progress:
        progress("生成本集剧本", 25.0)
    script = llm_json("script", prompt, project, progress)
    if not script.get("scenes"):
        raise PipelineError("script 缺少 scenes 字段")
    edir = paths.episode_dir(project["id"], episode["idx"])
    j = _write_json(edir / "script.json", script)
    m = edir / "script.md"
    m.write_text(script_markdown(script), "utf-8")
    get_store().update_episode(episode["id"],
                               summary=script.get("summary", ""),
                               title=episode.get("title") or script.get("title", ""))
    if progress:
        progress("剧本完成", 90.0)
    return [str(j), str(m)]


def stage_storyboard(project: dict, episode: dict,
                     cancel=None, progress=None) -> list[str]:
    script = load_script(project["id"], episode["idx"])
    if not script or not script.get("scenes"):
        raise PipelineError("缺少剧本，请先生成/重试 script 阶段")
    assets = load_assets(project["id"])
    eff = episode_effective_settings(project)
    n_shots = int(eff.get("shots_per_episode", 4))
    names = ", ".join(c["name"] for c in assets.get("characters", []))
    prompt = (f"角色：{names}\n镜头数：{n_shots}\n视觉风格：{eff.get('style', '')}\n"
              f"剧本 JSON：\n```json\n{json.dumps(script, ensure_ascii=False)}\n```\n\n"
              f"请输出分镜 JSON，字段："
              '{"shots": [{"idx": int(从1开始), "scene": str, "description": str, '
              '"camera": str, "motion": str(推/拉/摇/移/固定), '
              '"duration_hint": float(秒,3-8), "characters": [角色名], '
              '"lines": [{"speaker": str, "text": str}], '
              '"image_prompt": str(画面提示词，英文或中文均可)}]}（把剧本对白分配到镜头）')
    if progress:
        progress(f"生成分镜（{n_shots} 镜头）", 30.0)
    sb = llm_json("storyboard", prompt, project, progress)
    shots = sb.get("shots") or []
    if not shots:
        raise PipelineError("storyboard 缺少 shots 字段")
    for i, s in enumerate(shots, 1):  # 规范化
        s["idx"] = int(s.get("idx") or i)
        s.setdefault("lines", [])
        if not s["lines"]:  # 无对白镜头补旁白
            s["lines"] = [{"speaker": "旁白", "text": s.get("description", "")}]
        s["duration_hint"] = float(s.get("duration_hint") or eff.get("target_clip_seconds", 5.0))
    edir = paths.episode_dir(project["id"], episode["idx"])
    out = _write_json(edir / "storyboard.json", {"shots": shots})
    if progress:
        progress("分镜完成", 90.0)
    return [str(out)]


def _shot_dir(project_id: str, idx: int, shot_idx: int) -> Path:
    return paths.episode_dir(project_id, idx) / "shots" / f"s{shot_idx:03d}"


def stage_voiceover(project: dict, episode: dict,
                    cancel=None, progress=None) -> list[str]:
    sb = load_storyboard(project["id"], episode["idx"])
    if not sb or not sb.get("shots"):
        raise PipelineError("缺少分镜，请先生成/重试 storyboard 阶段")
    assets = load_assets(project["id"])
    voice_of = {c["name"]: c.get("voice", "narrator")
                for c in assets.get("characters", [])}
    tts = resolve_adapter("tts", project)
    outs: list[str] = []
    durations: dict[str, float] = {}
    shots = sb["shots"]
    for i, s in enumerate(shots):
        if cancel:
            cancel.should_cancel()
        if progress:
            progress(f"配音 镜头{s['idx']}/{len(shots)}", 5 + 85 * (i + 1) / len(shots))
        text = "。".join(str(ln.get("text", "")).strip() for ln in s["lines"] if ln.get("text"))
        if not text:
            text = s.get("description", "（空镜）")
        speaker = (s["lines"][0] or {}).get("speaker", "旁白")
        voice = voice_of.get(speaker, "narrator")
        sd = _shot_dir(project["id"], episode["idx"], s["idx"])
        res = tts.run({"text": text, "voice": voice, "out_path": sd / "vo.wav"})
        durations[str(int(s["idx"]))] = float(res.get("duration") or 3.0)
        outs.append(res["path"])
    edir = paths.episode_dir(project["id"], episode["idx"])
    # 实测时长独立落盘：storyboard.json 保持为纯创作产物（重生成可确定性复现）
    _write_json(edir / "voiceover.json", {"durations": durations})
    if progress:
        progress("配音完成", 92.0)
    return outs


def stage_keyframes(project: dict, episode: dict,
                    cancel=None, progress=None) -> list[str]:
    sb = load_storyboard(project["id"], episode["idx"])
    if not sb or not sb.get("shots"):
        raise PipelineError("缺少分镜，请先生成/重试 storyboard 阶段")
    assets = load_assets(project["id"])
    eff = episode_effective_settings(project)
    geo = output_geometry()
    image = resolve_adapter("image", project)
    # 角色参考图（worldview 阶段生成）：出场角色对应参考图传给图像后端，
    # qwen-image-edit 等编辑模型据此锁定外貌，实现跨镜头/跨集角色一致性。
    ref_map = character_ref_map(project["id"]) if eff.get("character_refs", True) else {}
    outs = []
    shots = sb["shots"]
    for i, s in enumerate(shots):
        if cancel:
            cancel.should_cancel()
        if progress:
            progress(f"关键帧 镜头{s['idx']}/{len(shots)}", 5 + 85 * (i + 1) / len(shots))
        sd = _shot_dir(project["id"], episode["idx"], s["idx"])
        prompt = lock_prompt(s, assets, eff.get("style", ""))
        ctx = {"prompt": prompt, "out_path": sd / "keyframe.png",
               "width": geo["width"], "height": geo["height"],
               "label": f"E{episode['idx']:02d}-S{s['idx']:03d}"}
        refs = [ref_map[n] for n in s.get("characters", [])
                if n in ref_map and Path(ref_map[n]).exists()]
        if refs:
            ctx["ref_images"] = refs
        res = image.run(ctx)
        outs.append(res["path"])
    if progress:
        progress("关键帧完成", 92.0)
    return outs


def stage_clips(project: dict, episode: dict,
                cancel=None, progress=None) -> list[str]:
    sb = load_storyboard(project["id"], episode["idx"])
    if not sb or not sb.get("shots"):
        raise PipelineError("缺少分镜，请先生成/重试 storyboard 阶段")
    eff = episode_effective_settings(project)
    geo = output_geometry()
    video = resolve_adapter("video", project)
    durations = load_durations(project["id"], episode["idx"])
    outs = []
    shots = sb["shots"]
    # 镜头过渡（flf2v）：当前镜头首帧 + 下一镜头关键帧尾帧 → 平滑转场片段
    transition = eff.get("transition", "none")
    for i, s in enumerate(shots):
        if cancel:
            cancel.should_cancel()
        if progress:
            progress(f"镜头片段 {s['idx']}/{len(shots)}", 5 + 85 * (i + 1) / len(shots))
        sd = _shot_dir(project["id"], episode["idx"], s["idx"])
        kf = sd / "keyframe.png"
        if not kf.exists():
            raise PipelineError(f"镜头 {s['idx']} 缺少关键帧，请先生成 keyframes 阶段")
        duration = shot_seconds(s, eff, durations)
        ctx = {"image_path": str(kf), "out_path": sd / "clip.mp4",
               "duration": duration, "prompt": s.get("description", ""),
               "motion": s.get("motion", "auto"), "fps": geo["fps"],
               "width": geo["width"], "height": geo["height"]}
        if transition == "flf2v" and i + 1 < len(shots):
            next_kf = _shot_dir(project["id"], episode["idx"],
                                shots[i + 1]["idx"]) / "keyframe.png"
            if next_kf.exists():
                ctx["end_image_path"] = str(next_kf)
        res = video.run(ctx)
        outs.append(res["path"])
    if progress:
        progress("镜头片段完成", 92.0)
    return outs


def stage_subtitles(project: dict, episode: dict,
                    cancel=None, progress=None) -> list[str]:
    sb = load_storyboard(project["id"], episode["idx"])
    if not sb or not sb.get("shots"):
        raise PipelineError("缺少分镜，请先生成/重试 storyboard 阶段")
    eff = episode_effective_settings(project)
    durations = load_durations(project["id"], episode["idx"])
    segments = []
    for s in sb["shots"]:
        text_parts = []
        for ln in s.get("lines", []):
            sp, tx = ln.get("speaker", ""), ln.get("text", "")
            text_parts.append(f"{sp}：{tx}" if sp and sp != "旁白" else tx)
        segments.append({"text": "\n".join(text_parts) or s.get("description", ""),
                         "speaker": (s.get("lines") or [{}])[0].get("speaker", "旁白"),
                         "duration": shot_seconds(s, eff, durations)})
    asr = resolve_adapter("asr", project)
    if progress:
        progress("字幕时间轴对齐", 50.0)
    res = asr.run({"segments": segments})
    aligned = res.get("segments", segments)
    edir = paths.episode_dir(project["id"], episode["idx"])
    srt = edir / "episode.srt"
    srt.write_text(segments_to_srt(aligned), "utf-8")
    if progress:
        progress("字幕完成", 92.0)
    return [str(srt)]


def stage_compose(project: dict, episode: dict,
                  cancel=None, progress=None) -> list[str]:
    sb = load_storyboard(project["id"], episode["idx"])
    if not sb or not sb.get("shots"):
        raise PipelineError("缺少分镜，请先生成/重试 storyboard 阶段")
    eff = episode_effective_settings(project)
    durations = load_durations(project["id"], episode["idx"])
    shots = []
    for s in sb["shots"]:
        sd = _shot_dir(project["id"], episode["idx"], s["idx"])
        clip = sd / "clip.mp4"
        if not clip.exists():
            raise PipelineError(f"镜头 {s['idx']} 缺少片段，请先生成 clips 阶段")
        shots.append({"idx": s["idx"], "clip": clip, "vo": sd / "vo.wav",
                      "duration": shot_seconds(s, eff, durations)})
    edir = paths.episode_dir(project["id"], episode["idx"])
    timeline = {"episode": episode["idx"], "title": episode.get("title", ""),
                "shots": [{"idx": s["idx"], "duration": s["duration"],
                           "clip": str(s["clip"]), "vo": str(s["vo"])}
                          for s in shots]}
    _write_json(edir / "timeline.json", timeline)
    srt = edir / "episode.srt"
    if progress:
        progress("合成成片", 10.0)
    out = compose_episode(shots, edir, srt if srt.exists() else None,
                          cancel=cancel, progress=progress)
    get_store().update_episode(episode["id"], status="ready")
    if progress:
        progress("成片完成", 95.0)
    return [str(out)]


# ----------------------------------------------------------------------
# 编排
# ----------------------------------------------------------------------
def make_stage_fn(project: dict, episode: dict, stage: str, force: bool):
    """构造阶段的任务执行函数（首次执行与手工重试共用同一路径）。"""
    def fn(cancel=None, progress=None):
        if not force and stage_complete(project, episode, stage):
            if progress:
                progress("产物已存在，跳过（断点续跑）", 100.0)
            return []
        if stage == "worldview":
            return stage_worldview(project, cancel, progress)
        if stage == "script":
            return stage_script(project, episode, cancel, progress)
        if stage == "storyboard":
            return stage_storyboard(project, episode, cancel, progress)
        if stage == "voiceover":
            return stage_voiceover(project, episode, cancel, progress)
        if stage == "keyframes":
            return stage_keyframes(project, episode, cancel, progress)
        if stage == "clips":
            return stage_clips(project, episode, cancel, progress)
        if stage == "subtitles":
            return stage_subtitles(project, episode, cancel, progress)
        if stage == "compose":
            return stage_compose(project, episode, cancel, progress)
        raise PipelineError(f"未知阶段: {stage}")
    return fn


def sync_episode_status(episode_id: str) -> str:
    """依据任务终态与产物完整性刷新分集状态。"""
    store = get_store()
    ep = store.get_episode(episode_id)
    if not ep:
        return "missing"
    proj = store.get_project(ep["project_id"])
    if proj is None:
        return "missing"
    tasks = store.list_tasks(episode_id=episode_id)
    if any(t["status"] in ("running", "pending") for t in tasks):
        status = "generating"
    elif any(t["status"] == "failed" for t in tasks):
        status = "failed"
    elif stage_complete(proj, ep, "compose"):
        status = "ready"
    elif any(stage_complete(proj, ep, s) for s in STAGES):
        status = "partial"
    else:
        status = ep.get("status") if ep.get("status") != "generating" else "draft"
    if status != ep.get("status"):
        store.update_episode(episode_id, status=status)
    get_bus().publish("episode", episode_id=episode_id, project_id=ep["project_id"],
                      idx=ep["idx"], status=status)
    return status


# 每集同一时刻仅允许一条流水线（对话/REST 并发保护）
_pipeline_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _episode_lock(episode_id: str) -> threading.Lock:
    with _locks_guard:
        if episode_id not in _pipeline_locks:
            _pipeline_locks[episode_id] = threading.Lock()
        return _pipeline_locks[episode_id]


def run_pipeline(episode_id: str, stage: str = "all", force: bool = False) -> dict:
    """同步执行流水线（供测试与单阶段重试；生产由 services 在后台线程调用）。"""
    store = get_store()
    episode = store.get_episode(episode_id)
    if not episode:
        raise PipelineError(f"分集不存在: {episode_id}")
    project = store.get_project(episode["project_id"])
    if not project:
        raise PipelineError("项目不存在")
    if stage != "all" and stage not in STAGES:
        raise PipelineError(f"未知阶段: {stage}（可选: all 或 {'/'.join(STAGES)}）")
    stages = [stage] if stage != "all" else list(STAGES)
    tm = get_task_manager()
    summary = {"episode_id": episode_id, "stages": {}, "status": "succeeded"}
    lock = _episode_lock(episode_id)
    if not lock.acquire(blocking=False):
        raise PipelineError("该分集已在执行流水线，请等待完成或先取消")
    try:
        store.update_episode(episode_id, status="generating")
        for st in stages:
            task = tm.submit(project["id"], episode_id, st,
                             make_stage_fn(project, episode, st, force),
                             params={"force": force, "stage": st})
            final = tm.wait(task["id"], timeout=3600)
            summary["stages"][st] = {"task_id": task["id"], "status": final,
                                     "error": (tm.get(task["id"]) or {}).get("error", "")}
            # Release VRAM between GPU-intensive stages
            if st in ("keyframes", "clips") and final == "succeeded":
                try:
                    registry.unload_all()
                except Exception:
                    pass
            if final != "succeeded":
                summary["status"] = final
                break
        sync_episode_status(episode_id)
        return summary
    finally:
        lock.release()


def start_pipeline(episode_id: str, stage: str = "all", force: bool = False) -> dict:
    """后台线程执行流水线（REST 入口，立即返回）。"""
    store = get_store()
    episode = store.get_episode(episode_id)
    if not episode:
        raise PipelineError(f"分集不存在: {episode_id}")
    lock = _episode_lock(episode_id)
    if not lock.acquire(blocking=False):
        raise PipelineError("该分集已有流水线在执行，请等待完成或先取消")
    lock.release()

    def _bg() -> None:
        try:
            run_pipeline(episode_id, stage, force)
        except PipelineError as exc:
            logger.warning("流水线未启动: %s", exc)
            get_bus().publish("pipeline_error", episode_id=episode_id, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("流水线异常")
            get_bus().publish("pipeline_error", episode_id=episode_id, error=str(exc))
            get_store().update_episode(episode_id, status="failed")

    threading.Thread(target=_bg, daemon=True,
                     name=f"pipeline-{episode_id}").start()
    return {"episode_id": episode_id, "stage": stage, "force": force,
            "started": True}

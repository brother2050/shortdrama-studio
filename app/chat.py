"""对话编排：自然语言 → 意图 → services 动作 → 回复。

- 意图解析：配置了真实 LLM 时优先用 LLM 结构化解析（失败自动回退规则），
  mock/离线环境直接用中文规则解析（零依赖、确定性、可测试）；
- 所有动作复用 services 层（与 REST 完全同路径）；
- 回复由模板生成（引用真实数据），消息与动作卡片落库可回放。
"""
from __future__ import annotations

import re
from typing import Any

from app.adapters import registry
from app.config import get_settings
from app.events import get_bus
from app.pipeline import STAGE_LABELS, STAGES, load_script
from app.schemas import ActionCard
from app.services import (ServiceError, cancel_task, create_episode,
                          create_project, generate_episode, project_detail,
                          retry_failed, retry_task, task_list)
from app.store import get_store

HELP_TEXT = (
    "我可以帮你完成连续短剧的全流程，试试这些说法：\n"
    "1. 「创建一部 3 集的都市爱情短剧，名字叫《晚风便利店》」\n"
    "2. 「生成第 1 集」/「继续下一集」\n"
    "3. 「重新生成第 1 集的分镜」「重试配音」\n"
    "4. 「重试失败的任务」「取消当前任务」\n"
    "5. 「现在什么进度」「我有哪些项目」\n"
    "6. 「用 cosyvoice 配音」「每集 6 个镜头」"
)

_STAGE_KEYWORDS = {
    "worldview": ["世界观", "设定"],
    "script": ["剧本", "台词本"],
    "storyboard": ["分镜", "故事板"],
    "voiceover": ["配音", "语音", "旁白", "音"],
    "keyframes": ["关键帧", "首帧", "画面"],
    "clips": ["镜头片段", "片段", "视频片段", "动态"],
    "subtitles": ["字幕"],
    "compose": ["合成", "成片", "剪辑"],
}
_GENRES = {"都市": "都市情感", "爱情": "爱情", "悬疑": "悬疑", "古装": "古装",
           "科幻": "科幻", "喜剧": "喜剧", "奇幻": "奇幻", "谍战": "谍战",
           "青春": "青春", "家庭": "家庭", "复仇": "都市复仇"}
_BACKEND_HINTS = [
    ("cosyvoice", "tts", "cosyvoice"), ("chattts", "tts", "chattts"),
    ("sovits", "tts", "gpt_sovits"), ("fish", "tts", "fish_speech"),
    ("modelscope", "llm", "modelscope"), ("qwen", "llm", "modelscope"),
    ("transformers", "llm", "transformers_qwen"),
    ("wan", "video", "diffsynth_wan"), ("kenburns", "video", "kenburns"),
    ("diffsynth", "image", "diffsynth"), ("flux", "image", "diffsynth"),
    ("sdxl", "image", "diffsynth"), ("funasr", "asr", "funasr"),
]

INTENTS = ("create_project", "generate_episode", "regenerate_stage",
           "retry_failed", "cancel_task", "status_query", "list_projects",
           "set_preferences", "help", "smalltalk")


# ----------------------------------------------------------------------
# 意图解析（规则）
# ----------------------------------------------------------------------
def parse_intent_rules(message: str) -> dict[str, Any]:
    msg = message.strip()
    if re.search(r"帮助|你能做什么|怎么用|有哪些功能|^help$", msg):
        return {"intent": "help"}
    if re.search(r"取消", msg) and re.search(r"任务|生成|流水线", msg):
        return {"intent": "cancel_task"}
    if re.search(r"重试|重跑|重新", msg) and re.search(r"失败", msg):
        return {"intent": "retry_failed"}
    m_stage = _match_stage(msg)
    if m_stage and re.search(r"重新生成|重跑|再来一次|重新来|重新做", msg):
        return {"intent": "regenerate_stage", "stage": m_stage,
                "episode": _match_episode(msg)}
    if re.search(r"^(?:那就)?重试", msg) and m_stage:
        return {"intent": "regenerate_stage", "stage": m_stage,
                "episode": _match_episode(msg)}
    if re.search(r"创建|新建|来一部|做一部|开一部", msg) and "短剧" in msg or \
            re.search(r"创建项目", msg):
        return {"intent": "create_project", "raw": msg}
    if re.search(r"生成|继续|开拍|来一集|拍一集|下一集", msg):
        if m_stage:
            return {"intent": "regenerate_stage", "stage": m_stage,
                    "episode": _match_episode(msg)}
        return {"intent": "generate_episode", "episode": _match_episode(msg),
                "force": bool(re.search(r"重新|强制", msg))}
    if re.search(r"讲到哪|演到哪|剧情|回顾|讲了什么|演了什么|梗概|大纲", msg):
        return {"intent": "recap", "episode": _match_episode(msg)}
    if re.search(r"进度|状态|怎么样|到哪了|什么情况|好了吗", msg):
        return {"intent": "status_query"}
    if re.search(r"哪些项目|项目列表|我的项目|列表", msg):
        return {"intent": "list_projects"}
    backend = _match_backend(msg)
    m_shots = re.search(r"(\d+)\s*个?(?:个)?镜头|镜头数[:：]?\s*(\d+)|每集\s*(\d+)", msg)
    if backend or m_shots:
        return {"intent": "set_preferences",
                "backend": backend, "shots": _first_int(m_shots) if m_shots else None}
    return {"intent": "smalltalk"}


def _match_stage(msg: str) -> str | None:
    for stage, words in _STAGE_KEYWORDS.items():
        if any(w in msg for w in words):
            return stage
    return None


def _match_episode(msg: str) -> int | None:
    m = re.search(r"第\s*(\d+)\s*集", msg)
    if m:
        return int(m.group(1))
    if "下一集" in msg or "新的一集" in msg:
        return -1  # 下一集（由执行层解析为 max+1）
    return None


def _match_backend(msg: str) -> tuple[str, str] | None:
    low = msg.lower()
    for kw, cap, name in _BACKEND_HINTS:
        if kw in low:
            return cap, name
    return None


def _first_int(m: re.Match) -> int:
    return int(next(g for g in m.groups() if g))


# ----------------------------------------------------------------------
# 意图解析（LLM，配置真实 LLM 时启用）
# ----------------------------------------------------------------------
def parse_intent_llm(message: str, context: str) -> dict | None:
    conf = get_settings().capability("llm")
    try:
        llm = registry.resolve("llm", conf["backend"], conf["params"])
        if type(llm).spec.name == "mock":
            return None  # mock 走规则即可
    except Exception:  # noqa: BLE001
        return None
    system = ("你是短剧平台的对话意图分类器。只输出 JSON："
              '{"intent": one_of(' + "|".join(INTENTS) + '), "params": {...}}。'
              "params 可含 episode(整数,-1表示下一集), stage(one_of("
              + "|".join(STAGES) + ")), backend, shots, name, genre。")
    try:
        resp = llm.run({"system": system, "messages": [
            {"role": "user", "content": f"上下文：{context}\n用户消息：{message}"}]})
        m = re.search(r"\{.*\}", resp.get("text", ""), re.S)
        data = eval_dict(m.group(0)) if m else None
        if data and data.get("intent") in INTENTS:
            params = data.get("params") or {}
            return {"intent": data["intent"], **params}
    except Exception:  # noqa: BLE001 —— LLM 解析失败回退规则
        return None
    return None


def eval_dict(text: str) -> dict | None:
    import json
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def parse_intent(message: str, context: str = "") -> dict:
    return parse_intent_llm(message, context) or parse_intent_rules(message)


# ----------------------------------------------------------------------
# 意图执行
# ----------------------------------------------------------------------
def _pick_project(project_id: str | None) -> dict | None:
    store = get_store()
    if project_id:
        return store.get_project(project_id)
    projects = store.list_projects()
    return projects[0] if projects else None


def _resolve_episode(project: dict, want: int | None) -> dict | None:
    store = get_store()
    eps = store.list_episodes(project["id"])
    if not eps:
        return None
    if want is None:
        return eps[-1]
    if want == -1:
        return None  # 下一集：需要新建
    for e in eps:
        if e["idx"] == want:
            return e
    return None


def _next_idx(project: dict) -> int:
    eps = get_store().list_episodes(project["id"])
    return (max(e["idx"] for e in eps) + 1) if eps else 1


def handle_chat(project_id: str | None, message: str) -> dict:
    store = get_store()
    user_msg = store.add_chat(project_id, "user", message)
    intent = parse_intent(message, f"当前项目: {project_id or '未选择'}")
    actions: list[ActionCard] = []
    try:
        reply, actions, project_id = _execute(intent, message, project_id)
    except ServiceError as exc:
        reply = f"操作失败：{exc}"
        actions = [ActionCard(intent=intent.get("intent", "?"), summary=str(exc),
                              ok=False)]
    except Exception as exc:  # noqa: BLE001
        reply = f"发生异常：{exc}（可重试该操作）"
        actions = [ActionCard(intent=intent.get("intent", "?"),
                              summary=f"{type(exc).__name__}: {exc}", ok=False)]
    cards = [a.model_dump() for a in actions]
    bot_msg = store.add_chat(project_id, "assistant", reply, cards)
    get_bus().publish("chat", project_id=project_id, user_message_id=user_msg["id"],
                      assistant_message_id=bot_msg["id"], intent=intent.get("intent"))
    return {"reply": reply, "actions": cards, "project_id": project_id,
            "message_id": bot_msg["id"]}


def _ok(intent: str, summary: str, **payload) -> ActionCard:
    return ActionCard(intent=intent, summary=summary, payload=payload, ok=True)


def _execute(intent: dict, message: str, project_id: str | None):
    name = intent["intent"]
    if name == "help":
        return HELP_TEXT, [], project_id

    if name == "create_project":
        m = re.search(r"《([^》]{1,32})》", message)
        title = m.group(1) if m else None
        if not title:
            m2 = re.search(r"名字叫?\s*([^\s，,。;；]{1,24})", message)
            title = m2.group(1) if m2 else "未命名短剧"
        genre = next((v for k, v in _GENRES.items() if k in message), "都市情感")
        m_ep = re.search(r"(\d+)\s*集", message)
        project = create_project(name=title, genre=genre, premise=message.strip(),
                                 episodes_planned=int(m_ep.group(1)) if m_ep else 3)
        detail = f"《{project['name']}》（{project['genre']}）"
        return (f"项目已创建：{detail}。现在可以说「生成第 1 集」，我会依次完成"
                "世界观 → 剧本 → 分镜 → 配音 → 关键帧 → 片段 → 字幕 → 合成。",
                [_ok("create_project", f"创建项目 {detail}",
                     project_id=project["id"])], project["id"])

    if name == "generate_episode":
        project = _pick_project(project_id)
        if not project:
            return ("还没有项目。先说「创建一部 3 集的都市爱情短剧，名字叫《晚风》」。",
                    [], project_id)
        want = intent.get("episode")
        wants_next = want == -1 or any(
            k in message for k in ("下一集", "新的一集", "来一集", "拍一集"))
        store = get_store()
        ep = None
        if want not in (-1, None):
            ep = store.get_episode_by_idx(project["id"], int(want))
        if ep is None:
            eps = store.list_episodes(project["id"])
            # 「继续生成」→ 续跑最后一集；「下一集」/无分集 → 新建
            if want is None and not wants_next and eps:
                ep = eps[-1]
            else:
                ep = create_episode(project["id"], title="")
                ep = store.update_episode(ep["id"], title=f"第{ep['idx']}集")
        generate_episode(ep["id"], "all", bool(intent.get("force")))
        return (f"第 {ep['idx']} 集流水线已启动（8 个阶段，失败会停下等你重试）。"
                "说「现在什么进度」随时查询。",
                [_ok("generate_episode", f"启动第 {ep['idx']} 集流水线",
                     episode_id=ep["id"], idx=ep["idx"])], project["id"])

    if name == "recap":
        project = _pick_project(project_id)
        if not project:
            return "还没有项目，先创建一部短剧吧。", [], project_id
        store = get_store()
        want = intent.get("episode")
        if want in (None, -1):
            eps = store.list_episodes(project["id"])
            ep = eps[-1] if eps else None
        else:
            ep = store.get_episode_by_idx(project["id"], int(want))
        if not ep:
            return "还没有可回顾的分集，先说「生成第 1 集」。", [], project["id"]
        script = load_script(project["id"], ep["idx"]) or {}
        summary = script.get("summary", "") or ep.get("synopsis", "") or "（暂无梗概）"
        actions_ = [f"{sc.get('name', '')}：{sc['action']}"
                    for sc in script.get("scenes", [])[:4] if sc.get("action")]
        body = "；".join(actions_) if actions_ else "剧本尚未生成，可先跑「剧本」阶段"
        return (f"《{project['name']}》第 {ep['idx']} 集：{summary}\n"
                f"剧情线：{body}",
                [_ok("recap", f"回顾第 {ep['idx']} 集", episode_id=ep["id"])],
                project["id"])

    if name == "regenerate_stage":
        project = _pick_project(project_id)
        if not project:
            return "还没有项目，先创建一个。", [], project_id
        stage = intent.get("stage") or "storyboard"
        if stage not in STAGES:
            return f"未知阶段：{stage}", [], project["id"]
        store = get_store()
        want = intent.get("episode")
        ep = store.get_episode_by_idx(project["id"], int(want)) \
            if want not in (-1, None) else None
        if ep is None:
            eps = store.list_episodes(project["id"])
            ep = eps[-1] if eps else None
        if not ep:
            return ("还没有分集，先说「生成第 1 集」。",
                    [], project["id"])
        generate_episode(ep["id"], stage, force=True)
        label = STAGE_LABELS.get(stage, stage)
        return (f"已强制重跑第 {ep['idx']} 集的「{label}」阶段。完成后可继续其余阶段"
                "（说「继续生成」即可从断点续跑）。",
                [_ok("regenerate_stage", f"重跑第 {ep['idx']} 集·{label}",
                     episode_id=ep["id"], stage=stage)], project["id"])

    if name == "retry_failed":
        task = retry_failed(project_id)
        label = STAGE_LABELS.get(task["stage"], task["stage"])
        return (f"已重试任务「{label}」（{task['id']}）。",
                [_ok("retry_failed", f"重试 {label}", task_id=task["id"])],
                project_id)

    if name == "cancel_task":
        running = [t for t in task_list(project_id=project_id)
                   if t["status"] in ("running", "pending")]
        if not running:
            running = [t for t in task_list() if t["status"] in ("running", "pending")]
        if not running:
            return "当前没有正在运行的任务。", [], project_id
        task = cancel_task(running[0]["id"])
        return (f"已取消任务「{STAGE_LABELS.get(task['stage'], task['stage'])}」。",
                [_ok("cancel_task", "已取消", task_id=task["id"])], project_id)

    if name == "status_query":
        project = _pick_project(project_id)
        if not project:
            return "还没有项目。先创建一个吧。", [], project_id
        detail = project_detail(project["id"])
        lines = [f"项目《{detail['project']['name']}》进展："]
        for ep in detail["episodes"]:
            done = [s for s, st in ep["stages"].items() if st == "ready"]
            lines.append(f"  第 {ep['idx']} 集 [{ep['status']}] {len(done)}/8 阶段完成")
        failed = [t for t in task_list(project_id=project["id"])
                  if t["status"] == "failed"]
        if failed:
            t = failed[0]
            lines.append(f"  最近失败：{STAGE_LABELS.get(t['stage'], t['stage'])}"
                         f"（{t['error'][:60]}），说「重试失败的任务」可重跑")
        return "\n".join(lines), [], project["id"]

    if name == "list_projects":
        projects = get_store().list_projects()
        if not projects:
            return "还没有项目。试试「创建一部悬疑短剧」。", [], project_id
        lines = ["项目列表："] + [
            f"  {i+1}. 《{p['name']}》（{p['genre']}）-{p['id']}"
            for i, p in enumerate(projects)]
        return "\n".join(lines), [], project_id

    if name == "set_preferences":
        project = _pick_project(project_id)
        backend = intent.get("backend")
        shots = intent.get("shots")
        if backend:
            cap, bname = backend
            if bname not in registry.names(cap):
                return (f"后端 {bname} 未注册（可选：{', '.join(registry.names(cap))}）。",
                        [], project_id)
        if project:
            config = dict(project.get("config") or {})
            if backend:
                config.setdefault("capabilities", {})[cap] = {"backend": bname, "params": {}}
            if shots:
                config.setdefault("episode_defaults", {})["shots_per_episode"] = int(shots)
            if backend or shots:
                from app.services import patch_project
                patch_project(project["id"], {"config": config})
                changed = ([f"{cap}→{bname}"] if backend else []) + \
                          ([f"每集 {shots} 镜头"] if shots else [])
                return (f"已更新本项目偏好：{'；'.join(changed)}（仅对本项目生效）。",
                        [_ok("set_preferences", "；".join(changed),
                             project_id=project["id"])], project["id"])
            return ("没有识别到可设置的偏好，试试「用 cosyvoice 配音」或「每集 6 个镜头」。",
                    [], project["id"])
        # 无项目：写入全局默认设置（之后创建的项目自动生效）
        partial: dict = {}
        if backend:
            partial.setdefault("capabilities", {})[cap] = {"backend": bname, "params": {}}
        if shots:
            partial.setdefault("episode_defaults", {})["shots_per_episode"] = int(shots)
        if not partial:
            return ("还没有项目，偏好可以先存为全局默认：说「用 cosyvoice 配音」"
                    "或「每集 6 个镜头」。", [], project_id)
        from app.services import update_settings
        update_settings(partial)
        changed = ([f"{cap}→{bname}"] if backend else []) + \
                  ([f"每集 {shots} 镜头"] if shots else [])
        return (f"已保存为全局默认偏好：{'；'.join(changed)}。"
                "之后创建的项目自动生效；已有项目可在项目设置中单独调整。",
                [_ok("set_preferences", "全局默认：" + "；".join(changed))], project_id)

    if name == "smalltalk":
        return ("我在。可以直接下指令，例如「生成第 2 集」「重新生成第 1 集的分镜」"
                "「重试失败的任务」；说「帮助」查看全部能力。"), [], project_id

    return HELP_TEXT, [], project_id

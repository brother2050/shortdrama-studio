"""LLM 后端 1/3：mock（内置，零依赖，默认兜底）。

用途：任何环境下保证全链路可演示、可测试。确定性输出（同种子同结果）。

协议约定：流水线提示词以 ``[STAGE:xxx]`` 标记阶段，mock 依据标记与
提示词内嵌的结构化信息（剧名/题材/创意/镜头数/前情）生成对应 JSON。
非流水线调用（闲聊/意图）返回模板回复（对话编排层另有规则解析，不依赖本回复）。
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any

from app.adapters.base import AdapterBase, AdapterSpec, register_adapter

_STAGES = ("worldview", "script", "storyboard")

_NAMES_F = ["林晚", "苏青禾", "沈亦然", "夏栀", "程一宁", "顾清欢", "叶知秋", "江晚吟"]
_NAMES_M = ["陆则铭", "顾北辰", "沈聿", "周砚辞", "裴叙", "霍云深", "温既明", "许亦深"]
_PERSONA_F = ["表面倔强内心柔软的便利店夜班店员", "雷厉风行却害怕告白的策展人",
              "外冷内热的急诊科医生", "怀揣秘密回国的调香师"]
_PERSONA_M = ["毒舌但守时的建筑设计师", "白天理性夜晚失眠的刑警",
              "笑容干净的独立咖啡店主", "背负家债却从不低头的创业者"]
_APPEAR_F = ["齐肩黑发, 米白针织衫, 眼角有泪痣", "高马尾, 燕麦色风衣, 指尖常有墨迹",
             "低丸子头, 深灰卫衣, 左腕细银链"]
_APPEAR_M = ["寸头, 墨绿工装夹克, 肩宽腿长", "微卷短发, 黑框眼镜, 惯穿白衬衫",
             "碎发, 深蓝大衣, 喉结明显"]
_SCENES = [("便利店·夜", "凌晨两点的便利店, 暖黄灯光, 玻璃窗映着雨痕"),
           ("老巷·黄昏", "夕照斜切进窄巷, 晾衣绳滴水, 猫从墙头跃下"),
           ("天台·夜", "城市灯火在远处铺开, 风掀起衣角"),
           ("画室·午後", "松节油气味, 逆光的尘埃, 未完成的肖像")]
_ACTIONS = ["她把关东煮推到他面前，别过脸", "他攥紧了口袋里的诊断书",
            "两人在雨里对峙，谁都没有先开口", "她笑着把伞塞给他，转身跑进雨幕",
            "他终于在打烊前说出了那句话"]
_EMOTIONS = ["克制", "哽咽", "释然", "试探", "雀跃", "颤抖"]
_CAMERAS = ["中景平视", "特写", "过肩镜头", "大远景", "手持跟拍"]
_MOTIONS = ["推", "拉", "摇", "移", "固定"]
_NARR = ["雨下了一整夜，城市把心事泡得很软。",
         "有些告白，迟到了七年，仍然烫。",
         "便利店的灯，为晚归的人亮到天明。"]


def _seed(text: str) -> random.Random:
    return random.Random(int(hashlib.md5(text.encode("utf-8")).hexdigest()[:12], 16))


def _pick(rng: random.Random, pool: list[str]) -> str:
    return rng.choice(pool)


def _grab(pattern: str, text: str, default: str = "") -> str:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else default


def _worldview(rng: random.Random, prompt: str) -> dict:
    name = _grab(r"剧名[:：]\s*《?([^》\n]+)》?", prompt, "晚风便利店")
    genre = _grab(r"题材[:：]\s*([^\n]+)", prompt, "都市情感")
    premise = _grab(r"创意[:：]\s*([^\n]+)", prompt, "")
    f, m = _pick(rng, _NAMES_F), _pick(rng, _NAMES_M)
    characters = [
        {"name": f, "role": "女主", "persona": _pick(rng, _PERSONA_F),
         "appearance": _pick(rng, _APPEAR_F), "voice": ""},
        {"name": m, "role": "男主", "persona": _pick(rng, _PERSONA_M),
         "appearance": _pick(rng, _APPEAR_M), "voice": ""},
    ]
    scenes = [{"name": n, "description": d, "mood": _pick(rng, ["温暖", "清冷", "怅然", "明亮"])}
              for n, d in (_SCENES[i % len(_SCENES)] for i in range(3))]
    logline = premise or f"{f}与{m}在{name}一次次深夜相遇，把遗憾慢慢熬成了告白"
    return {
        "title": name, "logline": logline, "genre": genre,
        "style": _pick(rng, ["电影感, 自然光, 浅景深", "日系清新, 柔光", "冷调都市, 霓虹夜色"]),
        "setting": f"现代都市，故事围绕「{name}」与三位主角的深夜交集展开",
        "characters": characters, "scenes": scenes,
        "episode_outline": [
            f"第1集 深夜相遇：{f}与{m}因一场雨困在同一屋檐",
            f"第2集 旧事重提：一封信揭开七年前的误会",
            f"第3集 晚风告白：天台之上，两人终于坦诚相对",
        ],
    }


def _script(rng: random.Random, prompt: str, chars: list[str]) -> dict:
    ep = _grab(r"集数[:：]\s*(\d+)", prompt, "1")
    title = _grab(r"本集标题[:：]\s*([^\n]+)", prompt, f"第{ep}集·晚风起")
    prev = _grab(r"前情摘要[:：]\s*([^\n]+)", prompt, "")
    n_scenes = 2
    # 采样不重复：避免同集内动作/台词复读（人性化可读）
    scene_actions = rng.sample(_ACTIONS, n_scenes)
    dialog_pool = rng.sample(
        ["你还在等那班末车吗？", "有些话，现在不说就晚了。",
         "我请你这碗关东煮，别哭。", "当年不辞而别的人是我。",
         "那你现在，可以留下来吗？", "明天，我请你喝真豆浆。"], 4)
    speakers = [c for c in chars if c] or ["林晚", "陆则铭"]
    scenes, di = [], 0
    for i in range(n_scenes):
        name, desc = _SCENES[(i + int(ep)) % len(_SCENES)]
        # 第一句固定旁白交代前情，其后台词交替角色、不重复
        lines = [{"speaker": "旁白",
                  "text": (f"上集说到，{prev}。" if prev else "") + _pick(rng, _NARR),
                  "emotion": "平静"}]
        for j in range(2):
            lines.append({"speaker": speakers[(i + j) % len(speakers)],
                          "text": dialog_pool[di % len(dialog_pool)],
                          "emotion": _pick(rng, _EMOTIONS)})
            di += 1
        scenes.append({"name": name, "location": desc,
                       "mood": _pick(rng, ["温暖", "克制", "怅然"]),
                       "action": scene_actions[i], "lines": lines})
    return {"episode": int(ep), "title": title, "scenes": scenes,
            "summary": f"{title}：{scene_actions[0]}，两人靠近一步。"}


def _storyboard(rng: random.Random, prompt: str, chars: list[str]) -> dict:
    n = int(_grab(r"镜头数[:：]\s*(\d+)", prompt, "4") or 4)
    script_json = _grab_json(prompt)
    shots = []
    lines_pool = []
    for sc in (script_json or {}).get("scenes", []):
        lines_pool.extend(sc.get("lines", []))
    li = 0
    for i in range(n):
        line = lines_pool[li % len(lines_pool)] if lines_pool else \
            {"speaker": "旁白", "text": _pick(rng, _NARR)}
        li += 1
        scene_name, scene_desc = _SCENES[i % len(_SCENES)]
        camera = _pick(rng, _CAMERAS)
        shots.append({
            "idx": i + 1, "scene": scene_name,
            "description": f"{scene_desc}；{line['speaker']}：{line['text']}",
            "camera": camera, "motion": _pick(rng, _MOTIONS),
            "duration_hint": round(rng.uniform(4.0, 6.0), 1),
            "characters": [c for c in chars if c in line["text"]] or [chars[0] if chars else ""],
            "lines": [line],
            "image_prompt": f"{scene_desc}, {line['speaker']}入镜, {camera}",
        })
    return {"shots": shots}


def _grab_json(text: str) -> dict | None:
    """提取提示词里嵌入的 JSON 块（```json ... ``` 或首个 {...}）。"""
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if not m:
        m = re.search(r"(\{.*\})", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


@register_adapter
class MockLLM(AdapterBase):
    spec = AdapterSpec(
        name="mock", capability="llm", display_name="内置模板引擎（离线）",
        description="零依赖的确定性短剧生成模板，用于演示/测试/降级兜底。"
                    "支持 worldview/script/storyboard 三种结构化阶段与非结构化闲聊。",
        priority=10, requires=[],
        default_params={}, param_docs={},
        license="MIT（本项目内置）",
    )

    def run(self, ctx: dict[str, Any], progress=None) -> dict[str, Any]:
        text = "\n".join(m.get("content", "") for m in ctx.get("messages", []))
        rng = _seed(text + json.dumps(ctx.get("system", ""), ensure_ascii=False))
        stage = ""
        m = re.search(r"\[STAGE:(\w+)\]", text)
        if m:
            stage = m.group(1)
        if stage in _STAGES:
            if progress:
                progress(f"模板生成 {stage}", 30.0)
            if stage == "worldview":
                data = _worldview(rng, text)
            elif stage == "script":
                chars = _grab(r"角色[:：]\s*([^\n]+)", text, "林晚, 陆则铭")
                chars = [c.strip() for c in re.split(r"[、,，]", chars) if c.strip()]
                data = _script(rng, text, chars)
            else:
                chars = _grab(r"角色[:：]\s*([^\n]+)", text, "林晚, 陆则铭")
                chars = [c.strip() for c in re.split(r"[、,，]", chars) if c.strip()]
                data = _storyboard(rng, text, chars)
            if progress:
                progress("模板生成完成", 90.0)
            return {"text": json.dumps(data, ensure_ascii=False, indent=2)}
        # 非结构化：模板闲聊
        return {"text": _pick(rng, [
            "收到。你可以让我：创建项目 / 生成下一集 / 重试失败任务 / 查看进度。",
            "我在。试试说「生成第 2 集」或「重新生成第 1 集的分镜」。",
        ])}

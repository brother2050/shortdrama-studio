"""内置四引擎 TTS 路由（移植自 mosaic TTS 节点，去外部依赖）。

引擎清单（全部惰性导入，未就绪自动跳过）：

===============  ============  ==============================  =====================
引擎             接入方式       依赖                             适用
===============  ============  ==============================  =====================
cosyvoice        本地库        pip install -e CosyVoice        多音色中文，音质最佳
chattts          本地库        pip install ChatTTS              对话感强，角色音色固定
gpt_sovits       HTTP 服务      api_v2.py（端口 9880）          声音克隆（参考音频）
fish_speech      HTTP 服务      tools/api_server.py（端口 8080） 声音克隆（reference_id）
===============  ============  ==============================  =====================

auto 路由顺序：本地库优先（离线友好）cosyvoice → chattts，再 HTTP 服务
gpt_sovits → fish_speech；显式 ``engine`` 参数直选。
"""
from __future__ import annotations

from typing import Any

from app.adapters.base import AdapterError, AdapterUnavailableError
from app.adapters.tts_libs._base import TTSEngineBase

#: auto 模式引擎优先级（本地库优先，离线友好）
AUTO_ORDER = ("cosyvoice", "chattts", "gpt_sovits", "fish_speech")


def _load_engines() -> dict[str, TTSEngineBase]:
    """按名加载引擎单例（导入本包即轻量，引擎模块本身零重依赖）。"""
    from app.adapters.tts_libs import chattts_engine, cosyvoice_engine
    from app.adapters.tts_libs import fish_engine, gptsovits_engine

    engines: dict[str, TTSEngineBase] = {
        cosyvoice_engine.engine.name: cosyvoice_engine.engine,
        chattts_engine.engine.name: chattts_engine.engine,
        gptsovits_engine.engine.name: gptsovits_engine.engine,
        fish_engine.engine.name: fish_engine.engine,
    }
    return engines


def get_engines() -> dict[str, TTSEngineBase]:
    return _load_engines()


def engine_status(params: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """全部引擎的就绪状态（设置页/系统页渲染用）。"""
    params = params or {}
    out: dict[str, dict[str, Any]] = {}
    for name, eng in get_engines().items():
        ok, reason = eng.ready(params)
        out[name] = {"label": eng.label, "kind": eng.kind,
                     "ready": ok, "reason": reason}
    return out


def any_engine_ready(params: dict[str, Any] | None = None) -> bool:
    return any(v["ready"] for v in engine_status(params).values())


def pick_engine(params: dict[str, Any]) -> tuple[str, TTSEngineBase]:
    """选择引擎：显式 ``engine`` 直选（须就绪），否则 auto 取第一个就绪引擎。"""
    engines = get_engines()
    wanted = str(params.get("engine") or "auto").strip()
    if wanted and wanted != "auto":
        eng = engines.get(wanted)
        if eng is None:
            raise AdapterError(
                f"未知 TTS 引擎 {wanted!r}（可选: auto, {', '.join(AUTO_ORDER)}）")
        ok, reason = eng.ready(params)
        if not ok:
            raise AdapterUnavailableError(f"引擎 {wanted} 未就绪：{reason}")
        return wanted, eng
    for name in AUTO_ORDER:
        eng = engines[name]
        ok, _ = eng.ready(params)
        if ok:
            return name, eng
    detail = "；".join(
        f"{s['label']}={'就绪' if s['ready'] else s['reason']}"
        for s in engine_status(params).values())
    raise AdapterUnavailableError(
        f"四引擎均未就绪：{detail}。可先安装 ChatTTS（pip install ChatTTS）"
        "或启动 GPT-SoVITS/Fish Speech 服务，或改用 tts=mock 后端。")


def unload_all_engines() -> None:
    """释放全部本地引擎模型显存。"""
    for eng in get_engines().values():
        try:
            eng.unload()
        except Exception:  # noqa: BLE001
            pass

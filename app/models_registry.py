"""模型预设注册表：全平台唯一的模型目录数据源。

下载脚本（scripts/download_models.py）、系统 API（/api/system/models）、
前端设置页（模型预设下拉 + JSON 自动填充）共用这一份注册表，
彻底消除"模型目录位置各异、命名千奇百怪"的问题。

统一布局规范（根目录 = 项目根 ``models/``，见 ``app.paths.models_root``）
------------------------------------------------------------------
::

    models/
    ├── llm/qwen2.5-1.5b/            # 能力/预设名（kebab-case，全小写）
    ├── tts/cosyvoice2-0.5b/
    ├── image/sdxl/
    ├── image/_shared/qwen-image-base/   # 跨预设共享组件
    ├── video/wan2.2-ti2v-5b/
    ├── video/_shared/umt5-xxl/
    ├── asr/sensevoice-small/
    └── _cache/                      # ModelScope 下载缓存

设计要点：
- 预设名即目录名（kebab-case），注册表校验合法性，杜绝怪字符；
- ``params`` 是"选中该预设时自动填充到设置页的参数模板"（相对路径，
  跨机器可移植；适配器运行时统一解析为绝对路径）；
- ``shared`` 声明该预设依赖的共享组件（多预设共用，只下载一次）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app import paths

#: 预设名合法格式：小写字母/数字/点/连字符（kebab-case）
_PRESET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*$")


@dataclass(frozen=True)
class SharedDownload:
    """跨预设共享组件：从 repo_id 下载 file_pattern 到 models/<into>/。"""
    repo_id: str
    file_pattern: str          # modelscope allow_file_pattern
    into: str                  # 相对 models 根的目标目录（如 "video/_shared/umt5-xxl"）


@dataclass(frozen=True)
class ModelPreset:
    """一个可下载、可一键配置的模型预设。"""
    capability: str            # llm / tts / image / video / asr
    name: str                  # 预设名 = models/<capability>/<name>/ 目录名
    repo_id: str               # ModelScope 仓库
    size_gb: float             # 磁盘占用约值
    desc: str                  # 人可读说明（设置页/下载脚本展示）
    backend: str               # 推荐后端（选中预设时自动填入）
    params: dict[str, Any] = field(default_factory=dict)
    shared: tuple[SharedDownload, ...] = ()

    # ------------------------------------------------------------------
    @property
    def dir_rel(self) -> str:
        """相对 models 根的目录（POSIX，写进设置 JSON 用）。"""
        return f"{self.capability}/{self.name}"

    def local_dir(self) -> "paths.Path":
        """绝对路径：models/<capability>/<name>/。"""
        return paths.models_root() / self.capability / self.name

    def is_downloaded(self) -> bool:
        """已下载 = 目录存在且含至少一个文件（顶层或子目录）。"""
        d = self.local_dir()
        if not d.is_dir():
            return False
        return any(d.iterdir())

    def download_command(self) -> str:
        return (f"python scripts/download_models.py "
                f"--capability {self.capability} --preset {self.name}")


# ----------------------------------------------------------------------
# 注册表本体
# ----------------------------------------------------------------------
_UMT5 = SharedDownload(
    repo_id="Wan-AI/Wan2.1-T2V-1.3B",
    file_pattern="google/umt5-xxl/*",
    into="video/_shared/umt5-xxl",
)
_QWEN_BASE = SharedDownload(
    repo_id="Qwen/Qwen-Image",
    file_pattern="text_encoder/*,vae/*,tokenizer/*",
    into="image/_shared/qwen-image-base",
)
_QWEN_EDIT_PROC = SharedDownload(
    repo_id="Qwen/Qwen-Image-Edit",
    file_pattern="processor/*",
    into="image/_shared/qwen-image-edit-processor",
)

REGISTRY: dict[str, dict[str, ModelPreset]] = {
    "llm": {
        "qwen2.5-0.5b": ModelPreset(
            "llm", "qwen2.5-0.5b", "qwen/Qwen2.5-0.5B-Instruct", 1.0,
            "剧本/分镜生成入门档，CPU 可跑", "modelscope",
            {"model_id": "models/llm/qwen2.5-0.5b"}),
        "qwen2.5-1.5b": ModelPreset(
            "llm", "qwen2.5-1.5b", "qwen/Qwen2.5-1.5B-Instruct", 3.0,
            "推荐：质量/资源均衡", "modelscope",
            {"model_id": "models/llm/qwen2.5-1.5b"}),
        "qwen2.5-7b": ModelPreset(
            "llm", "qwen2.5-7b", "qwen/Qwen2.5-7B-Instruct", 15.0,
            "高质量档（需 GPU）", "modelscope",
            {"model_id": "models/llm/qwen2.5-7b"}),
    },
    "tts": {
        "cosyvoice2-0.5b": ModelPreset(
            "tts", "cosyvoice2-0.5b", "iic/CosyVoice2-0.5B", 5.0,
            "推荐：多音色中文配音", "cosyvoice",
            {"model_dir": "models/tts/cosyvoice2-0.5b"}),
        "chattts": ModelPreset(
            "tts", "chattts", "pzc163/chatTTS", 2.0,
            "对话感中文配音（另需 pip install ChatTTS）", "chattts",
            {"model_dir": "models/tts/chattts"}),
        "gpt-sovits": ModelPreset(
            "tts", "gpt-sovits", "AIDub/GPT-SoVITS", 4.0,
            "声音克隆配音（另需 GPT-SoVITS 仓库源码安装）", "gpt_sovits",
            {"ref_audio": "填参考音频 wav 路径", "prompt_text": "填参考音频文本"}),
        "fish-speech-1.5": ModelPreset(
            "tts", "fish-speech-1.5", "fishaudio/fish-speech-1.5", 8.0,
            "多语言配音/克隆（另需 fish-speech 仓库源码安装）", "fish_speech",
            {"checkpoint_dir": "models/tts/fish-speech-1.5"}),
    },
    "image": {
        "sd15": ModelPreset(
            "image", "sd15", "AI-ModelScope/stable-diffusion-v1-5", 5.0,
            "入门：快、省显存", "diffsynth",
            {"model_preset": "sd15", "steps": 28, "guidance": 7.0}),
        "sdxl": ModelPreset(
            "image", "sdxl", "stabilityai/stable-diffusion-xl-base-1.0", 9.0,
            "推荐：关键帧质量好（需 ≥10GB 显存）", "diffsynth",
            {"model_preset": "sdxl", "steps": 28, "guidance": 7.0}),
        "flux-schnell": ModelPreset(
            "image", "flux-schnell", "AI-ModelScope/FLUX.1-schnell", 12.0,
            "高质量档，4 步出图（Apache-2.0）", "diffsynth",
            {"model_preset": "flux-schnell", "steps": 4}),
        "qwen-image": ModelPreset(
            "image", "qwen-image", "Qwen/Qwen-Image", 40.0,
            "Qwen-Image 文生图：中文语义强（需 ≥24GB 显存）", "diffsynth",
            {"model_preset": "qwen-image", "steps": 40},
            shared=(_QWEN_BASE,)),
        "qwen-image-edit": ModelPreset(
            "image", "qwen-image-edit", "Qwen/Qwen-Image-Edit-2509", 40.0,
            "多图编辑：角色一致性（参考图锁定外貌）", "diffsynth",
            {"model_preset": "qwen-image-edit", "steps": 40},
            shared=(_QWEN_BASE, _QWEN_EDIT_PROC)),
    },
    "video": {
        "wan2.2-ti2v-5b": ModelPreset(
            "video", "wan2.2-ti2v-5b", "Wan-AI/Wan2.2-TI2V-5B", 15.0,
            "推荐：图生视频（单模型 T2V+I2V，≈8GB 显存）", "diffsynth_wan",
            {"model_preset": "wan2.2-ti2v-5b"},
            shared=(_UMT5,)),
        "wan2.1-t2v-1.3b": ModelPreset(
            "video", "wan2.1-t2v-1.3b", "Wan-AI/Wan2.1-T2V-1.3B", 8.0,
            "轻量：文生视频/视频续写", "diffsynth_wan",
            {"model_preset": "wan2.1-t2v-1.3b"},
            shared=(_UMT5,)),
        "wan2.2-i2v-a14b": ModelPreset(
            "video", "wan2.2-i2v-a14b", "Wan-AI/Wan2.2-I2V-A14B", 60.0,
            "高质量 I2V（MoE，需 ≥24GB 显存）", "diffsynth_wan",
            {"model_preset": "wan2.2-i2v-a14b"},
            shared=(_UMT5,)),
        "wan2.1-flf2v-14b": ModelPreset(
            "video", "wan2.1-flf2v-14b", "Wan-AI/Wan2.1-FLF2V-14B-720P", 60.0,
            "首尾帧过渡：镜头间平滑转场（需 ≥24GB 显存）", "diffsynth_wan",
            {"model_preset": "wan2.1-flf2v-14b"},
            shared=(_UMT5,)),
    },
    "asr": {
        "sensevoice-small": ModelPreset(
            "asr", "sensevoice-small", "iic/SenseVoiceSmall", 1.0,
            "推荐：中文语音识别（字幕校对）", "funasr",
            {"model": "models/asr/sensevoice-small"}),
    },
}

#: 各能力默认预设（不加 --preset 时下载它）
DEFAULT_PRESET: dict[str, str] = {
    "llm": "qwen2.5-1.5b", "tts": "cosyvoice2-0.5b",
    "image": "sd15", "video": "wan2.2-ti2v-5b",
    "asr": "sensevoice-small",
}


# ----------------------------------------------------------------------
# 查询接口
# ----------------------------------------------------------------------
def find_preset(capability: str, name: str) -> ModelPreset | None:
    """按能力 + 预设名查找（不存在返回 None）。"""
    return REGISTRY.get(capability, {}).get(name)


def preset_local_dir(capability: str, name: str) -> "paths.Path":
    """预设的本地目录（不保证存在）。"""
    return paths.models_root() / capability / name


def shared_local_dir(into: str) -> "paths.Path":
    """共享组件的本地目录：models/<into>/。"""
    return paths.models_root() / into


def validate_registry() -> None:
    """注册表自检（导入时执行）：预设名合法、无重名、params 可序列化。"""
    for cap, presets in REGISTRY.items():
        if cap not in ("llm", "tts", "image", "video", "asr"):
            raise ValueError(f"未知能力: {cap}")
        for name, preset in presets.items():
            if preset.name != name:
                raise ValueError(f"预设名不一致: {name} != {preset.name}")
            if not _PRESET_NAME_RE.match(name):
                raise ValueError(f"非法预设名（须 kebab-case）: {name!r}")
            for shared in preset.shared:
                if not shared.into.startswith(f"{cap}/_shared/"):
                    raise ValueError(
                        f"{cap}/{name} 共享组件目录越界: {shared.into}")


def catalog() -> dict[str, Any]:
    """API 用目录：含已下载检测与自动填充模板（JSON 安全）。"""
    out: dict[str, Any] = {"models_root": str(paths.models_root()),
                           "capabilities": {}}
    for cap, presets in REGISTRY.items():
        items = []
        for preset in presets.values():
            items.append({
                "name": preset.name,
                "repo_id": preset.repo_id,
                "size_gb": preset.size_gb,
                "desc": preset.desc,
                "backend": preset.backend,
                "params": dict(preset.params),        # 自动填充 JSON 模板
                "dir_rel": preset.dir_rel,            # models/ 下相对目录
                "downloaded": preset.is_downloaded(),
                "download_command": preset.download_command(),
                "default": DEFAULT_PRESET.get(cap) == preset.name,
            })
        out["capabilities"][cap] = items
    return out


validate_registry()  # 导入即自检，配置错误在启动时暴露

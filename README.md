# ShortDrama Studio · 短剧工坊

> **完全离线可用**的对话式连续短剧生成平台 —— 一句话创意，多集成片。
>
> 对话驱动 · 阶段化流水线 · 手工重试 · 断点续跑 · 多模型可选（不选有默认）

[![CI](https://github.com/brother2050/shortdrama-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/brother2050/shortdrama-studio/actions/workflows/ci.yml)

---

## 它能做什么

用聊天的方式完成连续短剧的生产全流程：

```
你：创建一部 3 集的都市爱情短剧，名字叫《晚风便利店》
AI：好的，项目已创建。世界观：……角色：林晚（女主）……

你：生成第 1 集
AI：第 1 集流水线已启动：剧本 → 分镜 → 配音 → 关键帧 → 镜头片段 → 字幕 → 合成
    （每个阶段实时显示进度，产物即时可预览）

你：第 1 集的分镜重新生成，每集 6 个镜头
AI：已强制重跑分镜阶段，后续阶段可继续生成。

你：重试失败的任务
AI：已重新执行「合成」阶段，第 1 集成片就绪：episode.mp4
```

- **完全离线**：核心零云依赖；接入 ModelScope 本地模型（Qwen / CosyVoice2 / FunASR / Wan2.1 / SD / FLUX）后全栈离线生产。
- **任何环境可跑**：无 GPU / 无模型时自动降级到内置 mock 后端，全流程依旧可以演示与测试出片（ffmpeg Ken Burns 运镜）。
- **连续性保证**：角色资产（人设/外貌/音色）跨集复用，前情摘要滚动传递，关键帧提示词自动锁定角色外貌。
- **手工重试哲学**：代码中**没有任何自动重试**；失败即停、如实上报，由你决定何时、从哪个阶段重试。
- **人性化可读**：所有产物落盘为 Markdown / JSON / SRT / 媒体文件，目录结构一眼可懂。

## 快速开始

```bash
# 1. 安装（核心依赖仅 2 个：fastapi + uvicorn）
pip install -r requirements.txt

# 2. 启动（需要系统已安装 ffmpeg 用于视频合成；健康页会提示）
python -m app            # 或 uvicorn app.main:app --host 127.0.0.1 --port 8320

# 3. 打开
http://127.0.0.1:8320
```

零配置即可体验全流程（mock 后端）；接入真实模型见下方「接入离线模型」。

## 八阶段流水线

| # | 阶段 | 产物 | 能力（可选后端，默认 auto） |
|---|---|---|---|
| 1 | worldview 世界观 | `worldview.md` + 角色资产 | LLM：mock / transformers_qwen / ollama |
| 2 | script 剧本 | `script.md` `script.json` | LLM 同上 |
| 3 | storyboard 分镜 | `storyboard.json` | LLM 同上 |
| 4 | voiceover 配音 | `shots/*/vo.wav` | TTS：mock / cosyvoice / chattts / gpt_sovits / fish_speech |
| 5 | keyframes 关键帧 | `shots/*/keyframe.png` | 图像：mock / diffusers(SD/SDXL/FLUX/Qwen-Image) |
| 6 | clips 镜头片段 | `shots/*/clip.mp4` | 视频：kenburns(ffmpeg，默认) / wan_i2v(Wan2.1/2.2) |
| 7 | subtitles 字幕 | `episode.srt` | ASR：script(默认) / funasr(SenseVoice/Paraformer) |
| 8 | compose 合成 | `episode.mp4` | ffmpeg |

每一阶段都是独立任务：可单独重试、可强制重跑、产物即断点。

## 接入离线模型（可选，按需）

```bash
pip install -r requirements-models.txt      # 按能力分组，可只装需要的组
python scripts/download_models.py --capability llm --local-dir ./models   # ModelScope 离线下载
# 然后在「设置」页选择后端并填本地模型路径，或保持 auto 自动探测
```

模型选型表（显存/许可/下载命令）见 [docs/models.md](docs/models.md)，离线部署见 [docs/offline.md](docs/offline.md)。

## 目录结构

```
app/                # 后端：api/ adapters/ pipeline tasks chat continuity composer store events config
web/                # 前端：零构建 vanilla ES Modules（无 npm、无 CDN）
scripts/            # 模型离线下载等工具
docs/               # 模型手册 / 离线部署 / API 文档
tests/              # 12 组 pytest 测试（全离线可跑）
data/               # 运行时数据（gitignore）：SQLite + 各项目人可读产物
```

完整设计（架构/数据模型/状态机/扩展指南）见 [DESIGN.md](DESIGN.md)。

## 测试

```bash
pip install -r requirements-dev.txt
pytest                       # 全部测试（含端到端 mock 出片）
pytest tests/test_pipeline.py -q
```

## 扩展一个新后端

```python
# app/adapters/plugins/my_tts.py  —— 放入 plugins 目录即自动注册，无需改动任何现有代码
from app.adapters.base import AdapterBase, AdapterSpec, register_adapter

@register_adapter
class MyTTS(AdapterBase):
    spec = AdapterSpec(name="my_tts", capability="tts", display_name="我的TTS",
                       priority=30, requires=[], default_params={"speed": 1.0})
    def run(self, ctx, params, progress):
        ...  # 契约见 app/adapters/base.py
```

## 许可

MIT License，见 [LICENSE](LICENSE)。所引用模型请遵循各自许可证（详见 docs/models.md）。

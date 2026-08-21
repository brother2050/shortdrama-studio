# 完全离线部署指南

## 离线能力边界

ShortDrama Studio 设计目标：**断网环境从安装到出片全流程可用**。

| 环节 | 离线方案 | 说明 |
|---|---|---|
| 安装 | `pip install fastapi uvicorn`（仅 2 个核心依赖） | 无重依赖即可运行全链路（mock 后端） |
| 剧本 LLM | Qwen 本地推理 / Ollama 本机服务 / 内置模板 | 三级可选 |
| 配音 | CosyVoice2 / ChatTTS 本地 / GPT-SoVITS·Fish 服务 / 内置合成音 | 多音色与克隆角色映射（`mosaic` 后端四引擎内置路由） |
| 关键帧 | SD/SDXL/FLUX 本地 diffusers / 内置构图生成 | 风格提示词统一 |
| 镜头 | ffmpeg Ken Burns（零模型）/ Wan2.1 图生视频 | 关键帧一致性 |
| 字幕 | 剧本台词对齐（默认）/ SenseVoice ASR | 无需字体文件（mov_text 软字幕） |
| 成片 | 本地 ffmpeg | 拼接 + 音轨 + 软字幕 |
| 前端 | 随服务分发的静态文件（零构建、无 CDN） | 无外网请求 |

## 两档部署

### 档一：零模型快速体验（任何机器）

```bash
pip install -r requirements.txt     # fastapi + uvicorn
python -m app                        # 打开 http://127.0.0.1:8320
```

对话输入「创建一部 3 集的都市爱情短剧，名字叫《晚风》」→「生成第 1 集」，
8 阶段流水线全程 mock 后端，产出可播放 MP4（含软字幕）。

### 档二：高质量离线（GPU 机器）

```bash
pip install -r requirements.txt -r requirements-models.txt
python scripts/download_models.py        # 首次联网下载（或离线拷贝 models/ 目录）
# 断网后：
python -m app
# 设置页把 llm/tts/image/video 换成本地后端并填 model_path
```

离线迁移：把整个 `models/` 目录拷贝到目标机器，设置页 `model_path` 指向即可。

## 数据目录

- 默认 `./data/`，可用环境变量 `STUDIO_DATA_DIR` 重定向
- `data/config.json`：全部设置（人可读，可直接手工编辑，改完即时生效）
- `data/studio.db`：项目/分集/任务/对话（SQLite WAL）
- `data/projects/<id>/`：全部产物，目录即语义：

```
projects/<id>/
  project.json              # 项目配置（人可读）
  worldview.md              # 世界观设定
  episodes/e01/
    script.md / script.json # 剧本（人读 + 机读双格式）
    storyboard.json         # 分镜表
    episode.srt             # 字幕
    episode.mp4             # 成片
    shots/s001/             # 每镜头：keyframe.png / vo.wav / clip.mp4
```

## 手工重试模型（核心交互约定）

- 任何阶段失败：任务停在该阶段，错误中文入库，**不自动重试**
- 重试入口：对话「重试失败的任务」/ 项目页失败卡片 / 任务中心按钮 / REST
  `POST /api/tasks/{id}/retry`
- 重试次数完全由用户决定；断点续跑复用已完成阶段的产物检查点

## 服务运维

```bash
STUDIO_DATA_DIR=/data/sd STUDIO_MAX_WORKERS=8 python -m app          # 前台
uvicorn app.main:app --host 0.0.0.0 --port 8320                       # 生产
```

- 服务重启后遗留 running/pending 任务自动标记 failed（附中文说明），
  产物检查点仍在，可直接手工重试续跑
- SSE `/api/events` 推送任务进度，断线自动重连（前端内置）

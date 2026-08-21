# ShortDrama Studio（短剧工坊）—— 连续短剧生成平台 完整设计文档

> 版本：v1.0 · 状态：已定稿 · 定位：**完全离线可用**的对话式连续短剧生成平台
>
> 参考项目：MoneyPrinterTurbo / NarratoAI / ShortGPT / Huobao Drama / Jellyfish / VideoClaw（短剧流水线、
> 分镜状态机、资产一致性）、ModelScope 离线模型生态（Qwen / CosyVoice2 / FunASR / Wan2.1 / FLUX / SD）。

---

## 1. 目标与非目标

### 1.1 目标
| 目标 | 说明 | 验收方式 |
|---|---|---|
| 完整解决方案 | 从"一句话创意"到"多集成片 MP4"的全流程闭环 | 端到端测试跑通 mock 全链路 |
| 完全离线 | 不调用任何云 API；模型可提前用 ModelScope 下载到本地 | 代码无外网请求；mock 后端零依赖可跑 |
| 扩展性 | 五大能力（LLM/TTS/图像/视频/ASR）均为适配器，注册表 + 插件目录可扩展 | 新增后端仅需 1 个文件 + 注册 |
| 可用性 | 后端可用性探测、优雅降级、断点续跑、SSE 实时进度 | `/api/system/health` 与流水线跳过已完成阶段 |
| 人性化可读 | 产物全部人可读（JSON/Markdown/SRT/媒体文件），目录命名清晰 | `data/projects/<id>/` 结构检查测试 |
| 对话式生成 | 用自然语言创建项目、生成分集、重试失败阶段 | 对话意图解析测试 + 前端聊天界面 |
| 手工重试 | 失败不自动重试（代码中无重试次数常量），用户点按钮或对话重试 | `grep -r "retry" 无次数循环` + 任务重试 API |
| 模型可选可默认 | 每个能力多个后端可选；不选择时自动探测可用的最优后端 | 设置页 + `auto` 选择逻辑测试 |

### 1.2 非目标
- 不内置模型权重（提供 `scripts/download_models.py` 离线下载工具）。
- 不做分布式/多机部署（单机即可跑通，架构不阻碍未来扩展）。
- 不做数字人对口型（预留 `digital_human` 能力扩展位）。

---

## 2. 总体架构

```
┌────────────────────────────────────────────────────────────────────┐
│  web/  前端 SPA（零构建 vanilla ES Modules，无 CDN、无外部字体）      │
│  对话 · 项目/分集/分镜 · 播放器 · 任务 · 设置 · 系统状态   (SSE 实时)  │
├────────────────────────────────────────────────────────────────────┤
│  app/api/  REST + SSE                                              │
│   chat · projects · episodes · tasks · settings · system           │
├────────────────────────────────────────────────────────────────────┤
│  app/chat.py 对话编排层（意图解析 → 指令执行 → 回复生成）              │
│  app/pipeline.py 流水线（8 阶段状态机 + 断点续跑 + 手工重试）          │
│  app/tasks.py 任务管理器（ThreadPool + SQLite 持久化 + EventBus）    │
│  app/continuity.py 连续性（世界观/角色/场景资产 + 跨集滚动摘要）       │
├────────────────────────────────────────────────────────────────────┤
│  app/adapters/ 能力适配器（注册表模式 + ModelSlot 显存生命周期）        │
│   llm:  mock │ transformers_qwen(ModelScope) │ ollama              │
│   tts:  mock │ cosyvoice │ chattts │ gpt_sovits │ fish_speech     │
│   image:mock │ diffusers(SD/FLUX/Qwen-Image)                        │
│   video:kenburns(ffmpeg，默认) │ wan_i2v(diffusers)                │
│   asr:  script(默认) │ funasr(SenseVoice/Paraformer)                │
├────────────────────────────────────────────────────────────────────┤
│  app/store.py SQLite(sqlite3 标准库) │ app/composer.py ffmpeg 合成   │
└────────────────────────────────────────────────────────────────────┘
```

设计原则（源自参考项目的最佳实践）：
1. **注册表 + 规格描述**：每个后端带 `AdapterSpec`（能力、依赖、默认参数、参数说明），
   惰性导入，未安装依赖时可注册、不可用、可降级。
2. **统一任务中心**（Jellyfish）：所有阶段统一 `Task` 模型，状态机
   `pending → running → succeeded / failed / canceled`，支持取消与手工重试。
3. **分镜级状态机**（Jellyfish/VideoClaw）：阶段产物落盘即检查点，重跑自动跳过已完成阶段（可 `force` 强制）。
4. **资产一致性**（Huobao/Jellyfish）：角色/场景是项目级共享资产，跨集引用；角色 → 固定音色 + 固定外貌提示词。
5. **对话驱动**（VideoClaw）：自然语言 → 意图 → 复用同一套 REST 指令，聊天只是薄编排层。

---

## 3. 数据模型与存储

### 3.1 SQLite 表（`data/studio.db`，`sqlite3` 标准库，WAL 模式）
| 表 | 字段 | 说明 |
|---|---|---|
| projects | id, name, genre, style, premise, status, config_json, created_at, updated_at | 项目 + 项目级配置覆盖 |
| episodes | id, project_id, idx, title, synopsis, status, created_at, updated_at | 分集（连续短剧的"集"） |
| tasks | id, project_id, episode_id, stage, status, params_json, error, artifacts_json, created_at, started_at, finished_at | 阶段任务 |
| chat_messages | id, project_id, role, content, actions_json, created_at | 对话历史 |

id 用 `uuid4().hex[:12]`，时间用 ISO8601，全部人可读。

### 3.2 磁盘产物（人性化可读，全部 pretty-print）
```
data/
├── studio.db                      # SQLite
├── config.json                    # 全局设置（人可读、可直接改）
└── projects/<project_id>/
    ├── project.json               # 项目元数据 + 资产（角色/场景/音色分配）
    ├── worldview.md               # 世界观设定（Markdown，人可读）
    └── episodes/e<idx:02d>/
        ├── script.md              # 剧本（Markdown 人可读）
        ├── script.json            # 剧本结构化（scenes/dialogues）
        ├── storyboard.json        # 分镜（shots：画面提示/时长/运镜/角色）
        ├── voiceover.json         # 配音实测时长（镜头序号→秒；创作产物与度量数据分离）
        ├── shots/s<idx:03d>/
        │   ├── keyframe.png       # 关键帧图
        │   ├── clip.mp4           # 镜头视频片段
        │   └── vo.wav             # 旁白/对白配音
        ├── episode.srt            # 字幕
        ├── timeline.json          # 合成时间轴（人可读）
        └── episode.mp4            # 成片
```

---

## 4. 流水线设计（8 阶段）

每集按序执行，**每阶段一个 Task**，产物落盘即检查点：

| 阶段 | 名称 | 输入 | 输出 | 能力 | 失败处理 |
|---|---|---|---|---|---|
| 1 | `worldview` 世界观 | 创意/题材/风格 | worldview.md、角色/场景资产 | LLM | 手工重试 |
| 2 | `script` 剧本 | 世界观 + 角色 + 前情摘要 | script.md/json | LLM | 手工重试 |
| 3 | `storyboard` 分镜 | 剧本 + 角色外貌 | storyboard.json | LLM | 手工重试 |
| 4 | `voiceover` 配音 | 对白 + 角色音色 | shots/*/vo.wav + durations | TTS | 手工重试（单句粒度） |
| 5 | `keyframes` 关键帧 | 分镜画面提示 + 风格 | shots/*/keyframe.png | 图像 | 手工重试（单镜头粒度） |
| 6 | `clips` 镜头片段 | 关键帧 + 时长 + 运镜 | shots/*/clip.mp4 | 视频 | 手工重试（单镜头粒度） |
| 7 | `subtitles` 字幕 | 剧本 + 实测配音时长 | episode.srt | ASR/脚本 | 手工重试 |
| 8 | `compose` 合成 | 时间轴 + 全部素材 | episode.mp4 | ffmpeg | 手工重试 |

规则：
- 顺序：4/5 可并行（实现中串行执行以保证确定性，架构预留并行位）；其余严格顺序。
- **代码中没有任何自动重试**：阶段失败 → 任务置 `failed` + 记录错误 → 流水线停止 →
  用户在 UI 点"重试"或对话说"重试配音" → `POST /api/tasks/{id}/retry`。
- 断点续跑：阶段开始时检查产物是否齐备（如所有 vo.wav 存在且比剧本新），齐备则 `succeeded(skipped)`。
  传 `force=true` 强制重跑。
- 配音在关键帧之前（第 4→5 调序为 4=voiceover, 5=keyframes），使片段时长以配音实测时长为准，字幕零漂移。

### 4.1 连续性（多集连贯）
1. **资产层**：`project.json` 存角色（姓名/人设/外貌提示词/音色 id/参考图）与场景库，跨集复用。
2. **剧情层**：第 N 集剧本生成的上下文 = 世界观 + 角色卡 + 前 N-1 集梗概（每集完成时由剧本压缩成 ≤120 字摘要滚动传递）。
3. **视觉层**：关键帧提示词自动拼接角色的固定外貌描述 + 全局风格词，保证跨镜头一致（参考 Jellyfish 实体模型 + Huobao 外貌锁定）。
4. **听觉层**：角色 → 音色映射在 worldview 阶段一次性分配，之后各集 TTS 复用。

---

## 5. 能力适配器与模型选择（每项可选、均有默认值）

> 选择方式：全局 `config.json` 的 `capabilities.<能力>.backend` + `params`；
> 项目级可在 `project.config` 覆盖。`"auto"`（默认）= 按"可用性探测 + 优先级"自动挑选。
> 修改设置**立即生效**（适配器实例按配置懒加载并缓存，配置变更即失效重载）。

### 5.1 LLM（剧本/世界观/分镜）
| 后端 | 依赖 | 模型（ModelScope id） | 显存 | 许可 | 备注 |
|---|---|---|---:|---|---|
| `mock`（默认兜底） | 无 | - | 0 | - | 模板化中文短剧生成，保证任何环境可演示/测试 |
| `transformers_qwen` | torch+transformers | `Qwen/Qwen2.5-0.5B-Instruct`（CPU）· `Qwen2.5-1.5B/7B-Instruct`（GPU）· `Qwen3-0.6B/1.7B/4B` | 1–14GB | Apache-2.0 | 本地目录离线加载 `from_pretrained(local_path)` |
| `ollama` | 本机 Ollama 服务 | qwen2.5:0.5b/1.5b/7b 等 | 0（外部进程） | Apache-2.0 | OpenAI 兼容接口 `http://127.0.0.1:11434` |

参数（含默认值）：`model_path`(默认 auto 首个存在的本地模型)、`device`(auto/cpu/cuda)、
`max_new_tokens`(1024)、`temperature`(0.8)。`mock` 无参数。

### 5.2 TTS（配音）
| 后端 | 依赖 | 模型 | 采样率 | 显存 | 许可 | 备注 |
|---|---|---|---:|---:|---|---|
| `mock`（默认兜底） | 无（stdlib wave） | - | 24000 | 0 | MIT | 按音色生成可听的正弦谐波音轨，时长=按字数估算，离线可跑 |
| `cosyvoice` | cosyvoice 包 | `iic/CosyVoice2-0.5B`（需另下 `iic/CosyVoice-ttsfrd`） | 24000 | 2GB | Apache-2.0 | 质量最佳，多音色 SFT，ModelScope 离线 |
| `chattts` | ChatTTS 包 | `pzc163/chatTTS` | 24000 | 1.5GB | CC-BY-NC-4.0 | 对话感；音色固定种子（同角色跨集一致） |
| `gpt_sovits` | GPT_SoVITS 包（仓库源码安装） | `AIDub/GPT-SoVITS` | 32000 | 3GB | MIT | 声音克隆：`ref_audio`+`prompt_text` 或 `voice_refs` 按角色映射 |
| `fish_speech` | fish_speech 包（仓库源码安装） | `fishaudio/fish-speech-1.5` | 由 codec 决定 | 4GB | CC-BY-NC-SA | LLM+Codec 多语言；`voice_refs` 参考音频克隆 |

四后端全部本地库推理（无 HTTP 服务），统一走 `ModelSlot(capability="tts")` 管理：
加载前显存检查、OOM 回退 CPU、设置页切换后端时自动释放旧模型显存。
参数：`model_dir`/`checkpoint_dir`（模型目录）、`voice_map`（角色→音色）、
`ref_audio`+`prompt_text`（克隆）、`voice_refs`（按角色克隆映射）、`speed`。

### 5.3 图像（关键帧/角色参考图）
| 后端 | 依赖 | 模型（ModelScope id） | 显存 | 许可 | 备注 |
|---|---|---|---:|---|---|
| `mock`（默认兜底） | 无（纯 stdlib PNG 编码器） | - | 0 | - | 按分镜提示词哈希生成电影感占位卡（场景色 + 标题 + 镜头号），可离线全链路演示 |
| `diffusers` | torch+diffusers | `AI-ModelScope/stable-diffusion-v1-5`（4–6GB）· `stabilityai/stable-diffusion-xl-base-1.0`（6–10GB）· `AI-ModelScope/FLUX.1-schnell`（8–12GB）· `Qwen/Qwen-Image-2512` | 4–24GB | Apache-2.0(FLUX-schnell) 等 | 本地目录 `StableDiffusionPipeline.from_pretrained(path)` |

参数：`model_path`、`width`(1280)、`height`(720)、`steps`(28)、`guidance`(7.0)、`negative_prompt`(默认通用负面词)。

### 5.4 视频（镜头片段）
| 后端 | 依赖 | 模型 | 显存 | 许可 | 备注 |
|---|---|---|---:|---|---|
| `kenburns`（默认） | ffmpeg 二进制 | - | 0 | - | 关键帧 + zoompan 运镜（推/拉/摇）+ 时长对齐配音，**永远可用**，无 GPU 也出片 |
| `wan_i2v` | torch+diffusers | `Wan-AI/Wan2.1-T2V-1.3B`(≈8GB) · `Wan2.2-TI2V-5B`(单卡4090) | 8GB+ | Apache-2.0 | `WanImageToVideoPipeline.from_pretrained(local)`，图生视频保持首帧一致 |

参数：`motion`(auto/in/out/pan)、`fps`(24)、`duration_cap`(10s，单镜头上限)、`model_path`、`num_frames`(81)。

### 5.5 ASR/字幕对齐
| 后端 | 依赖 | 模型 | 显存 | 备注 |
|---|---|---|---:|---|
| `script`（默认） | 无 | - | 0 | 直接用剧本对白 + TTS 实测时长生成 SRT，零漂移 |
| `funasr` | funasr+modelscope | `iic/SenseVoiceSmall`（CPU 友好）· `iic/speech_paraformer-large...vocab8404`（带时间戳） | 0.5–1GB | 对已合成音频二次校对字幕时间戳 |

### 5.6 后端可用性探测
`AdapterSpec.requires`（Python 包名列表）+ `is_available()`：
- import 探测（`importlib.util.find_spec`）
- 外部二进制/服务探测（ffmpeg `shutil.which`；ollama 端口 `socket` 试连）
- `/api/system/health` 汇总展示；`auto` 模式按 `priority` 排序取首个可用者。

---

## 6. 对话编排（chat.py）

```
用户消息 → 意图解析（real LLM: JSON 意图；mock: 规则中文解析）
        → 动作执行（复用 REST 同一套 service 函数）
        → 回复生成（动作结果 → 模板 / LLM 润色）
        → 消息与动作落库（actions_json，前端可渲染"动作卡片"）
```

支持的意图（每种都有对应 REST 等价物，保证可测性）：
| 意图 | 示例说法 | 动作 |
|---|---|---|
| create_project | "创建一部都市爱情短剧，叫《晚风》" | 建项目 |
| set_preferences | "用 CosyVoice 配音 / 每集 4 个镜头" | 改项目配置 |
| generate_episode | "生成第 2 集 / 继续下一集" | 建集 + 跑流水线 |
| regenerate_stage | "重新生成第 1 集的分镜 / 重试配音" | 对指定阶段 force 重跑 |
| retry_failed | "重试失败的任务" | 重试最近失败任务 |
| cancel_task | "取消当前任务" | 取消 running 任务 |
| status_query | "现在什么进度了？" | 汇报任务/阶段状态 |
| list_projects | "我有哪些项目？" | 列表 |
| help | "你能做什么？" | 帮助 |

---

## 7. API 设计（全部有测试覆盖）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat` | 对话：`{project_id?, message}` → `{reply, actions, project_id}` |
| GET/POST | `/api/projects` | 列表 / 创建 |
| GET/DELETE | `/api/projects/{pid}` | 详情（含资产、分集、各阶段状态）/ 删除 |
| PATCH | `/api/projects/{pid}` | 改名称/配置 |
| GET/POST | `/api/projects/{pid}/episodes` | 分集列表 / 新建分集（可带梗概） |
| GET | `/api/projects/{pid}/episodes/{idx}` | 分集详情（分镜/产物路径/阶段状态） |
| POST | `/api/episodes/{eid}/generate` | `{stage?: "all"|阶段名, force?: bool}` 触发流水线 |
| GET | `/api/tasks` | 任务列表（过滤 project/episode/status） |
| POST | `/api/tasks/{tid}/retry` | **手工重试**（唯一重试入口，代码无自动重试） |
| POST | `/api/tasks/{tid}/cancel` | 取消 |
| GET/PUT | `/api/settings` | 读/写全局配置（capabilities.*） |
| GET | `/api/system/health` | 后端可用性矩阵、ffmpeg、磁盘、版本 |
| GET | `/api/events` | SSE：任务状态、进度日志、聊天事件 |
| GET | `/` `/static/*` | 前端静态资源 |

## 8. 前端设计（web/，零构建）

- **技术**：原生 ES Modules + History API 路由 + SSE（EventSource）。无 npm、无 CDN、无构建步骤。
- **视图**：`chat`（默认，对话+动作卡片+快捷指令）、`projects`（列表）、`project`（项目详情：世界观/角色卡/分集）、
  `episode`（分镜表格 + 阶段流水线条 + 每阶段重试按钮 + 镜头网格（关键帧/片段/配音试听）+ 成片播放器 + SRT）、
  `tasks`（任务中心）、`settings`（能力/后端/参数，含默认值与说明）、`system`（健康矩阵）。
- **主题**：影院暗色（CSS 变量），中文字体栈回退系统字体，响应式（≥360px）。

## 9. 测试策略（pytest，全部离线可跑）

| 文件 | 覆盖 |
|---|---|
| test_config.py | 默认值、持久化、校验、项目级覆盖 |
| test_registry.py | 注册、去重、auto 选择、依赖探测 |
| test_adapters.py | 各 mock 后端产出合法产物（wav/PNG/mp4 可被 ffmpeg 识别）、参数默认值 |
| test_store.py | 增删改查、外键、JSON 序列化 |
| test_tasks.py | 状态机流转、取消、手工重试、无自动重试、崩溃恢复（重启后 running→pending） |
| test_continuity.py | 角色音色分配、跨集摘要传递、提示词锁定外貌 |
| test_pipeline.py | 端到端（mock）：一集 8 阶段全部成功、断点跳过、失败停止、force 重跑 |
| test_composer.py | ffmpeg 时间轴合成（时长/音轨/字幕轨） |
| test_chat.py | 全部意图的中文解析与执行、失败重试话术 |
| test_api.py | REST 全接口（200/404/422）+ SSE 事件 |
| test_frontend.py | 静态文件齐全、导航/挂载点存在、`node --check` 全部 JS 语法 |
| test_dependencies.py | requirements 每项可导入且在代码中被使用（**最小且有效**）、models 可选组不进入核心 |

CI（GitHub Actions）：`pip install -r requirements.txt -r requirements-dev.txt` + `apt ffmpeg` + `pytest`。

## 10. 目录结构

```
shortdrama-studio/
├── README.md  DESIGN.md  LICENSE  pyproject.toml
├── requirements.txt（核心：fastapi、uvicorn —— 仅 2 个）
├── requirements-dev.txt（pytest、httpx）
├── requirements-models.txt（可选模型栈：torch/transformers/diffusers/modelscope/funasr，分组注释）
├── .github/workflows/ci.yml
├── app/（config/schemas/store/events/tasks/chat/continuity/pipeline/composer + adapters/ + api/）
├── web/（index.html + css/ + js/views/*）
├── scripts/download_models.py（ModelScope snapshot_download 离线下载器）
├── docs/（models.md 模型手册、offline.md 离线部署、api.md）
├── data/（运行时生成，gitignore）
└── tests/（12 个测试文件 + conftest.py）
```

## 11. 依赖清单（最小且有效）

- **核心运行**：`fastapi`、`uvicorn`（其余全为标准库：sqlite3/json/wave/struct/zlib/threading/asyncio/uuid/hashlib/socket/shutil/subprocess/argparse）。mock 图像编码器为纯 stdlib PNG 写入（zlib+struct），TTS 为纯 stdlib wave 合成。
- **开发测试**：`pytest`、`httpx`（FastAPI TestClient 所需）。
- **可选模型栈**（`requirements-models.txt`，按能力分组、全部带用途注释，缺省不安装）：torch、transformers、diffusers、accelerate、modelscope、funasr、ChatTTS；CosyVoice / GPT-SoVITS / Fish Speech 以本地路径安装说明。
- 有专门测试保证：核心依赖均可导入、均被使用；未列依赖不被隐式引用（惰性导入仅在可选后端内）。

## 12. 扩展指南（三步新增一个后端）
1. `app/adapters/` 新建文件，继承 `AdapterBase`，定义 `AdapterSpec`（能力/优先级/依赖/参数及默认值）。
2. 实现 `is_available()` 与 `run()`（输入输出契约见 `adapters/base.py` 文档字符串）。
3. 用 `@register_adapter` 装饰即完成注册；也可放进 `app/adapters/plugins/`（目录自动扫描，无需改任何现有代码）。

## 13. 可用性设计
- 启动自检：`GET /api/system/health` 输出能力可用性矩阵与建议（如"未检测到 ffmpeg，视频合成不可用"）。
- 优雅降级：`auto` 后端选择永不选中不可用后端；mock 兜底保证任何环境全链路可演示。
- 断点续跑：产物即检查点；服务重启后 `running` 任务复位为 `pending`，重新触发即续跑。
- 实时性：SSE 推送任务/进度事件；断线自动重连（EventSource 原生）。
- 可观测：结构化日志 + 任务级 error 字段 + 前端失败卡片一键重试。

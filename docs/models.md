# 模型选择指南（全部可离线）

每个能力都有多个后端，`auto` 模式自动选择当前环境可用的最优后端；
未安装任何重依赖时自动回退到内置 mock 后端（零依赖、确定性，保证全链路可用）。

## 能力 × 后端矩阵

| 能力 | 后端 | 模型 / 来源 | 显存 | 许可 | 说明 |
|---|---|---|---|---|---|
| llm | `mock` | 内置模板 | 0 | MIT | 零依赖兜底，确定性输出 |
| llm | `transformers_qwen` | Qwen/Qwen2.5-{0.5B,1.5B,7B}-Instruct | 2~16GB | Apache-2.0 | ModelScope 离线推理（transformers），0.5B 可 CPU |
| llm | `modelscope` | qwen/Qwen2.5-{0.5B,1.5B,7B}-Instruct | 2~16GB | Apache-2.0 | ModelScope 原生 LLM 推理 |
| tts | `mock` | 内置正弦波 | 0 | MIT | 零依赖兜底 |
| tts | `cosyvoice` | iic/CosyVoice2-0.5B | 2GB | Apache-2.0 | 多音色中文配音（推荐） |
| tts | `chattts` | pzc163/chatTTS | 1.5GB | CC-BY-NC-4.0 | 对话感配音，角色固定种子 |
| tts | `gpt_sovits` | AIDub/GPT-SoVITS | 3GB | MIT | 声音克隆（参考音频） |
| tts | `fish_speech` | fishaudio/fish-speech-1.5 | 4GB | CC-BY-NC-SA | 多语言配音/克隆 |
| image | `mock` | 内置 PNG 生成 | 0 | MIT | 零依赖兜底（色块构图） |
| image | `diffsynth` | SD/SDXL/FLUX | 4~12GB | 各模型许可 | DiffSynth-Studio 关键帧文生图 |
| video | `kenburns` | ffmpeg | 0 | - | 关键帧 Ken Burns 运镜（默认，零依赖） |
| video | `diffsynth_wan` | Wan-AI/Wan2.1-T2V-1.3B | 8GB+ | Apache-2.0 | DiffSynth-Studio 图生视频 |
| asr | `script` | 剧本内置 | 0 | - | 直接用剧本台词做字幕对齐（默认） |
| asr | `funasr` | iic/SenseVoiceSmall | 1GB | FunASR 许可 | 语音识别，字幕时长校对 |

## 下载与启用（三步）

```bash
# 1. 安装可选依赖（核心功能不需要）
pip install -r requirements-models.txt

# 2. 离线下载模型（默认各能力推荐档，可 --list 查看全部档位）
python scripts/download_models.py --capability llm tts image
python scripts/download_models.py --list          # 查看可选档位与体积

# 3. 启动后到「设置」页选择后端并填 model_path（脚本下载完会打印要填的值）
python -m app    # 打开 http://127.0.0.1:8320
```

## 参数默认值

每个后端的默认参数定义在 `app/adapters/*.py` 的 `AdapterSpec.default_params`，
设置页留空即用默认。常用参数：

| 能力 | 参数 | 默认 | 说明 |
|---|---|---|---|
| llm | `max_new_tokens` | 1024 | 单次生成上限 |
| llm | `temperature` | 0.8 | 采样温度 |
| tts | `voice_map` | 内置映射 | 角色 → 音色 |
| image | `steps` / `guidance` | 28 / 7.0 | FLUX.1-schnell 建议 4 / 3.5 |
| video | `num_frames` / `fps` | 81 / 16 | ≈5 秒镜头 |
| 全局 | `shots_per_episode` | 4 | 每集镜头数 |
| 全局 | `target_clip_seconds` | 5.0 | 单镜头目标时长（配音更长则顺延） |
| 全局 | `style` | 电影感, 自然光… | 全局风格提示词（锁视觉一致性） |
| 全局 | `video_output` | 1280×720@24 | 成片画幅 |

## TTS 四后端（全部本地库推理，无 HTTP 服务）

| 后端 | 安装 | 关键参数 | 特点 |
|---|---|---|---|
| `cosyvoice` | `git clone --recursive .../CosyVoice && pip install -e ./CosyVoice` | `model_dir` | 多音色中文，音质最佳（推荐） |
| `chattts` | `pip install ChatTTS` | `model_dir`（可空） | 对话感强；固定种子保角色音色一致 |
| `gpt_sovits` | `git clone .../GPT-SoVITS && pip install -e ./GPT-SoVITS` | `ref_audio` + `prompt_text`，或 `voice_refs` | 参考音频克隆，按角色映射 |
| `fish_speech` | `git clone .../fish-speech && pip install -e ./fish-speech` | `checkpoint_dir`，可选 `ref_audio` | LLM+Codec，多语言；可克隆 |

- 模型离线下载：`python scripts/download_models.py --capability tts --list`（四档可选）
- 克隆类后端（gpt_sovits/fish_speech）：`voice_refs` 参数按角色音色 id 映射不同参考音频
- 显存切换保护：设置页切换 TTS 后端时自动释放旧模型显存（见下节）

## 显存（VRAM）管理

平台内置显存管理模块（`app/vram.py`），自动处理：

- **加载前检查**：模型加载前检查可用显存，不足时给出可读错误（不崩溃）
- **OOM 恢复**：CUDA 显存不足时自动回退到 CPU + float32（附警告日志）
- **智能设备选择**：`device=auto` 优先 CUDA，显存不够自动降级 CPU
- **后端切换释放**：设置页切换某能力后端/参数时，自动释放该能力旧模型（防双份模型占显存）
- **阶段间释放**：流水线在 GPU 密集阶段（keyframes/clips）完成后自动释放模型
- **手动释放**：「系统」页可一键释放所有已加载模型
- **API 接口**：`GET /api/system/vram` 查看状态，`POST /api/system/vram/release` 释放

```bash
# 查看显存状态
curl :8320/api/system/vram

# 手动释放所有模型显存
curl -X POST :8320/api/system/vram/release
```

## 新增后端（扩展）

复制 `app/adapters/plugins/_TEMPLATE.py.example` 为新模块，实现 `run()` 并用
`@register_adapter` 注册即可被 auto 选择与设置页渲染，无需改动任何其他代码。

GPU 后端建议继承 `ModelSlot` 管理模型生命周期，并在 `spec.vram_gb` 声明显存需求，
平台会自动在加载前检查、OOM 时恢复、阶段间释放。

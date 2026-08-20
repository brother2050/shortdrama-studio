# 模型选择指南（全部可离线）

每个能力都有多个后端，`auto` 模式自动选择当前环境可用的最优后端；
未安装任何重依赖时自动回退到内置 mock 后端（零依赖、确定性，保证全链路可用）。

## 能力 × 后端矩阵

| 能力 | 后端 | 模型 / 来源 | 显存 | 许可 | 说明 |
|---|---|---|---|---|---|
| llm | `mock` | 内置模板 | 0 | MIT | 零依赖兜底，确定性输出 |
| llm | `ollama` | qwen2.5:0.5b~7b | 由 Ollama 管 | 模型许可 | 本机 Ollama 服务（OpenAI 兼容） |
| llm | `transformers_qwen` | Qwen/Qwen2.5-{0.5B,1.5B,7B}-Instruct | 2~16GB | Apache-2.0 | 完全离线推理，0.5B 可 CPU |
| tts | `mock` | 内置正弦波 | 0 | MIT | 零依赖兜底 |
| tts | `cosyvoice` | iic/CosyVoice2-0.5B | 4GB | CosyVoice 许可 | 多音色中文配音（推荐） |
| tts | `mosaic` | brother2050/mosaic | 2GB | 见仓库 | mosaic 项目 TTS，四层管线 |
| image | `mock` | 内置 PNG 生成 | 0 | MIT | 零依赖兜底（色块构图） |
| image | `diffusers` | SD1.5 / SDXL / FLUX.1-schnell / Qwen-Image | 4~12GB | 各模型许可 | 关键帧文生图 |
| video | `kenburns` | ffmpeg | 0 | - | 关键帧 Ken Burns 运镜（默认，零依赖） |
| video | `wan_i2v` | Wan-AI/Wan2.1-T2V-1.3B / Wan2.2-TI2V-5B | 8GB+ | Apache-2.0 | 图生视频，镜头动态 |
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

## 显存（VRAM）管理

平台内置显存管理模块（`app/vram.py`），自动处理：

- **加载前检查**：模型加载前检查可用显存，不足时给出可读错误（不崩溃）
- **OOM 恢复**：CUDA 显存不足时自动回退到 CPU + float32（附警告日志）
- **智能设备选择**：`device=auto` 优先 CUDA，显存不够自动降级 CPU
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

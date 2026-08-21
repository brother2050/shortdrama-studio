# REST API 一览

Base URL: `http://127.0.0.1:8320`，全部 JSON。交互式文档：`/docs`（FastAPI 自带）。

## 系统

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/version` | 版本 |
| GET | `/api/system/health` | 能力健康矩阵（配置/实际生效后端）+ 环境 + 显存状态 |
| GET | `/api/system/backends` | 五能力 × 全部后端规格（设置页渲染用） |
| GET | `/api/system/models` | 模型预设目录：各能力预设/参数模板/已下载状态（设置页「模型预设」下拉数据源） |
| GET | `/api/system/vram` | 显存状态（GPU 型号/总量/已用/可用/已加载模型） |
| POST | `/api/system/vram/release` | 释放所有已加载模型（手动回收显存） |
| GET | `/api/settings` | 读设置 |
| PUT | `/api/settings` | 合并更新（深度合并，未提及键保留） |
| GET | `/api/events` | SSE 事件流（task/progress/chat/episode/project） |

## 对话

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/chat` | `{message, project_id?}` → `{reply, actions, project_id}` |
| GET | `/api/chat/intent?message=` | 意图解析预览（调试） |
| GET | `/api/projects/{pid}/chat?limit=` | 对话历史（含动作卡片） |

## 项目与分集

| 方法 | 路径 | 说明 |
|---|---|---|
| GET/POST | `/api/projects` | 列表 / 创建 |
| GET/PATCH/DELETE | `/api/projects/{pid}` | 详情（含分集阶段状态）/ 更新 / 删除 |
| GET/POST | `/api/projects/{pid}/episodes` | 分集列表 / 新建 |
| GET | `/api/projects/{pid}/episodes/{idx}` | 分集详情（剧本/分镜/产物/任务） |
| POST | `/api/episodes/{eid}/generate` | `{stage, force}` 启动/续跑（stage 可为 8 阶段之一或 all） |
| GET | `/api/projects/{pid}/media/{rel}` | 产物受控访问（防目录穿越） |

## 任务

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/tasks?status=&project_id=&episode_id=&limit=` | 任务列表 |
| GET | `/api/tasks/stages` | 8 阶段元信息 |
| POST | `/api/tasks/{tid}/retry` | 手工重试（复用同任务 ID） |
| POST | `/api/tasks/{tid}/cancel` | 协作式取消 |

## 8 阶段流水线

`worldview → script → storyboard → voiceover → keyframes → clips → subtitles → compose`

每阶段一个任务，产物落检查点；`generate(stage=X, force=false)` 从 X 续跑到成片，
`force=true` 只强制重跑 X（已完成阶段产物保留）。

## 典型调用序列

```bash
# 1) 建项目
curl -X POST :8320/api/projects -H 'content-type: application/json' \
  -d '{"name":"晚风","genre":"都市情感","premise":"深夜便利店"}'
# 2) 建分集并生成
curl -X POST :8320/api/projects/<pid>/episodes -d '{"title":"第1集"}' -H 'content-type: application/json'
curl -X POST :8320/api/episodes/<eid>/generate -d '{"stage":"all"}' -H 'content-type: application/json'
# 3) 轮询或订阅 SSE；失败时：
curl -X POST :8320/api/tasks/<tid>/retry
```

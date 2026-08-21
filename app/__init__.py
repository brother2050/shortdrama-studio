"""ShortDrama Studio 后端包。

模块划分（详见 DESIGN.md）：
- config      全局设置（JSON 持久化，含默认值）
- schemas     pydantic 数据模型
- store       SQLite 存储（标准库 sqlite3）
- events      事件总线（SSE 推送）
- adapters/   五大能力适配器（注册表模式 + ModelSlot 显存生命周期）
- tasks       任务管理器（状态机 + 手工重试，无自动重试）
- continuity  连续性（角色资产、跨集摘要、外貌锁定）
- composer    ffmpeg 成片合成
- pipeline    八阶段流水线（断点续跑）
- services    业务编排（REST 与对话共用）
- chat        对话编排（意图解析 → 动作执行 → 回复生成）
- api/        REST 路由
"""

__version__ = "1.0.0"

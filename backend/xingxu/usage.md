# AI 用量

每个供应商请求在发起前记录到 MySQL `ai_usage`，每次调用一个 UUID 主键，原位更新；不依赖聊天记录，不因删除对话而删除用量。失败、截断和中断请求仍记录已知用量。字段只包含账号/工作区、会话/本轮编号、入口、模型连接、模型名、时间、状态及计数，不包含对话正文或密钥。

`UsageScope` 使用 ContextVar 隔离并发调用；DSH broker 显式继承所在本轮作用域。入口为 desktop、wechat、feishu、schedule、test，不能从模型或用户输入指定所属账号。压缩上下文、工具往返、模型连接测试均计入发起账号。WebSocket 入口与定时任务沿用本人执行身份。

Chat Completions 读取 usage 及缓存字段。DeepSeek 原生 Messages 协议读取累计用量，最终 message_delta 覆盖初始 usage，不重复累加；input_tokens 为未命中部分，加 cache_read_input_tokens 与 cache_creation_input_tokens 才得到总输入。若流缺少 message_stop 或最终 usage，则记录可确认部分并标记 usage_complete=false。供应商没返回的计数为 NULL，不能填0假装完整。总览为已确认/已报告计数之和，并显式显示用量不完整请求数；这不是供应商结算账单。

`GET /api/v1/usage/me` 只返回本人今日、本月及累计。`GET /api/v1/usage` 需要 ai:usage:read，只查询当前工作区，按北京时间起止日期统计，可筛选入口/模型。员工列表含头像、姓名、部门和汇总，未发生调用的员工显示0。数据库按工作区、账号和时间索引，SQL 按日期和维度汇总，不把全量请求加载到客户端。

客户端：每条新回复的 usage 为此轮所有模型请求汇总；ERP → AI用量显示趋势、头像排行、入口与模型分布。历史回复没有 usage 时不倒推或假造。仅启用后的真实请求被记录。没有记录的范围不等于历史没有消耗。

迁移：backend/migrations/009_ai_usage.sql。原有数据库备份自动包含本表。离线测试使用私有文件后备，正式服务器使用 MySQL。未新增配额、限流或费用规则。

协议口径：https://api-docs.deepseek.com/guides/anthropic_api/ 。

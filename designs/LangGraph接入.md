# LangGraph 对话执行与上下文管理

2026-11-16：已接入并定向部署 LangGraph `1.2.22`。生产服务仍只在远程服务器运行，客户端继续使用现有 HTTP API，无须因本次后端改动重新安装。

## 执行流程

`backend/xingxu/agent_runtime.py` 使用真正的 `StateGraph`，由 `AiService.complete()` 调用：

```text
START → prepare_context → model ──无工具调用──→ END
                           ↓ 有工具调用
                          tools
                           └────────→ model
```

- `prepare_context`：组装管理员系统提示词、允许的工具分类提示词、历史摘要、完整的近期对话及当前问题。
- `model`：继续调用现有 Chat Completions 适配器；模型、加密 API Key、Base URL、思考参数及错误脱敏沿用原实现。
- `tools`：按模型返回的顺序逐次调用现有 `GalaxyDispatcher.execute()`；不使用另一个工具执行器，不改变用户、工作区或店铺权限检查、凭证注入与审计。
- Graph 的状态只有消息、当前模型回复及调用计数。身份和凭证通过本次调用的依赖传入，不进入 Graph 状态或额外的检查点存储。
- 使用原有按请求所属用户隔离的 `ChatRuns` 打断机制。模型请求、摘要请求或工具执行都能收到取消；已完成的工具事件仍按原规则保存，未完成的工具调用不会作为下一轮的残缺协议上下文。

本次不引入节点自动重试、业务工具调用轮数配额或执行中的任务恢复。Graph 不配置 checkpointer：完整回合和摘要仍由现有 JSON 事务一起提交。进程重启后能继续已保存的聊天，但不会自动重放中断任务中的业务写操作。不需要 LangSmith 账号，也不启用外部追踪。

## 上下文策略

`backend/xingxu/conversation_context.py` 替代原来的“最近 40 条、约 8 万字符”选择方式。

1. 估算系统提示词、工具定义、历史消息、工具协议及当前问题的输入大小。估算采用 ASCII 字符数与非 ASCII UTF-8 字节数的加权方式，**不是模型原生 tokenizer 的精确计数**。
2. 输入超过预算的 75% 时，优先把较早的完整回合压缩为滚动摘要，尽量保留最近 4 个完整回合。确实放不下时继续摘要更近的历史，当前问题和系统规则不会被静默裁掉。
3. 摘要使用当前选中的模型，单次最大输出 1024 Token，不提供工具；DeepSeek 摘要调用关闭思考，正常回答仍沿用用户当轮参数。历史中的原始推理字段不参与事实摘要。
4. 超大历史工具结果会分段送入摘要模型，每段都检查预算；原文片段不会被截掉。摘要保留目标、约束、实体 ID、日期、统计口径及成功/失败状态，历史记录的时间戳一并提供。
5. 历史摘要以 `user` 角色的背景资料注入，不提升为管理员系统规则，也不代表当前执行授权。销售、库存、考勤等实时数据仍需通过工具重新查询。
6. 工具结果增长后重新检查预算，必要时继续摘要较早的历史，为当前任务腾出空间。当前问题、固定提示词、工具参数或当轮工具结果本身仍放不下时返回 `context_too_large`，不发送超预算请求。
7. 同一回合内完全相同的工具结果，以首个完整结果的调用 ID 引用去重，减少多次读取目录等场景的重复输入。每次工具仍实际执行，完整结果保留在原有 `tool_events` 中；不会因去重跳过操作或合并不同结果。

摘要会产生额外模型费用；摘要请求的 usage 计入本轮总 usage。短对话不额外请求摘要。摘要失败不提交本轮或部分摘要；用户主动停止时保留原有“已停止回复”消息语义。

### 配置

服务器环境变量 `GALAXY_AI_INPUT_TOKEN_BUDGET` 默认 `48000`，允许 `4096`～`1000000`。它是**估算输入额度**，不是模型总上下文窗口。需要根据供应商实际窗口设置，另外为输出和估算误差预留空间；切换模型时也要检查这项设置。它不改变控制台中的最大输出 Token 配置。

可以在 systemd drop-in 中配置，例如：

```ini
[Service]
Environment=GALAXY_AI_INPUT_TOKEN_BUDGET=48000
```

模型最终输出额度留空时仍沿用供应商默认值；历史摘要使用独立的 1024 Token 上限。

## 存储兼容

既有 `private/chats.json` 的消息结构与原文保持兼容，仅在需要摘要的对话中增加内部字段：

```json
{
  "context_state": {
    "version": 1,
    "summary": "历史摘要",
    "covered_messages": 20,
    "through_message_id": "被摘要的最后一条消息 ID"
  }
}
```

服务校验摘要游标与原始消息 ID；无效缓存从原始历史重新构建。摘要不会复用到其他对话。对话归属仍先按 `workspace_id + user_id` 检查，再进行任何模型请求。内部摘要字段和 `model_context` 不返回客户端。删除对话时摘要随对话一起删除。

无需批量迁移旧聊天。移除本次运行时代码后，旧实现也可继续读取原始聊天记录；摘要字段只会被忽略。

本次不包含跨会话长期记忆、知识库 RAG、多 Agent 协作、逐字流式输出或正在执行的任务恢复。

## 验证

- `backend/tests/test_agent_runtime.py`：真实 LangGraph 执行、工具批次顺序、超过默认递归层数的流程、失败后不自动重复业务写入、取消清理、并发隔离、预算、增量摘要及大结果分段。
- `backend/tests/test_agent_service.py`：真实 `AiService`、供应商 HTTP 适配器及 `ChatRuns`，使用内存仓库和模拟网关；验证思考协议、身份传递、摘要提交/复用/隐藏、费用汇总、归属检查、摘要失败和取消。模拟仓库与网关不替代 Linux 文件锁、文件访问或真实业务集成验证。
- `backend/tests/test_context_integration.py`：Linux 上的真实 API、身份检查及临时 JSON 仓库，验证摘要持久化、重启后复用、客户端隐藏，以及摘要失败后原文件逐字节保持不变。

Windows 可运行上述离线测试，不启动本地服务：

```powershell
cd backend
uv run --no-project --with langgraph==1.2.22 --with httpx==0.28.1 --with cryptography==50.0.2 --with pytest==9.2.0 --with tzdata python -m pytest tests/test_agent_runtime.py tests/test_agent_service.py -q
```

Linux 服务器的隔离测试环境还需运行现有回归：

```bash
PYTHONPATH=backend:galaxy-cli/src python -m pytest -c backend/pytest.ini backend/tests/test_agent_runtime.py backend/tests/test_agent_service.py backend/tests/test_context_integration.py backend/tests/test_ai.py backend/tests/test_tools.py backend/tests/test_chat_cancel.py backend/tests/test_thinking.py backend/tests/test_tool_prompts.py -q
```

所有这些测试使用临时数据与模拟模型，不应读取生产 `private`，也不运行 `verify-*-live.py`。

## 部署范围

只同步以下运行文件，不整仓覆盖暂停中的商品功能：

- `backend/requirements.txt`
- `backend/xingxu/ai.py`
- `backend/xingxu/agent_runtime.py`
- `backend/xingxu/conversation_context.py`

测试文件与说明文档可同步到隔离测试副本。更新后端虚拟环境依赖并通过回归后，再按现有 systemd 流程重启。私有配置、密钥、聊天记录、有赞/飞书登录状态及客户端安装包无需覆盖。

## 本次验证与上线记录

- Windows 离线运行时与接入测试：`27 passed`。
- 服务器使用线上源码副本和独立的干净 Python 环境验证，未复制生产私有数据：`86 passed in 38.92s`；隔离环境依赖检查通过。
- 只更新上述 4 个运行文件及后端环境所需的新依赖，未上线本地暂停中的商品功能。
- `galaxy-ai.service` 重启后保持 `active`，`/health` 返回 `status=ok`。
- 实际配置的 `deepseek-flash` 连接测试返回 `OK`。
- 实际模型长上下文验证：32 条合成历史中 30 条进入滚动摘要，最终回复“校验通过”；本次共用 6476 Token，未保存正式聊天、未执行业务工具。
- 原有源码及完整后端 Python 环境保存在服务器 `/var/backups/galaxy-ai/langgraph-20261116-4076397d30/`，仅用于本次回滚。原始聊天仍可由旧版读取。

官方参考：[Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)、[Memory](https://docs.langchain.com/oss/python/langgraph/add-memory)、[LangGraph 源码](https://github.com/langchain-ai/langgraph)。

> 2026-11-10 部署更新：生产后端和业务 CLI 仅在远程服务器运行，本地启动、联调及浏览器运行环境已移除。以下含历史设计说明；当前运行方式以 deploy/README.md 和 backend/README.md 为准。

# AI 接入与管理员控制台

管理员和员工使用同一个 Windows 程序。管理员侧边栏显示“控制台”，当前有 AI、有赞两个子页面。有赞为连接管理预留页，尚未执行授权或接入现有有赞登录程序。

工作台与控制台统一使用黑、深灰和白色；AI 子页面顶部提供“AI 模型 / 全局提示词”两个标签，支持点击与方向键切换。

## 配置与使用

1. 管理员进入“控制台 → AI → AI 模型”，点击“添加 AI”。
2. 输入名称、Base URL、API Key、模型 ID，设置最大输出 Token 和启用状态。
3. 保存后点击“测试连接”。服务器向已保存的地址和模型发送一条简短消息，显示成功及耗时，或具体错误类别。
4. 第一组启用的配置自动成为默认 AI；添加多组后，可将其他已启用配置设为默认。
5. 点击顶部“全局提示词”标签，填写统一身份与回复规则，点击保存。两类配置分别展示，切换标签会保留未保存的提示词草稿。
6. 返回 AI 模块即可聊天，普通员工使用同一个默认 AI；各人的对话和历史分别保存。

当前适配 Chat Completions 兼容接口，使用 Bearer API Key。Base URL 填到接口根路径，例如 `https://api.example.com/v1`，服务器追加 `/chat/completions`；也兼容直接填写完整的 `/chat/completions` 地址。DeepSeek 的兼容接口形式可参见 [官方调用示例](https://api-docs.deepseek.com/)。供应商模型名称以其实际提供的模型 ID 为准。

目前每轮等待完整回复后展示，有等待状态；已接通 `galaxy_cli` 的结构化工具调用，当前只授权 `help`。界面显示实际调用及执行结果，安全边界见 [工具执行权限](Galaxy工具执行权限.md)。逐字流式输出、附件、MCP 协议服务和自动重试尚未实现。历史记录从右上角图标打开，支持重新打开、新建和删除自己的对话。

## 思考内容展示

供应商在回复中返回 `reasoning_content`（或兼容的文本 `reasoning`）时，服务器将其与最终回复分开保存，界面在回复上方显示“思考过程”。默认收起，点击或键盘确认可展开和收起，重新打开个人历史后仍可查看。

只展示供应商明确返回的文本；未返回时不显示该入口，也不会根据最终答案生成模拟思考。旧版仅保存最终回复，既有历史中的思考内容无法事后恢复。思考文本与最终答案分开；工具模式会按供应商要求保留并回传必要的原始协议上下文，详情见 [工具执行权限](Galaxy工具执行权限.md)。

当前仍为完整响应后展示，并非实时流式显示。思考文本与聊天消息使用相同的用户归属校验，并同样对供应商 Key 做遮蔽。DeepSeek 的返回字段定义见 [Chat Completions 文档](https://api-docs.deepseek.com/api/create-chat-completion/)。

## 权限与凭证

- 每个 `/api/v1/admin/ai` 管理接口都要求 `ai:configure` 权限，员工即使直接通过 IPC 调用也会被服务器拒绝。
- API Key 使用 Fernet 认证加密后写入 JSON，密钥文件为 `backend/private/ai-secret.key`，权限 `0600`。Windows 不保存供应商 API Key。
- 读取配置只返回 `has_api_key` 状态，不返回密钥明文、密文或密钥后缀。编辑时 API Key 留空会保留原值。
- Base URL 不接受 URL 用户密码、查询参数或 fragment；供应商调用不跟随重定向，避免将 Authorization 头转发到跳转目标。
- 全局提示词只由管理员修改。每轮聊天的 system 消息由服务器注入，客户端不能提交自定义 system 角色或伪造历史。
- 用户身份来自登录会话，按 `user_id + workspace_id` 检查对话归属。管理员的角色也不会自动赋予读取员工个人对话的权限。
- 会话 token 留在 Electron 主进程。供应商 Key 仅在管理员输入新值时临时经过界面，保存后不会回填；模型和普通员工不接收该 Key。

Fernet 的加密与密钥管理原理参见 [Cryptography 文档](https://cryptography.io/en/latest/fernet/)。

## 存储与执行

| 文件 | 内容 |
| --- | --- |
| `private/auth.json` | 用户、角色、登录会话摘要、登录限流 |
| `private/ai.json` | 供应商配置、加密后的 API Key、默认连接和全局提示词 |
| `private/ai-secret.key` | 独立加密主密钥，备份和恢复必须保留 |
| `private/chats.json` | 按用户归属的对话和消息 |

各文件使用自己的锁与原子替换。网络调用在文件事务之外进行，不会因等待模型而锁住 JSON。每次发送先复制默认配置及全局提示词快照，管理改动作用于下一次发送。

服务目前为单进程：同一对话生成中拒绝再次发送，不同对话可以并发调用。2026-11-16 源码接入 LangGraph，按估算输入预算选择完整历史回合，并为较早的对话保存滚动摘要；完整原文仍保存在 JSON。模型失败不会写入半轮消息，客户端保留本次问题并显示失败原因、重试和编辑按钮。实现、预算配置与部署范围见 [LangGraph 接入说明](../designs/LangGraph接入.md)。后续增加多个服务实例时，生成锁和存储应迁移到数据库／共享任务机制。

模型连接超时 10 秒；正常聊天不设置整轮或响应等待的固定时长限制，连接测试仍有网络超时。输入消息最长 8,000 字符（界面当前限制 4,000），全局提示词最长 20,000 字符。上游错误只展示归类后的信息，不展示原始错误体或 Authorization。

## 主要接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/admin/ai` | 管理员读取脱敏配置 |
| POST | `/api/v1/admin/ai/providers` | 新增模型连接 |
| PUT / DELETE | `/api/v1/admin/ai/providers/{id}` | 编辑／删除连接 |
| POST | `/api/v1/admin/ai/providers/{id}/test` | 一键测试已保存的连接 |
| PUT | `/api/v1/admin/ai/default` | 选择默认连接 |
| PUT | `/api/v1/admin/ai/prompt` | 保存全局提示词 |
| GET | `/api/v1/ai/status` | 当前可用模型，不含 URL、Key 或提示词 |
| POST | `/api/v1/chat/send` | 提交消息，服务器维护上下文 |
| GET | `/api/v1/chat/conversations` | 当前用户的历史列表 |
| GET / DELETE | `/api/v1/chat/conversations/{id}` | 读取／删除自己的对话 |

## 验证方式

后端 `tests/test_ai.py` 使用临时数据与受控上游，覆盖实际请求结构、密钥加密、留空保留、权限拒绝、提示词注入、错误脱敏、默认模型切换、并发及对话归属。

历史本地联调接口及其 fixture 启动程序已退役。现在使用 Windows 客户端连接服务器验证，不启动本地测试接口。

DeepSeek 的思考开关与 low/high/max 强度由聊天输入框逐次提交，设置按当前用户保存在客户端，不修改全局供应商配置。关闭思考时发送 thinking.type=disabled、reasoning_effort=none；开启时发送 enabled 和所选强度。不兼容模型不显示该控件。模型 finish_reason=length 时明确报告输出额度截断，不执行残缺工具参数。全局提示词支持 {{current_time}}、{{today}}、{{yesterday}}，由服务器按北京时间渲染；业务策略只保存在管理员配置中。

最大输出 Token 支持留空（null），正常聊天此时不发送 max_tokens，沿用供应商默认生成额度；填写值时按管理员配置发送，不再设置代码中的 4096 默认值或 32768 上限。业务流程没有工具调用次数/轮数、批量数量或整轮时长配额。

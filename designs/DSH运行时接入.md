# DSH 对话与 Python 运行时

日期：2026-11-16。目标是用 DSH 替换现有对话执行循环及后续上下文压缩，保留 Galaxy 的身份、聊天记录和业务权限边界。

## 执行与上下文

- 线上通过 `GALAXY_AI_RUNTIME=dsh` 选择 DSH。不会在 DSH 失败后悄悄切回 LangGraph，也不会同时运行两套摘要循环。
- 原始聊天记录保留。旧会话首次进入 DSH 时，使用原有上下文准备逻辑迁移历史；过长历史只在迁移时生成摘要。之后由 DSH 的持久会话和 compaction 管理上下文。
- 每轮从上一份已提交的会话存档恢复，在独立临时副本内执行。新的存档编号随聊天记录提交，且绑定最后一条消息编号。失败或取消不覆盖之前已提交的存档；中断记录参与后续历史重建。
- 业务工具的实际完成结果仍进入 Galaxy 聊天记录和审计。运行时在操作后失败，也会保留已经完成的工具结果，避免用户重试时失去已发生操作的记录。
- 原 LangGraph 代码保留用于明确的运维回退，生产 DSH 对话不经过其执行循环。

## 运行隔离

固定版本：`deepseek-harness-sdk==0.2.2rc2`，匹配其 runtime wheel；使用 `sdk-minimal`，关闭会话日志向 DeepSeek 的额外上传、插件清单上传和图片读取工具。

`galaxy-dsh.service` 是固定的可信启动器，使用与主服务分离的资源控制组。它只启动 Bubblewrap 中的 DSH 和 Python，不在宿主机上执行模型提供的 shell。

每轮环境只提供只读系统运行库、只读 DSH/Python 安装、当前会话的 `/workspace` 与 `/state`，以及本轮专用的 Galaxy 网关 socket。没有宿主机应用目录、其他账号文件目录、管理员配置、SSH 密钥、真实模型密钥或运行平台控制 socket。

Bubblewrap 建立独立用户、进程、网络和挂载命名空间，移除 capabilities，禁止嵌套用户命名空间。DSH 自身的 `danger-full-access` 只作用于这一层外部隔离已经限定的可见文件系统，不能被当作宿主机安全控制。

当前资源配置：

| 项目 | 配置 |
| --- | --- |
| 同时运行 | 1 个 DSH 会话轮次；主服务最多接纳 8 个运行中/等待中的轮次 |
| 内存 | worker 控制组 768 MB，禁止 swap |
| CPU | 1 核额度 |
| 进程/线程 | 控制组最多 256 |
| 每轮时间 | 15 分钟；停止会关闭连接并清理进程 |
| 临时工作区 | worker 专用 192 MB tmpfs，另有 16 MB 临时目录 |
| 存档 | 每账号合计最多 128 MB；一份存档中工作文件最多 32 MB、会话日志最多 64 MB，单个普通文件最多 32 MB |
| 下载成果 | 单份最多 20 MB，每轮导出最多 64 MB，每账号导出源文件最多 128 MB；相同内容复用源副本 |
| Python 取上传文件 | 单份最多 32 MB，一轮合计最多 64 MB；大型 HAR 继续使用现有解析工具 |

当前会话内的工作文件可以延续。删除会话会清理对应的 DSH 存档、导出源文件、上传资料及已登记下载附件。存档、下载成果目录不通过静态文件服务暴露。

## 工具、上传与网络

- DSH 中的 `galaxy-cli` 是网关客户端，实际调用现有 `GalaxyDispatcher`。用户、工作区、会话和会话令牌由服务端固定，不从 Python 参数接收。
- 每次调用仍检查当前登录状态、命令权限、店铺范围和平台数据范围。排队后及运行期间继续检查登录状态；其他账号不能通过存档编号选择别人的环境。
- 飞书、有赞、时间、Excel、文件发布、按需上传和 HAR 工具沿用原入口。没有为每个业务接口新增独立工具。
- 上传入口仍先写明文件用途，上传成功后将真实回执交给 AI。`galaxy-files get FILE_ID` 只能取当前账号、当前对话中有效的上传文件。
- Python 生成的普通文件经描述符检查、大小与配额检查后，再调用原有 `file.publish`，显示现有客户端下载附件。符号链接、硬链接和特殊文件不能导出。
- 执行环境没有宿主机网络。HTTP/HTTPS 由当前轮次专用网关代理，解析并固定公网目标 IP；内网、回环、云元数据、IPv4 映射/过渡地址及当前应用服务器地址拒绝访问。端口仅 80/443，限制连接数、请求数和流量，重定向目标仍需重新检查。
- 现有模型设置为 DeepSeek 官方 `deepseek-flash`，优先走原生 Messages 协议，真实密钥只在宿主网关注入。客户端的思考开关与强度映射到原生参数。
- 模型网关只开放当前文本和本地工具协议，不开放共享模型账号的 Files、文档引用或服务端工具，避免运行代码借服务凭据读取该模型账号的其他资料。自定义 Chat Completions 地址使用受限的文本/工具协议桥接。

## 已完成的验证

新增测试覆盖真实 SDK/Python 执行、原有工具调用、旧上下文恢复、跨轮文件延续、上传卡片、上传后 HAR 解析、原生模型协议、密钥不进入执行 shell、同账号跨会话文件拒绝、跨账号存档拒绝、宿主文件不可见、链接导出拒绝、私网/元数据拒绝、磁盘限额、取消清理、会话删除清理。

真实模型自检使用合成账号与独立文件目录，不写生产聊天或业务数据：Python 算出 5050 并写入文件；时间工具成功；公网 HTTP 及跳转后的 HTTPS 成功；下一轮实际读回文件并回忆上一轮口令。没有创建成本同步或月度调度任务。

全量回归中，以下 3 个原有目录断言在未改动的线上代码上也失败，本次没有恢复已移除工具或修改这些断言：

- `test_catalog_tools.py::test_catalog_command_canonicalization_and_aliases[product-search-query-body0]`
- `test_catalog_tools.py::test_catalog_command_canonicalization_and_aliases[product-search-options-body1]`
- `test_youzan_tools.py::test_catalog_route_is_actual_help_and_admin_only`

它们涉及旧商品搜索别名和固定工具数量。其余检查单独执行；最终测试数量与部署备份位置见部署记录。

## 部署与回退

固定启动器和配置在 `deploy/dsh/`；服务端接入在 `backend/xingxu/dsh_runtime.py` 与 `ai.py`。专用 worker 不需要主后端安装 DSH SDK，不修改原有登录、飞书、有赞实现或客户端版本。

部署步骤包括源文件基线比对、独立验证、备份、worker 配置校验、生产运行时切换和健康检查。通过单独的 systemd drop-in 启用 DSH；回退时恢复备份中的服务端源文件并移除该 drop-in，再重启主服务。已有 DSH 原始聊天仍保留，不能通过回退重放已发生的业务写入。

参考：[DeepSeek Harness SDK](https://github.com/deepseek-ai/deepseek-harness/tree/master/python/sdk)、[官方安全说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/SAFETY.md)、[原生接口兼容说明](https://api-docs.deepseek.com/guides/anthropic_api/)。

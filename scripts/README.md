# 2026-11-16 开发及排查脚本

这里保存当天编写的原始部署、验证、HAR 采集和数据库排查脚本，供后续维护参考。它们原先位于被忽略的 `artifacts/`，当前应用源码以 `backend/`、`galaxy-cli/`、`desktop-client/`、`deploy/dsh/` 为准。

| 文件 | 用途 |
| --- | --- |
| `langgraph_remote.py`、`chat_progress_remote.py` | 当日上下文及等待状态功能的阶段部署与验证 |
| `upload_remote.py`、`har_reference_remote.py` | 按需上传及上传文件 HAR 分析功能验证 |
| `dsh_remote.py`、`dsh_*.py` | DSH 部署、隔离验证和运行状态检查 |
| `truncation_*.py` | 历史摘要截断修复、只读诊断及验证 |
| `update-installed-*.ps1` | 备份并更新本机客户端 |
| `collect_cost_har.py`、`validate_task_har_local.py` | 本地指定抓包采集及样本验证 |
| `mami_gjp_*.py` | 现有打通系统协议、配置及成本读取调查 |
| `gjp_*.sql`、`gjp_readonly.ps1` | 库存、机构和成本候选口径的只读查询 |
| `silverlight_*.py` | 对本地已有程序集做静态分析，不执行目标程序集 |

这些是当日维护脚本，并非通用一键部署入口。运行前应阅读脚本模式和固定路径；部分模式会更新服务或本机应用，`python_remote.py` 是 DSH 采用前保留的阶段方案。新环境按 `deploy/` 文档准备，不直接依次执行所有脚本。

SSH 密码从本机指定的桌面文件读取，数据库连接同样在运行时读取，仓库不含凭据。SSH 使用已有 `known_hosts` 校验。路径、主机及阶段验证目录需要按环境核对。会生成本地状态或报告的脚本使用被忽略的 `artifacts/`；其中的备份、状态、抓包和程序集需另行准备，不会随仓库上传。静态分析需 dnfile/dncil，SSH 脚本需 Paramiko，相关数据库脚本另需 pymysql。

SQL 查询执行前必须填写 `@BranchPrefix`；样本对照查询还需填写 `@SampleCodes`，默认值会主动终止查询。机构编号、真实商品条码及实测结果未发布。`gjp_readonly.ps1` 的 ApplicationIntent 不代替数据库权限，连接时使用只读账户。

`truncation_summary_smoke.py` 使用环境变量 `GALAXY_SMOKE_CHAT_ID` 指定待验证会话，并只在临时目录创建 AI 服务对象；不要在生产私有目录以 root 创建该对象。`truncation_diagnose.py` 同样按这个变量筛选会话。`mami_gjp_read_probe.py` 使用 `GALAXY_SAMPLE_BARCODE` 指定查询样本。

HAR 本地验证保留当日样本的条目数和成本字段断言；其他抓包应调整断言或使用 CLI 的通用 `task.har` 工具。采集器生成的文件仍可能含有业务内容，应保存在本地 `artifacts/`。

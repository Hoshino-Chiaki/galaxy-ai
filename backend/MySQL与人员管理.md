# MySQL 与人员管理

## 运行方式

MySQL Community Server 8.4 LTS 安装在现有服务器，仅监听 `127.0.0.1:3306`。
后端通过 `/etc/galaxy-ai/database.json` 读取独立应用账号，文件仅 root 和 galaxy-ai 服务组可读。
应用数据库账号只有 `galaxy_ai` 库内的 SELECT、INSERT、UPDATE、DELETE 权限。
正式目录的存储工厂固定使用 MySQL，不会在连接失败时回退写 JSON。
本地 JSON 实现仅保留给离线单元测试使用。

## 表结构

- 人员：`employees`。
- 账号鉴权：`users`、`sessions`、`login_attempts`。
- 权限：`roles`、`permissions`、`user_roles`、`role_permissions`。现有管理员/员工权限已原样导入；每次鉴权从数据库读取授权。
- AI：`ai_providers`、`ai_settings`。
- 对话：`chat_conversations`。
- 有赞：`youzan_connections`。
- 飞书：`feishu_apps`、`feishu_connections`。
- 文件：`file_assets`、`upload_requests`。
- 工具：`tool_prompts`、`tool_audit`。
- 审计：`employee_audit`；记录人员操作人、操作类型及变动字段名，不记录密码内容。

账号、会话和员工表使用结构化列；平台凭证、聊天上下文等复杂记录在各自的 MySQL 表中保留原 JSON 结构。
现有模块使用事务兼容适配器，每个模块的修改由数据库锁串行保护，按变化的记录写入。
上传附件、生成文件和 DSH 运行文件仍放在服务器文件目录，数据库保存其元数据与关联关系。
加密密钥仍是原有 `private/ai-secret.key`，已加密的平台凭证不解密重写。

## 员工字段

| 页面字段 | 存储与行为 |
|---|---|
| 姓名 | `employees.name`，原账号无姓名时显示“未填写”，不猜测真实姓名 |
| 账户 | `users.username`，Galaxy AI 登录名，全局唯一 |
| 头像 | `employees.avatar`，用户选择图片后缩成 128×128 的 WebP；服务端只接受限长的 PNG/JPEG/WebP 数据 |
| 部门 | `employees.department` |
| 岗位 | `employees.job_title` |
| 手机 | `employees.phone` |
| 在职状态 | `employees.employment_status`；离职会同步停用登录账号、使已有会话失效 |
| 关联账号 | 按人员账号 ID 读取其有赞/飞书绑定，只显示平台和账号标识，不回传凭证 |
| 密码 | `users.password_hash` 保存 Argon2id 哈希；不返回原值；编辑时留空保留原密码 |

内部 ID、工作区、账号外键和创建/更新时间用于约束与审计，不增加到员工表单中。
新增员工默认使用员工角色；角色与权限独立保存，不放进员工档案字段。

## 权限边界

人员列表、新增、编辑都要求 `users:manage` 权限。
服务端按当前登录账号的工作区查询和更新，不能靠传入其他工作区或人员 ID 越权。
不能将当前登录账号或最后一个在职管理员设为离职。
密码修改或离职会递增账号授权版本，旧会话在下一次访问时被拒绝。
数据库不是直接开放给 AI 的工具，模型仍走 Galaxy CLI 的原有鉴权边界。

## 备份和恢复

迁移前完整 JSON 数据、加密密钥及旧运行代码位于服务器 root 私有备份目录。
迁移工具对 9 类存储逐项比对，确认内容一致后才启用 MySQL。
每天 03:30 的 `galaxy-ai-db-backup.timer` 备份数据库及加密密钥；文件位于 `/var/backups/galaxy-ai`，仅 root 可读，默认保留最近 7 份。
可通过 root 私有的 `/etc/galaxy-ai/database-backup.json` 中 `keep` 参数调整保留份数。
恢复数据库和密钥必须作为一套处理。上线后的新增数据应从 MySQL 备份恢复，旧 JSON 只作为迁移前备份保留。

## 验证

独立测试库已验证事务回滚、并发修改、哈希密码、人员新增/编辑、密码留空保留、权限即时读取、员工访问拒绝、跨工作区隔离和离职会话失效。
客户端构建和 Windows 页面另行验证。原有有赞登录脚本没有修改。

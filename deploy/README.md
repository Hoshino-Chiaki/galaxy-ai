# 0.7.1 授权界面简化

同步 backend/xingxu/feishu/service.py、routes.py 和 galaxy-cli/src/galaxy_cli/feishu_capabilities.py；不需要重建 Go CLI，不变更个人凭证数据。新客户端移除飞书范围选择器和有赞店铺选择板块，统一授权请求覆盖当前全部已接入用户能力。旧客户端 group 参数仅兼容接收，不再限制范围。

# Galaxy AI 服务器部署

服务地址：`https://124.222.51.137`，使用 TCP 443。Nginx 将应用请求转给仅监听本机的 `127.0.0.1:18443`；不需要对外开放内部端口。原有 HTTP 站点及其他域名 HTTPS 站点独立保留。

项目目录 `/opt/galaxy-ai`。服务为 `galaxy-ai.service`，以专用系统用户运行并开机启动。2026-11-23 已在当前服务器启用账号文件存储：MySQL 保存身份、登录会话、权限和企业配置；个人聊天、渠道凭证、任务与文件统一位于 `backend/private/accounts/<用户名>/`。具体目录、切换与回退步骤见 [账号文件存储实施记录](../designs/账号文件存储实施记录-20261123.md)。下文旧路径描述属于历史部署方式。不要把 private 内容加入安装包或下载目录。备份需包含 MySQL、加密密钥和账号目录；手工共享文件仍位于 `hosted-files`。

客户端只包含服务器地址与用于校验 HTTPS 的公开证书，AI Key 和有赞账号密码都从服务器端使用。换电脑安装后使用原 Galaxy AI 账号登录即可；更换服务器地址后需要重新登录。

此 IP 入口使用客户端内置证书校验，未关闭 TLS 验证。证书私钥仅位于服务器 `/etc/nginx/galaxy-ai`；更换证书时应同步更新客户端内置公开证书。

管理命令：`systemctl status galaxy-ai`、`journalctl -u galaxy-ai`。Nginx 配置见本目录模板。

文件下载：`/files/<文件名>`。自动生成文件带 UUID 前缀防止同名覆盖，并校验登录用户、工作区与对话归属；手动放入 hosted-files 的普通文件对已登录用户共享。隐藏文件、符号链接、路径跳转和未完成的附件不会公开。原按文件 ID 的接口继续兼容。

Windows 安装程序由 electron-builder NSIS 生成，可选择安装位置，保留现有用户数据；安装包不包含后端私有配置。安装包可放到服务器 `releases` 目录，通过 `/downloads/<安装包文件名>` 下载。

有赞现有配置、加密密码、SID、CSRF 及业务 token 已迁移，云端报表读取已实测成功。2026-11-12 已恢复原有固定登录程序和运行依赖，生产接入方式见下文。普通接口和基于当前有效会话的操作不启动登录程序。

文件工具采用两步流程：`excel.create` 返回 `path/filename/size/sheets`，`file.publish` 接受 `{"path":"相对路径"}`，返回 `file_id/filename/size/media_type/download_path`。文件存储模式下，工作区位于 `backend/private/accounts/<用户名>/workspace/`，发布副本位于该账号的 `files/published/`；DSH 的会话文件和检查点位于 `tasks/dsh/`。路径仅由服务端确定，模型不能指定账号目录。生成文件保留在工作区，发布时复制到对应发布目录，无须刷新服务。同一对话中重复发布同一路径且内容未变时返回已有附件。

文件操作拒绝绝对路径、目录跳转、编码路径、隐藏路径、符号链接、硬链接和特殊文件，按目录句柄逐层打开。发布前后均检查当前登录权限、源文件与托管副本，执行期间撤销权限会取消登记并清理本次副本。`excel:export`、`file:publish`、`file:read` 分别控制生成、托管和下载；附件只有在所属对话成功保存后才可下载，下载还需匹配用户、工作区与对话。文件类型不限于 Excel，客户端使用原生保存对话框，不自动执行下载文件。

后端已拆分为 `xingxu/browser/` 公共层与 `xingxu/youzan/` 有赞业务包，结构和兼容说明见 [后端模块结构](../designs/后端模块结构.md)。部署时必须同步新包及公共模块，并移除旧的 `xingxu/youzan.py`；不要只拷贝单个旧服务文件。现有私有 JSON 和加密密钥不迁移、不覆盖。

0.6.0 新增飞书：同步 `backend/xingxu/feishu/`、`galaxy-cli/src/galaxy_cli/tools/feishu/`、`galaxy-cli/src/galaxy_cli/feishu_client.py` 和 `backend/feishu/`。CLI 二进制仅在服务端，App Secret 和个人 OAuth 加密存于现有 private 下的 feishu.json，不放进安装包；不开放新端口。员工可配置个人有赞连接与飞书授权，AI 设置与飞书企业应用密钥仅管理员可改。

## 仅服务器运行（2026-11-10）

Arch 本地的服务注册、旧启动脚本、后端与 CLI 虚拟环境、飞书运行二进制及旧浏览器运行依赖已退役。不要复制原 `backend/xingxu.service` 或重新安装本地 Galaxy CLI。新的 `deploy/setup-server.sh` 仅在 `/opt/galaxy-ai` 中执行；应用默认工厂及账号导入入口也检查服务器部署目录。Windows 客户端地址和公开证书不变，安装包无须因本次清理而更新。

开发在本地编辑源码并构建 Windows 客户端；后端部署、CLI 执行、集成验证与运行数据均在服务器上进行。离线测试需要显式临时数据目录，不允许回退到本地 `backend/private`。历史本地数据保存在项目外受限备份中，不再作为运行数据使用。
# 飞书 0.7.0 部署补充

只同步飞书模块、Galaxy 工具网关和角色权限的小范围改动，避免夹带暂停的商品功能。飞书 Go wrapper 升级为固定 commit ac22a60a23d66c37d43a7ec6d9e99b99a4320356，main.go、workspace.go、allowed-commands.json 与 galaxy_cli/feishu_catalog.json 必须保持一致。需要 Go 1.25+；仅在服务器构建业务 CLI。

飞书个人配置数据保持原样，grants 按业务范围附加存储，旧 token 兼容；无须修改现有 App ID、Secret 或全局提示词。客户端 0.7.0 增加授权范围选择，0.6.0 仍能调用原工具和新目录，但没有范围选择界面。


## 恢复原有赞登录程序（2026-11-12）

> 后续已于 2026-11-23 上线 SDK 就绪等待与保存后自动获取 SID、CRM、授权店铺。当前生产主脚本摘要为 `77cc550f73da187eb56c08c7e4c788b1baa003378e85d08f3f9935d15d96413d`；下文是 11 月 12 日恢复原版时的历史记录。当前行为、验证限制和回退位置见 [有赞自动连接上线记录](../designs/有赞SDK就绪与自动连接上线记录-20261123.md)。

生产适配器 `backend/xingxu/youzan/login.py` 直接运行原 `session_research/youzan-login.cjs --managed-stdin`，不再使用遇到滑块即退出的替代实现。`youzan-login.sh` 和 `session_research/youzan-login-runtime.json` 同步保留。三个文件与原文件逐字节一致；主脚本 SHA-256 为 `63004226569347014c32cdf95642a72caf0e54931bf0322563e4a3fab4fbc5e3`。

原脚本使用 `/usr/sbin/chromium`、有界面模式、1280×900；服务器安装 Debian Chromium 156.0.8078.79，并以 `/usr/sbin/chromium` 链接到 `/usr/bin/chromium`。不通过共享的 `backend/browser/launch.cjs` 改写语言、时区或浏览器版本。Playwright Core 固定 1.63.0，`session_research/runtime/node_modules` 链接到已有的 `backend/browser/node_modules`。

Python 位于 `investigation/youzan-slider/.venv`，依赖按 `session_research/requirements-login.lock.txt` 恢复，两个上游源码 SHA-256 继续由原脚本检查。ddddocr 保持 1.5.6；该旧库元数据限制 Python <3.13，本次与历史环境一样仅在隔离环境忽略该元数据限制，并通过原成功图片的坐标识别、72 组轨迹及加密检查。不替换为返回格式不同的新版本。

原成功浏览器目录在确认账号与服务器连接一致后，迁入该连接的 `backend/private/youzan-profiles/<连接ID>/<profile_revision>`。以后每个用户仍使用自己的独立目录。服务通过私有 stdin 传入当前用户的账号、密码及目录；HOME 和 XDG 写目录由适配器指向该目录。原脚本的托管模式不读研究账号文件，不写全局 Cookie 或报告，也不将 SID 回显给客户端。

历史成功记录证明 2026-11-08 有一次 `login_code=0` 且未要求短信；是否需要短信仍以本次有赞响应为准，不能把该历史结果视为每次登录的保证。验证记录位于 `artifacts/youzan-login-restored-live-20261112.json`。旧脚本、旧服务器浏览器目录和登录前的加密连接数据保存在服务器 `/var/backups/galaxy-ai/youzan-login-restore-20261112/`，仅 root 可访问。不要将该备份目录放到下载目录或源码包中。

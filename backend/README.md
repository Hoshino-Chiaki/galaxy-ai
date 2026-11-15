# Galaxy AI 服务端

生产后端只在远程服务器 **124.222.51.137** 的 `/opt/galaxy-ai` 运行。Arch 本地仅保留源码和 Windows 客户端构建文件，没有本地后端、CLI、预览服务或开机启动流程。

- 客户端固定访问 `https://124.222.51.137`，公开证书位于 `desktop-client/electron/certs/server.crt`。
- 服务端由 `galaxy-ai.service` 管理；配置见 [服务器部署](../deploy/README.md)。
- 账号、聊天、AI 密钥、有赞及飞书凭证只从服务器 `backend/private` 使用，附件在服务器 `hosted-files`。
- 本地的 `run.sh`、`setup.sh`、`xingxu.service` 及全局 Galaxy CLI 命令安装入口已移除。
- 无参 `create_app()` 只能在服务器安装目录、明确配置服务器数据目录后启动，不能从本地检出目录生成运行数据或启动服务。
- 若需离线单元测试，可向工厂显式传入临时目录；这不是本地生产运行入口。当前本地专用虚拟环境已退役，测试与运行在服务器准备环境。

## 服务器管理

```bash
ssh root@124.222.51.137 'systemctl status galaxy-ai.service --no-pager'
ssh root@124.222.51.137 'curl -fsS http://127.0.0.1:18443/health'
```

服务器初始化脚本是 `deploy/setup-server.sh`，仅允许在 `/opt/galaxy-ai` 内执行，不向用户 HOME 安装 CLI 链接。既有服务器无须重新初始化或覆盖私有配置。

导入账号也只在服务器执行，读取服务器上的凭证文件：

```bash
cd /opt/galaxy-ai/backend
XINGXU_DATA_DIR=/opt/galaxy-ai/backend/private .venv/bin/python -m xingxu.bootstrap --credentials /服务器上的账号文件
```

公开 API 和现有功能没有改变：登录与会话、用户权限、AI 聊天及打断、分类提示词、有赞多店工具、员工飞书 OAuth、文件生成和发布均由服务器处理。AI 设置与企业应用密钥仅管理员可改，员工只管理自己的连接。结构见 [后端模块结构](../designs/后端模块结构.md)。

项目内的工具权限、路径范围、私有 stdin 与服务进程保护只作用于本项目的服务器请求及其子进程，不设置电脑其他 AI 的会话沙箱策略，也没有全局禁止套接字的设置。

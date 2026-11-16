# Galaxy AI

Galaxy AI 的桌面客户端、服务端、业务 CLI 和部署源码，便于跨电脑继续开发。当前客户端版本为 `0.7.6`。

## 目录

| 目录 | 内容 |
| --- | --- |
| `desktop-client/` | Electron、React、TypeScript、Vite Windows 客户端 |
| `backend/` | Python API、账号权限、有赞与飞书接入、工具调度、测试 |
| `galaxy-cli/` | 业务工具源码及测试 |
| `deploy/` | systemd、Nginx 和服务器部署说明 |
| `session_research/` | 已恢复的原有赞登录程序、适配器和依赖版本记录 |
| `investigation/` | 登录程序使用的固定上游源码及许可证 |
| `designs/` | 功能设计、权限及模块说明 |
| `scripts/` | 当日部署、验证和成本取数调查脚本，不含实际业务数据 |

## 在其他电脑继续开发

```bash
git clone git@github.com:Hoshino-Chiaki/galaxy-ai.git
cd galaxy-ai
git switch -c feature/my-change
```

客户端安装依赖和构建：

```bash
cd desktop-client
npm ci
npm run build
```

Windows 安装包构建见 [客户端说明](desktop-client/README.md)。客户端连接现有远程服务器，后端和业务 CLI 在服务器运行；本地检出目录用于编辑与构建，不自动启动后端。

提交修改：

```bash
git add <修改的源码路径>
git commit -m "Describe the change"
git push -u origin HEAD
```

## 服务器与部署

当前服务器为 `124.222.51.137`，项目在 `/opt/galaxy-ai`，服务为 `galaxy-ai.service`。上传 GitHub 不会自动部署。

按 [部署说明](deploy/README.md) 同步需要变更的源码，保留服务器现有 `backend/private`、浏览器目录和 `hosted-files`。账号密码、AI Key、飞书密钥、有赞 SID/CSRF、聊天记录、浏览器状态、附件和安装包均不在本仓库中。

有赞登录使用原 `session_research/youzan-login.cjs`、`youzan-login.sh` 和 `youzan-login-runtime.json`，保留固定源码与依赖版本；服务器运行依赖需单独安装，见部署说明。`desktop-client/electron/certs/server.crt` 是客户端校验 HTTPS 所需的公开证书。

后端测试依赖见 `backend/requirements-dev.txt`，CLI 依赖见 `galaxy-cli/pyproject.toml`。单元测试使用临时数据目录；`verify-*-live.py` 等真实联调脚本需要另行提供私有测试配置及账号文件。

这次源码整理的范围及与线上版本的差异见 [源码快照说明](designs/源码快照-20261116.md)。

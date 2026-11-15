# Galaxy AI · Windows 客户端

Windows 客户端使用 Electron、React、TypeScript 和 Vite，只连接远程服务器 `https://124.222.51.137`。Arch 本地保留源码和 Windows 安装包构建能力；本地后端、CLI、网页预览服务与启动命令已移除。

当前版本 0.6.0：深色聊天界面、可展开思考内容、真实工具调用记录、文件下载、停止回复。管理员有独立 AI 设置；员工在控制台管理自己的有赞连接和飞书授权。具体功能见 [飞书模块与控制台权限](../designs/飞书模块与控制台权限.md)。

账号、聊天、AI Key 和有赞/飞书凭证均由服务器处理。客户端只有固定服务器地址、HTTPS 公有证书和个人 Windows 加密登录会话，不携带服务端配置或服务器私钥。

## 编辑与构建

在本地仅编辑并构建，不启动 Linux Electron、Vite 开发或预览服务器：

```bash
cd desktop-client
npm ci
npm run build
npm run package:win
```

安装包使用 Windows 构建环境执行 `electron-builder --win nsis`，保留当前 NSIS 配置和用户数据目录。GUI 验证在 Windows 客户端完成，连接真实远程后端；本地旧界面模拟服务器已退役。

客户端入口地址在 `electron/backend.json`，公有证书在 `electron/certs/server.crt`。修改服务器入口或证书后才需重新打包；本次后端本地环境清理不影响已安装的 0.6.0 客户端。

服务器初始化、服务管理和日志检查见 [服务器部署](../deploy/README.md)，均通过 SSH 在服务器执行。

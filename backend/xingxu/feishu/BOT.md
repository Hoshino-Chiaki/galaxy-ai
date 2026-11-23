# 飞书机器人入口

机器人直接使用现有 Galaxy AI 的模型、对话及 galaxy-cli，不调用 Aily 或豆包模型。使用管理员已有的飞书自建应用，员工仍在「我的账号 → 飞书」完成自己的 OAuth 授权。

平台设置：开启机器人能力；事件与回调选择「使用长连接接收事件」，订阅 `im.message.receive_v1`；应用身份开通 `im:message.p2p_msg:readonly` 与 `im:message:send_as_bot` 并发布。无需开放新端口、请求 URL、Verification Token 或 Encrypt Key。

`bot_worker.py` 仅运行官方 lark-oapi 1.8.0 WebSocket SDK。SDK 独立安装在 `backend/feishu/bot-venv`，依赖见 `backend/feishu/bot-requirements.txt`，避免降级后台已有的 websockets。App Secret 经私有 stdin 管道传入，不放在命令参数、环境变量或日志中。SDK 日志关闭；连接器作为当前后台的子进程启动和退出，不注册额外服务。

`bot.py` 持久接收消息后才 ACK；以 message_id 去重，只处理用户文字私聊。根据当前应用 revision、tenant_key、OAuth 验证的 open_id 查找唯一启用的 Galaxy 账号；无绑定或多账号冲突时拒绝进入 AI，不按昵称、手机号或管理员身份兜底。

入口需要 `ai:chat` 与 `feishu:chat`；所有工具继续经过 GalaxyDispatcher 的实时权限与个人凭证校验。`channel_identity.py` 为微信、飞书入口共用的身份校验与临时执行会话。退出本轮后注销会话。飞书断开 OAuth 会停止该员工正在处理的机器人消息。

私聊使用独立对话，并按员工、飞书用户、chat_id 隔离。上下文沿用服务器对话；回复仅发送最终正文。账号授权或绑定在处理中变化时不发送旧结果。消息及对话索引保存在加密的 `private/feishu-bot/<应用作用域>/inbox.json`；重启不重放已开始的业务操作。

官方资料：https://github.com/larksuite/oapi-sdk-python ，https://open.feishu.cn/document/server-docs/im-v1/message/events/receive 。

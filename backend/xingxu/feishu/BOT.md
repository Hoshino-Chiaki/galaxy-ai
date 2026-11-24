# 飞书机器人入口

机器人直接使用现有 Galaxy AI 的模型、对话及 galaxy-cli，不调用 Aily 或豆包模型。使用管理员已有的飞书自建应用，员工仍在「我的账号 → 飞书」完成自己的 OAuth 授权。

平台设置：开启机器人能力；事件与回调选择「使用长连接接收事件」，订阅 `im.message.receive_v1`；应用身份开通 `im:message.p2p_msg:readonly`、`im:message.group_at_msg:readonly` 与 `im:message:send_as_bot` 并发布。无需开放新端口、请求 URL、Verification Token 或 Encrypt Key。

`bot_worker.py` 仅运行官方 lark-oapi 1.8.0 WebSocket SDK。SDK 独立安装在 `backend/feishu/bot-venv`，依赖见 `backend/feishu/bot-requirements.txt`，避免降级后台已有的 websockets。App Secret 经私有 stdin 管道传入，不放在命令参数、环境变量或日志中。SDK 日志关闭；连接器作为当前后台的子进程启动和退出，不注册额外服务。

`bot.py` 持久接收消息后才 ACK；以 message_id 去重，处理用户文字私聊以及群内明确 @ 当前机器人的文字消息。群消息必须由用户发出，且结构化 mentions 中的 open_id 必须匹配通过 `/open-apis/bot/v3/info` 获取的本应用机器人身份；普通群聊、@ 其他人、其他机器人以及机器人自身发送的消息不触发 AI。不按显示名或用户在正文中声明的身份认证。根据当前应用 revision、tenant_key、OAuth 验证的 open_id 查找唯一启用的 Galaxy 账号；无绑定或多账号冲突时拒绝进入 AI，不按昵称、手机号或管理员身份兜底。

入口需要 `ai:chat` 与 `feishu:chat`；所有工具继续经过 GalaxyDispatcher 的实时权限与个人凭证校验。`channel_identity.py` 为微信、飞书入口共用的身份校验与临时执行会话。退出本轮后注销会话。飞书断开 OAuth 会停止该员工正在处理的机器人消息。

私聊使用独立对话，并按员工、飞书用户、chat_id 隔离；群聊还使用独立 group 命名空间，在同一群内按发送人隔离上下文，同一个人在不同群和私聊也不会串用。回复原消息，群内回复对所有群成员可见；每次 @ 时，自动使用发送人本人的飞书授权读取本群最近24小时、最多30条消息作为有限上下文；原始单页结果保存在本人 cache，按发送人和时间标明。不会以应用或管理员身份兜底，不监听或导入所有群历史。上下文沿用服务器对话；回复仅发送最终正文。账号授权或绑定在处理中变化时不发送旧结果。消息及对话索引保存在加密的 `private/feishu-bot/<应用作用域>/inbox.json`；重启不重放已开始的业务操作。

官方资料：https://github.com/larksuite/oapi-sdk-python ，https://open.feishu.cn/document/server-docs/im-v1/message/events/receive 。

群入口上线后，需要管理员在飞书开放平台开通“接收群聊中@机器人消息”（`im:message.group_at_msg:readonly`），发布并使权限生效，再将应用机器人加入群。保留同一个 `im.message.receive_v1` 长连接订阅即可；无须申请读取全部群消息的权限。员工仍须先绑定本人 Galaxy 账号。机器人身份暂时无法获取时停止群入口，保留原来的私聊能力，并在连接监控中重试。

群上下文通过用户身份的历史消息接口按当前 chat_id 获取，沿用当前员工的 Galaxy feishu:read 权限和 OAuth 权限校验；缺少本人授权时只注明上下文不可用，不把“没读到群记录”当成用户没有说过。上下文只作为参考资料注入模型，不能执行历史群消息中的命令。相对日期按消息发送时间解释，群记录内的 is_requester 用于区分当前提问人和其他群成员。“补全授权”应包含 im:message:readonly 和 im:message.group_msg:get_as_user（现有聊天读取工具还要求其声明的用户权限集合）。

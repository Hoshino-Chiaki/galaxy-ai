# ClawBot

此模块使用腾讯官方 openclaw-weixin 2.4.10 的 iLink 协议，接入现有 Galaxy AI，不另行运行 OpenClaw 网关。

- 客户端「我的账号 → ClawBot」生成二维码，微信扫码确认；上游要求配对码时由用户手动填写。
- 每个 Galaxy 员工在自己的 `channels/clawbot/connection.json` 保存一份加密连接，公共接口不返回 bot token、context token 或消息游标。
- 只接收扫码确认者的文字私聊。群消息、其他发送者和没有 context token 的消息不会进入 AI。同一 bot 不能绑定给两个 Galaxy 员工。
- 调用现有 AiService，使用绑定员工当前权限及个人渠道会话；每轮使用临时服务端会话并在结束时注销。密码变更要求重新绑定。
- 私聊拥有独立的会话，持续上下文。消息 ID 在执行前落盘去重，重启不自动重放业务操作；回复失败不自动重新执行模型或工具。
- 解除绑定停止当前收发并删除本地凭证。此操作不声称撤销微信平台授权；微信侧解绑仍以手机设置为准。
- `clawbot.send` 可向本人绑定的微信发送文字或工作目录中的文件，也可由定时任务按 instruction 调用；发送权限为 `clawbot:send`，文件另需 `file:read`。每次发送均重新校验身份、权限和绑定；request_key 保存投递回执，未知结果不盲目重发。文件依照官方协议加密上传腾讯 CDN 后发送附件，不暴露服务端路径或凭证。
- 微信消息上下文有平台限制，缺少或失效时如实返回未完成，不承诺脱离平台限制的永久主动投递。
- 仍不支持普通微信群及图片、语音、文件的入站解析。

协议来源：https://github.com/Tencent/openclaw-weixin 。原项目采用 MIT 许可证。

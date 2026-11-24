# 聚水潭供应链渠道

当前渠道对应 `gyl.scm121.com` 的供应链订单，保留 `backend/jushuitan/` 的原有 ERP 登录、库存独立脚本。

- `routes.py`：本人配置和登录入口。省略密码复用当前同账号已保存密码，明确传空或 null 表示空密码。
- `service.py`：按官方登录页面流程获取临时加密参数、提交账号密码，保存平台签发的身份和令牌。安全验证或登录失败不覆盖原连接，不自动重试。
- `tools.py`：固定订单查询参数规范化。工具进程只接收本人 access token，不接收密码、refresh token 或可选身份。
- 生产连接保存在 `accounts/<账号>/channels/jushuitan/connection.json`，凭证由服务器密钥加密。查询结果走统一缓存，完整 JSON 位于 `accounts/<账号>/cache/jushuitan/order-search/`。
- 权限 `jushuitan:order:read` 控制目录可见和每次调用；配置权限由实际渠道能力派生，不将平台角色转换成 Galaxy 管理员权限。
- 工具源码与输入输出说明：`galaxy-cli/src/galaxy_cli/tools/jushuitan/order_search.py`。计数请求 searchType=2，明细请求 searchType=1；完整响应分别保留在 count 和 query，列表 total=-1 不能当零订单。

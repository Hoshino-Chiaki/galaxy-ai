# 聚水潭供应链渠道

当前渠道对应 `gyl.scm121.com` 的供应链订单，保留 `backend/jushuitan/` 的原有 ERP 登录、库存独立脚本。

- `routes.py`：本人配置和登录入口。省略密码复用当前同账号已保存密码，明确传空或 null 表示空密码。
- `service.py`：使用账号中心 `https://account.scm121.com` 获取临时加密参数、提交账号密码；appcode 为 `account.scm121.com`，Source 为 `ACCOUNT`，请求体携带 `fromBu=jst-gyl-pc`。每次无验证码登录从空 Cookie 状态开始，仅接收本次请求新签发的 Cookie，不加载历史浏览器数据或指纹。安全验证或登录失败不覆盖原连接，不自动重试。
- `tools.py`：固定订单查询参数规范化。工具进程只接收本人 access token，不接收密码、refresh token 或可选身份。
- 生产连接保存在 `accounts/<账号>/channels/jushuitan/connection.json`，凭证由服务器密钥加密。查询结果走统一缓存，完整 JSON 位于 `accounts/<账号>/cache/jushuitan/order-search/`。
- 权限 `jushuitan:order:read` 控制目录可见和每次调用；配置权限由实际渠道能力派生，不将平台角色转换成 Galaxy 管理员权限。
- 工具源码与输入输出说明：`galaxy-cli/src/galaxy_cli/tools/jushuitan/order_search.py`。计数请求 searchType=2，明细请求 searchType=1；完整响应分别保留在 count 和 query，列表 total=-1 不能当零订单。
- 平台要求图形验证码时，登录响应返回 `captcha_required` 和图片；员工填写后提交 `challenge_id`、`verify_code`。刷新接口 `POST /api/v1/jushuitan/captcha` 只接收账号。平台 preToken 和当次 Cookie 只存在服务端内存，绑定员工、渠道账号，五分钟过期、提交即消费；刷新会使旧图失效，不保存或自动识别答案。

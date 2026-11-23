# 聚水潭账号登录与商品库存接口

本目录固定了 2026-11-21 实际验证成功的流程：账号密码登录 → 新 Cookie 核验身份 → 保存会话 → 查询商品库存。

`client.py` 只使用 Python 标准库，建议在 Linux、Python 3.10+ 下执行。它是可独立调用的脚本，目前没有增加 Galaxy 页面、注册 galaxy-cli 工具或部署后台服务，也没有修改有赞登录程序。

## 当前本机直接使用

在项目根目录执行：

```bash
cd /home/chiaki/有赞调查/galaxy-ai-current

# 账号、密码和设备信息已从本次指定的 HAR 准备到私有配置。
# 重新登录：先验证用户/公司，再原子替换保存的会话。
python3 backend/jushuitan/client.py login

# 检查已保存会话是否仍然可用；不重新登录。
python3 backend/jushuitan/client.py identity

# 当前筛选范围的商品总数。
python3 backend/jushuitan/client.py inventory --action count

# 第一页商品库存，完整业务 JSON 输出到终端；仅隐藏会话凭证。
python3 backend/jushuitan/client.py inventory --page 1 --page-size 50

# 保存完整第一页结果，不在终端展开明细。
python3 backend/jushuitan/client.py inventory --page 1 --page-size 50 \
  --output backend/jushuitan/private/default/inventory-page-1.json
```

默认私有目录是 `backend/jushuitan/private/default/`：

| 文件 | 内容 |
|---|---|
| `config.json` | 账号、密码、设备标识、首次登录浏览器状态、固定库存查询模板 |
| `session.json` | 最新通过身份核验的 Cookie、用户 ID、公司 ID、设备信息和到期元数据 |
| `inventory-page-1.json` | 本次只读测试保存的第一页库存业务数据 |

私有目录已被 Git 忽略，文件权限为 `0600`，目录为 `0700`。**当前独立脚本的私有 JSON 是明文文件，不是加密存储，不要上传到代码仓库或发给 AI。** 未来接入 Galaxy 后，应由服务端加密保存到当前员工的渠道连接记录，脚本的 stdout 不能包含 Cookie/密码。

密码重置后，在本机私有 `config.json` 中更新 `password`，再运行 `login`；不要将密码直接放进终端参数。账户变更应使用新的私有目录，不混用已有会话。

## 新账号或迁移到服务器

将代码及对应账号的私有配置安全放到运行环境即可；初始化以后不需要每次携带 HAR。

另一个账号使用独立目录，`--profile` 放在子命令前：

```bash
python3 backend/jushuitan/client.py --profile /secure/jushuitan/employee-a prepare \
  --login-har /secure/captures/login.har \
  --inventory-har /secure/captures/inventory.har

python3 backend/jushuitan/client.py --profile /secure/jushuitan/employee-a login
python3 backend/jushuitan/client.py --profile /secure/jushuitan/employee-a inventory --action count
```

本次初始化的来源是：`~/Downloads/www.erp321.com1.har` 和 `~/Downloads/www.erp321.com3.har`。`prepare` 只读取指定 HAR，不联网、不登录；同名配置已经存在时拒绝覆盖。

## 登录接口

```http
POST https://api.erp321.com/erp/webapi/UserApi/WebLogin/Passport
Content-Type: application/json; charset=utf-8
Origin: https://www.erp321.com
Referer: https://www.erp321.com/
Cookie: 浏览器初始状态或上一次核验成功的会话
```

```json
{
  "data": {
    "account": "账号",
    "password": "密码",
    "j_d_3": "设备标识",
    "v_d_144": "客户端标识",
    "isApp": false
  }
}
```

- `j_d_3` 对应页面的 `window.jdDeviceId`。
- `v_d_144` 对应页面的 `window.vipMarketClentId`。
- 必须同时检查 HTTP 和 JSON 业务结果。`code=0` 后继续检查 `data.hasRisk`、`data.idaasHasRisk`，不能只看 HTTP 200。
- 会话由响应 `Set-Cookie` 及 JSON 的 `cookie` 字段签发。脚本使用 CookieJar 接收 Set-Cookie，并核对 `u_json`、`u_cid`、`u_sso_token` 与 JSON 一致。
- 用户、公司 ID 从本次登录返回的 `u_id`、`u_co_id` 获取，不让模型填写身份。
- 本账号登录返回“密码需要重置”的提示，但实际身份和库存查询都已成功。脚本保留此提示，不会替用户修改密码。
- 额外安全验证、网络失败、业务失败或身份不匹配时，不覆盖已有 `session.json`，不自动重试登录，也不处理验证挑战。

身份核验接口：

```http
POST https://api.erp321.com/erp/webapi/UserApi/Passport/GetUserInfo
```

```json
{"data": {}, "uid": 100, "coid": 200}
```

上面的数字仅是格式示例，实际使用本次会话的 ID。返回 `data.uid`、`data.coId` 必须与本次登录一致。`isAdmin`、`roleIds` 是渠道返回的信息，不等于 Galaxy 管理员权限。

## 商品库存接口

```http
POST https://apiweb.erp321.com/webapi/ItemApi/ItemSku/GetPageListV2?owner_co_id=公司ID&authorize_co_id=公司ID&__from=抓包中的页面标识
Content-Type: application/json; charset=utf-8
Cookie: 最新会话Cookie
```

请求体示意：

```json
{
  "ip": "",
  "uid": "当前会话用户ID",
  "coid": "当前会话公司ID",
  "page": {"currentPage": 1, "pageSize": 50, "pageAction": 1},
  "data": {
    "sku_type": 1,
    "stock_type": 6,
    "orderBy": "",
    "queryFlds": ["sku_id", "i_id", "name", "properties_value", "qty", "order_lock", "orderable"]
  }
}
```

脚本保存并提交实际抓包的 **51 项 `queryFlds`**，上面只是字段示例；请求字段不代表每条返回一定有值。`sku_type=1`、`stock_type=6` 保持抓包原值，尚未核对它们的完整枚举含义，不擅自称为“所有库存类型”。

库存请求也复用抓包中的 `gwfp`、页面来源等非会话请求信息；本次抓包的 `u_sso_token` 请求头为空，实际认证使用 Cookie 中的新会话值，没有从库存 HAR 复制旧 Cookie。

分页约定：

| pageAction | 用途 | 结果 |
|---|---|---|
| `1` | 商品明细 | `data` 是当前页数组；`page.count=-1` 表示本次没有返回总数，不能当作负库存或零数据 |
| `2` | 总数 | 读取 `page.count`、`page.pages`；`data=[]` 正常，不代表没有商品 |

`--action list/count` 对应以上两个值；`--page` 默认 1，`--page-size` 默认 50，脚本当前开放 1—50（50 已实测；不是宣称上游最大值为50）。需要多页时保持筛选不变递增页码，不自动拉取全部商品。

已观察到的库存字段：

| 字段 | 页面业务含义 |
|---|---|
| `sku_id` / `i_id` | SKU / 商品标识，按原值保存 |
| `name` / `properties_value` | 名称 / 规格 |
| `qty` | 库存数量 |
| `orderable` | 可售量 |
| `order_lock` / `lock_qty` / `pick_lock` | 订单占用、锁定、拣货相关数量，分别保留，不合并计算 |
| `purchase_qty` | 采购在途 |
| `return_qty` / `in_qty` / `allocate_qty` / `virtual_qty` | 对应退货、入库、调拨、虚拟数量字段，具体统计口径仍以页面定义为准 |
| `sale_price` / `qty_modified` | 价格原值 / 库存更新时间原值；金额单位未单独核实，不套用有赞的“分”或数量千分位规则 |

本次数据中，商品 `co_id` 与登录公司一致，而 `owner_co_id=0`；不能将行内 `owner_co_id=0` 错判为跨公司。查询 URL 的 `owner_co_id`、`authorize_co_id` 均来自当前登录公司，不接受模型传任意公司 ID。

## 已验证范围与边界

- 已成功：同一账号、复用捕获的设备/浏览器状态，通过账号密码签发新会话，再独立读取保存的会话访问库存。
- 实测明细 50 条；计数为 21,455 条、430 页（只代表测试当时和当前抓包筛选范围）。
- 尚未验证：没有任何历史 Cookie 的全新机器登录、跨公网 IP 长期使用、会话持续多久、所有查询条件的枚举。
- 保存后续只读请求返回的 Cookie 更新；不会自动续期或重新登录。失效时显式运行 `login`，未成功不能把错误当作零库存。
- 当前同一 profile 应串行操作；后续平台多账号接入按员工隔离 profile，并通过后端串行化同账号的登录和会话替换。
- 后续 Galaxy AI 调用时只传业务筛选，渠道凭证由后台注入。不要把这个登录脚本作为 AI 可自由填写账号/公司身份的业务工具。

## 验证脚本

```bash
python3 -m unittest discover -s backend/jushuitan -p test_client.py -v
```

测试使用模拟数据，覆盖新会话核验、失败不覆盖、风险分支、分页参数、公司范围、原始业务字段保留、账号隔离及 Cookie 持久化。

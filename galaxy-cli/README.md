> 正式 CLI 仅安装在远程服务器 `/opt/galaxy-ai/galaxy-cli`，由已认证的 Galaxy AI 后端调用。任务系统 HAR 工具当前提供独立本地验证入口，使用临时依赖环境，不创建项目虚拟环境、全局命令链接或 CLI 凭证缓存。

# Galaxy CLI

任务系统分类（内部标识 `task`）新增通用工具 `task.har`，使用 Haralyzer 2.4.0 读取 HAR。分类提示词要求先制定方案、一次列齐资料清单，再分析验证和执行，全文见 [任务系统分类提示词](../designs/任务系统分类提示词.md)。支持 summary 概览、requests 筛选和分页、entry 请求详情、body 正文分段读取。保留请求头、Cookie、重复参数、表单中的 JSON 字符串及响应结果；默认隐藏常见凭证字段，`include_sensitive=true` 才读取原始正文和凭证值。工具不会执行抓包中的任何请求。

现已接入按需上传：AI 使用 `task.request-upload` 说明用途并生成上传卡片，用户上传后，服务器将属于当前用户、工作区和对话的 path 与 file_id 提供给 AI。聊天里的 `task.har` 可传这两个引用中的一个，不允许传入任意磁盘路径；独立本地入口仍支持本地 path。上传文件默认保留 24 小时，单文件最多 256 MB，每人最多占用 512 MB。说明见 [按需文件上传](../designs/按需文件上传.md) 和 [任务系统 HAR 解析工具](../designs/任务系统HAR解析工具.md)。

```bash
galaxy-cli help
galaxy-cli help --category youzan
galaxy-cli help --category excel
galaxy-cli youzan customer-search --input '{"keyword":"客户姓名"}'
galaxy-cli youzan customer-get --input '{"account_id":"搜索结果中的 accountId","account_type":1}'
galaxy-cli youzan business-report options
galaxy-cli youzan business-report query --input '{"period":"month","start_date":"2026-10-01","end_date":"2026-10-31","dimensions":[4,5,0]}'
```

help 每项只有工具名、分类和描述：

```json
{"version":"0.1.0","data":[{"tool":"youzan.customer-search","category":"youzan","description":"搜索客户"}]}
```

有赞业务命令直接返回接口的完整 JSON，包括原始 code、msg、data、客户记录、分页及其他业务字段。保留原始字段名、嵌套结构、空值、空数组、false 和 0，不筛字段、不重命名、不添加 Galaxy 业务包装。

客户搜索支持 keyword（渠道 ID／手机号／姓名／昵称）、last_trade_start/end、customer_created_start/end、member_created_start/end、branch_names/branch_ids、mobile_authorized、wecom_friend、page、page_size。时间为北京时间，日期开始补 00:00:00、结束补 23:59:59。默认第一页、每页 20，最多 50。

分类凭证由服务器按当前登录用户自动提供，模型只传业务筛选；不自动登录或续期。凭证值不进入命令参数、环境、help 或业务输出。输入输出结构和权限仍在内部校验。

客户详情必填字符串 account_id，选填整数 account_type（默认 1），都应沿用搜索记录的 accountId/accountType。结果按 customer、relations、behavior 分别保留主详情、关系人、行为分析三个接口的完整原始 JSON。主详情包括资料、标签、权益、资产、交易统计等全部上游字段；行为分析只返回该接口实际提供的记录，不宣称完整历史。主详情业务错误时停止后续查询；附加接口失败时保留其他部分，并在对应部分报告错误。

业务工具各自独立放在 `src/galaxy_cli/tools/youzan/customer_search.py` 和 `customer_get.py`，目录即分类；公共 HTTP 和凭证处理位于 `youzan_http.py`。新增工具通过 register 注册后，help 和客户端目录自动显示元数据，服务器仍需显式分配可执行权限。

```bash
# 仅在远程服务器 /opt/galaxy-ai 执行
bash deploy/setup-server.sh
/opt/galaxy-ai/galaxy-cli/.venv/bin/python -m unittest discover -s galaxy-cli/tests -v
```

Excel 工具位于 `src/galaxy_cli/tools/excel/create.py`，命令 `galaxy-cli excel create --input JSON`。必填 title 和 sheets；每张表必填 name、columns、rows，列必填 key、title，可选 type（默认 text）、width。类型支持 text/number/integer/currency/percent/date/datetime/boolean；手机号和长 ID 用文本，金额单位为元，百分比用小数。数据行是对象数组，键与列 key 对应，缺失值/null 留空，未定义列会报错。可选 subtitle 和 charts，图表支持 column/bar/line/pie。完整限制见 help。

示例输入：

```json
{"title":"消费报告","sheets":[{"name":"明细","columns":[{"key":"name","title":"客户"},{"key":"amount","title":"消费金额","type":"currency"}],"rows":[{"name":"演示客户","amount":268.5}],"charts":[{"type":"column","category":"name","values":["amount"]}]}]}
```

输出仅 `{file_id,filename,size,sheets:[{name,rows}]}`，size 为字节，不含服务器路径或公开链接。Excel 使用 XlsxWriter 生成，统一配色、交替行底色、列宽、筛选、冻结表头和图表；文本不执行公式或自动变成外链。Excel 无需有赞连接，生成及下载需要 excel:export 权限；文件绑定当前用户、工作区、对话，删除对话同时删除附件。正文和运行目录由服务器通过私有管道交给 CLI，避免大段数据进入进程参数。

经营报表是第三个 youzan 工具，独立文件 `src/galaxy_cli/tools/youzan/business_report.py`，在 help 中只占一项，包含 options/query 两个子命令。options 返回部门、销售单元、销售渠道接口的完整响应，以及小时、维度、周期选项；选项分页会自动读取，超出上限明确报错。query 必填 start_date、end_date、dimensions，支持日报/周报/月报/自定义，部门、销售单元、渠道、小时均可选，省略即全部。ID 和渠道代码从 options 取得，明确选择的筛选值会先校验，不允许传入凭证、URL、店铺或异步查询 key。

query 默认第一页、每页 20，可选 page_size 1—50。查询异步结果会短时间轮询，最终完整返回原始响应；data.status 非 1 时不能当成零数据。完成结果在 data.data.data，包含 items 和 paginator。isTotal=true 的合计可能每页重复，不可重复累计。金额展示字段按接口元单位读取，不替换成自行计算的 GMV。工具说明仅包含调用方式、输入、输出和解析规则。

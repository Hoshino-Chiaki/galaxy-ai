# 任务系统 HAR 解析工具

状态：2026-11-16 本地实现和验证完成；随后已接入服务器按需上传和聊天执行，客户端 0.7.6。最新流程见 [按需文件上传](按需文件上传.md)，以下保留最初本地解析验证记录。

## 分类与提示词

- 显示分类：任务系统；内部标识：`task`。
- 通用工具：`task.har`，接口数量不会增加工具注册数量。
- 分类默认提示词已扩展为方案制定、一次列齐资料清单、文件用途说明及验证执行流程，全文见 [任务系统分类提示词](任务系统分类提示词.md)。
- 管理员自定义提示词优先；明确清空后不再注入默认提示词。提示词仍受工作区和工具权限约束。
- 工具目录和分类提示词页面均已增加“任务系统”的中文显示；本地网页构建已通过，未安装新的桌面版本。

## 当前能力

使用固定依赖 `haralyzer==2.4.0` 的 `HarParser` 和 `HarEntry`；封装负责业务分析所需的信息整理，不进行平台业务推断。

| action | 用途 | 参数 |
| --- | --- | --- |
| summary | 文件指纹、请求数量、域名、方法、请求头名称、环境线索、登录资料是否存在 | path |
| requests | 请求索引，保留原始条目编号 | path；可选 host、method、search、offset、limit |
| entry | 请求头、Cookie、查询参数、负载、响应头、Cookie、返回内容与时间信息 | path、entry_index；可选 body_offset、body_limit |
| body | 单独分段读取请求或响应正文 | path、entry_index；可选 part、body_offset、body_limit |

所有操作支持 `include_sensitive`，默认 false。默认保留业务字段，并隐藏常见凭证字段和可识别的已知凭证值；这不是个人或商业数据的全面匿名化。原始正文仅在 include_sensitive=true 时输出，不能把敏感结果复述到普通回复或公共报告。

- path 是本地文件路径，不要求 `.har` 扩展名，支持 UTF-8 BOM。
- entry_index 从 0 开始，筛选和分页后不会重新编号。每次返回源文件 SHA-256，读取不同结果时可确认资料版本一致。
- 请求头、Cookie、查询参数、表单字段用数组保留，不因重名覆盖。
- 表单同时保留原始字段字符串和 decoded_value；例如字符串中的 JSON 可解析查看，但不把其原始 HTTP 类型自动改成 JSON 请求。
- 原始请求正文和响应正文包含长度与 SHA-256，支持按字符分页；base64 响应保留编码原文，并尝试解码文本。
- 原始文本下一页为 next_offset；解析文本为 parsed_next_offset；base64 解码文本为 decoded_next_offset。三者位置独立。正文分页片段不能当作完整 JSON。
- 区分缺失正文、空正文、只有表单参数、二进制或解码失败；缺失数据不会补造。
- 原始文件保持不变；不自动保存包含真实凭证的提取结果，不发送抓包请求。
- 当前整份文件在内存解析，上限 256 MB；本地实测约 110 MB 文件可处理。未来服务器接入时再确定资源配额和文件访问范围。

## Windows 本地验证入口

现有正式 CLI 及业务适配器依赖 Linux，因此提供独立入口，只发现任务系统工具，复用同一注册器、输入校验、处理函数和输出结构。不改变现有 Linux 执行机制。

从项目根目录运行：

```powershell
$env:PYTHONPATH = (Resolve-Path 'galaxy-cli/src').Path
uv run --no-project --python 3.13 --with haralyzer==2.4.0 --with 'jsonschema>=4.23,<5' python -X utf8 -m galaxy_cli.har_local
```

省略 --input 时输出工具说明。以下 --input 示例适用于本地入口；标准 CLI 注册命令为 `galaxy-cli task har --input JSON`，尚未授权服务器聊天执行。

```json
{"path":"C:/Users/Administrator/Desktop/成本价","action":"summary"}
```

```json
{"path":"C:/Users/Administrator/Desktop/成本价","action":"requests","host":"gateway.youzan.com","method":"POST","search":"update.product"}
```

```json
{"path":"C:/Users/Administrator/Desktop/成本价","action":"entry","entry_index":308,"body_limit":64000}
```

明确需要原始正文或真实凭证时才增加 `"include_sensitive":true`。这里只给参数示例，不在文档保存真实登录值。

## 验证结果

自动检查：HAR 工具 12 项、分类提示词 2 项，共 14 项通过。覆盖重复字段、嵌套 JSON、空值与 false/0、敏感字段处理、请求筛选、正文分页重组、base64、缺失正文、无效文件和本地入口。提示词测试仅替换 Linux 文件存储，实际默认值、工作区隔离、权限过滤、修改及清空逻辑参与测试。

真实本地“成本价”文件：

- 109,884,493 字节，638 条有效请求。
- SHA-256：`19df8c132b5dbb26957cc28a7f6177b01aa4d1c9cacd984ed37e06dde1c161bc`。
- 根据域名、方法和网址关键词找到条目 308；不是工具中硬编码商品接口。
- 64 个表单字段，两个捕获到的成本价均为字符串 `11.00`；抓包记录的响应 success 为 true。
- 请求／响应头、Cookie、查询参数、请求正文和响应正文与原文件逐项比较一致。
- 从原文件独立提取 28 项登录相关值，未在该条目的默认输出中发现明文。
- 测试前后源文件指纹一致，发送业务请求 0 次。
- 本次概览约 0.70 秒，筛选约 0.37 秒，敏感详情和默认详情分别约 0.37、0.38 秒；这只是当前机器单次结果。

验证数据保存在本地 `artifacts/task-har-local-validation.json`，不包含真实登录值或完整业务正文。

## 后续边界

现已完成按用途开放的文件上传、服务器按用户和工作区及对话定位文件、解析权限与自动续接。模型可传上传后返回的 path 或 file_id，path 会转换成上传标识并校验归属；不会直接打开任意磁盘路径。Python 任意执行、Windmill、队列和任务板仍未接入。

参考：[Haralyzer](https://github.com/haralyzer/haralyzer)、[Haralyzer 2.4.0](https://pypi.org/project/haralyzer/2.4.0/)。

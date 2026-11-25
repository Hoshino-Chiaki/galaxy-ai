from ...context import read_context
from ...gjp.cost_schema import INPUT
from ...gjp.schemas import OUTPUT

DESCRIPTION = (
    '批量查询管家婆当前库存均价，命令 galaxy-cli gjp inventory-cost --input JSON。'
    'barcodes、source_path、resume_id 必选且只选一种：barcodes 为条码字符串数组，或包含 barcode、可选 product_name 的对象数组；'
    '大量条码优先传 source_path，支持本人工作目录、/account-files、/account-cache 中的 XLSX、CSV、JSON、JSONL；'
    '文件可选 barcode_column（默认“规格编码”）、name_column、sheet（XLSX工作表名，默认第一张）、header_row（表格标题行，默认1）。'
    'JSON/JSONL须为条码字符串或按列名提供的对象记录，不支持任意嵌套结果；先用Python整理成单列CSV即可。'
    '自动去重、批量取数，无须逐条查询或自行翻页。可选 organization_id、warehouse_id 为真实内部ID，只缩小范围；省略则查本人已绑定账套的全部可用机构/仓库。'
    '需要独立“读取库存均价”权限和已保存的账套绑定，不依赖网页登录会话有效，不接受数据库名、SQL、网址、账号或密码。'
    '始终保留零库存。非零正常库存按库存金额合计/数量合计，先四舍五入保留2位小数，与网页库存均价一致，再用于数量乘成本计算；'
    '零库存且金额及存储单价均为0时均价为0；负库存、零数量残留金额、缺少库存记录等返回待核对状态与null均价，不能按0成本处理。'
    '同条码多商品只有商品名精确匹配唯一候选时才选择，否则返回ambiguous及候选，禁止默认取第一项。'
    '输出data含resume_id、summary、rows；每行含barcode、商品ID和名称、quantity、stock_amount、avg_cost、status，金额和数量为十进制字符串。'
    '完整结果存本人缓存，聊天只返回摘要及实际sandbox_path；summary.complete表示全部请求已处理，不代表所有条码都有可用价格，需检查needs_review。'
    '失败或打断可仅传真实resume_id继续未完成批次；已完成批次不重查，续跑时间可能不同。新查询默认重新取数，不能把当前库存均价当成历史出库实际成本。'
)


def register(registry):
    @registry.tool(name='gjp.inventory-cost', category='gjp', description=DESCRIPTION,
                   input_schema=INPUT, output_schema=OUTPUT)
    def invoke(arguments):
        from ...gjp.inventory_cost import run
        return run(arguments, read_context())

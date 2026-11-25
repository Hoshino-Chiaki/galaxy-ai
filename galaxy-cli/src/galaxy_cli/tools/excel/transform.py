"""Run declarative file-level processing in one CLI invocation."""
from ...context import read_context
from ...excel.schema import TRANSFORM_INPUT as INPUT, TRANSFORM_OUTPUT as OUTPUT, normalize

DESCRIPTION = (
    '批量匹配、筛选、计算和导出本人表格文件；galaxy-cli excel transform --input JSON；只传文件与规则，不传全部明细。'
    '必填 sources={别名:source} 和 exports=[{table:别名,sheet:输出表名}]，可选 steps（依次执行）、filename（默认数据处理结果.xlsx）。'
    'source 必填 path，其余 sheet/header_row/encoding/record_path/columns/text_columns 以及文件格式、目录与 excel.inspect 相同，先 inspect 确认真实列名。'
    '每步必填 op 和 into（结果别名，可覆盖内存别名，不覆盖源文件）。'
    'lookup：必填 table,from,left_on,right_on（等长列名数组）,columns={来源列:新增列名}；按键精确左匹配，保持主表行数顺序；'
    '空键不匹配，默认 multiple=error 遇到重复键报错；明确需要一对多字段合并时用 multiple=join，separator默认、，合并不同值到同一格，不展开复制行；可选 match_column 新增匹配布尔列。'
    'filter：必填 table,conditions=[{column,operator,value}]；logic 默认AND，可OR；operator 支持 ==/!=/>/</>=/<=/contains/startswith/endswith/in/not_in/is_blank/not_blank，'
    '除最后两种外必须传 value，in/not_in 用数组；字符串精确比较，数字比较用JSON数字。'
    'calculate：必填 table,columns=[{name,expression,variables:{变量名:列名},decimals:2,status_column:可选状态列名}]；'
    '使用十进制 + - * /、比较、条件表达式和 abs，列按顺序计算，默认保留2位四舍五入，可0..12位；'
    '例如 expression=p*q，variables={p:库存均价,q:实际数量}；quantity 指销售或退货数量，不是库存数量。'
    '新增列不能覆盖已有列，null/空白/非数值/除零返回空值并统计原因，可用 status_column 保存逐行原因；合法0和负数正常计算，不推断“-”的退货量。'
    'select：必填 table,columns（有序列名数组），可选 rename={原列:新列}；concat：必填 tables（别名数组），按顺序追加，按同名列对齐，不去重、不分组、不补0。'
    'exports 可选 totals=[列名] 和 formats={列名:text/number/currency/integer/percent}；数值格式会把合法数字文本写为数字，无效文本保留原值，text 强制文本；'
    '输出新XLSX，统一表头、列宽、筛选、冻结；'
    '只导出所选 exports，不保留源文件宏、图表或原格式，不执行公式，不自动换算金额。'
    '返回 path,filename,size,sheets（行数、十进制合计及空值统计）,summary（各步行数/匹配/计算异常数），不返回全部明细。'
    '文件在本人工作目录，需下载时再 file.publish 真实 path。')


def register(registry):
    @registry.tool(name='excel.transform', category='excel', description=DESCRIPTION, input_schema=INPUT, output_schema=OUTPUT)
    def transform(arguments):
        from ...excel.engine import transform_files
        return transform_files(normalize(arguments, 'transform'), read_context())

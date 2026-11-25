"""Describe account-local data without returning whole worksheets."""
from ...context import read_context
from ...excel.schema import INSPECT_INPUT as INPUT, INSPECT_OUTPUT as OUTPUT, normalize

DESCRIPTION = (
    '查看本人文件的工作表、列名、总行数、少量样例和指定列合计；galaxy-cli excel inspect --input JSON。'
    '必填 source={path}；path 支持本人相对路径、/account-files/上传路径、/account-cache/缓存路径、/workspace/执行文件，禁止他人目录和URL。'
    '支持 XLSX/XLS/CSV/JSON/JSONL；source 可选 sheet（省略首表）、header_row（1起，默认1）、encoding（CSV默认utf-8-sig，可gb18030）、'
    'record_path（JSON记录数组的点分键路径）、columns（JSON提取映射，目标列名:记录内点分键路径）、text_columns（强制文本列名数组，条码应为文本）。'
    '重复列名以 __2、__3 区分，后续按返回列名引用；不会丢弃原有重复明细。可选 preview_rows=0..10（默认3）、totals=[需合计的列名]。'
    '返回 rows,sheet,sheets,columns,preview,preview_truncated,totals,formula_cells；样例长文本截断会标记 preview_truncated=true，完整数据仍在源文件；'
    '合计用十进制字符串，并给出空值/无效数量；公式作为文本，不执行或重算。'
    '源文件不修改，金额不自动换单位；大量数据用 excel.transform 处理，不把整表搬入参数。')


def register(registry):
    @registry.tool(name='excel.inspect', category='excel', description=DESCRIPTION, input_schema=INPUT, output_schema=OUTPUT)
    def inspect(arguments):
        from ...excel.engine import inspect_file
        return inspect_file(normalize(arguments, 'inspect'), read_context())

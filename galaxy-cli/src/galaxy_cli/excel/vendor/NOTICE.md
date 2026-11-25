# Excel MCP integration

Selected algorithms from https://github.com/mbeps/excel-mcp at revision
3e0277a3b4849ece9c695ed6dac4791b5dfb5e36, MIT license (LICENSE.txt).

`expression_validator.py` is copied unchanged. `frames.py` adapts the dataframe
merge and multi-condition filter implementations from `tools/pivot_etl.py` and
`tools/analysis.py`. Galaxy supplies descriptor-scoped input streams, immutable
source files, explicit duplicate-key policy, null-safe matching, Decimal
arithmetic and file receipts. The upstream MCP server, arbitrary file access,
custom code execution and unrelated statistical dependencies are not enabled.

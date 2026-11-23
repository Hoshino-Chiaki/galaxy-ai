from ._schemas import DELETE as INPUT, OUTPUT, normalize as validate, register_tool
DESCRIPTION=('实验功能：删除本人任务，galaxy-cli schedule delete --input JSON。必填真实 id、revision，来自最近 list 或当前关联任务。删除后不再触发，正在执行的运行会被请求停止；已经完成的外部业务操作不会撤回，历史结果对话保留。仅在用户明确要求删除时调用；成功返回 deleted=true、id。')
def normalize(value):return validate(value,INPUT)
def register(registry):register_tool(registry,'delete',INPUT,DESCRIPTION)

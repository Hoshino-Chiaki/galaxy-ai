from ._schemas import LIST as INPUT, OUTPUT, normalize as validate, register_tool
DESCRIPTION=('实验功能：galaxy-cli schedule list --input JSON，默认 {} 列出当前账号任务；可选 id 查询具体任务及最近运行记录。只返回本人任务，不接受账号或工作区参数。task 含 id、revision、name、instruction、time_description、rule、status、next_at、next_runs、last_run。runs 含实际状态、conversation_id 和时间；有执行结果时可在 Galaxy AI 对话查看。修改或删除前用真实 id、revision，不猜编号；失败、部分完成、中断都不是成功或零数据。')
def normalize(value):return validate(value,INPUT)
def register(registry):register_tool(registry,'list',INPUT,DESCRIPTION)

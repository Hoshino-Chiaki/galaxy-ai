from ._schemas import UPDATE as INPUT, OUTPUT, normalize as validate, register_tool
DESCRIPTION=('实验功能：修改本人任务的触发时间，galaxy-cli schedule update-time --input JSON。必填 id、revision（来自关联任务或最近 list）、time_description、rule；规则与 schedule.create 一致。只修改时间，不修改任务内容，不立即执行业务。用户点击卡片后只输入新的自然语言时间，按服务器关联的真实任务核对后更新；含糊时间通过 interaction.ask 澄清。返回 updated=true、task 及接下来三次 next_runs 供核对；旧版本冲突时先 list 重新核对，不能盲目覆盖。')
def normalize(value):return validate(value,INPUT)
def register(registry):register_tool(registry,'update-time',INPUT,DESCRIPTION)

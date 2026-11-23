from ._schemas import CREATE as INPUT, OUTPUT, normalize as validate, register_tool
DESCRIPTION=('实验功能：创建当前账号的持久化定时任务。命令 galaxy-cli schedule create --input JSON。必填 name（1至100字）、instruction（1至6000字，独立完整的执行内容，明确店铺/人员/口径，不能依赖本次聊天历史）、time_description（用户可读时间规则）、rule、request_key（本次创建的唯一标识，重试沿用相同值防止重复创建）。'
 'rule.type=cron 时传五段 expression，如 0 9 * * 1-5 表示工作日九点，默认 timezone=Asia/Shanghai；once 传带时区 ISO at；interval 传正整数 seconds，可选带时区 anchor。支持复杂 Cron 日历规则；不要只把中文时间当表达式。'
 '先用 time.now 确认时间，条件不明确时 interaction.ask；仅在用户要求安排后创建。instruction 保留今天/昨天等相对时间，执行时重新解析。每次运行会生成独立 Galaxy AI 对话。用户要求发送到本人微信时，在 instruction 中明确先生成所需文件，再调用 clawbot.send 使用实际相对 path 投递给当前账号绑定的微信，并核对发送回执；可安排无人值守投递，仍受执行时权限、绑定与微信消息上下文有效性约束。不能把创建成功或文件生成成功说成已经发送；飞书投递需另查实际工具能力。'
 '返回真实 task（id、revision、内容、规则、next_at、next_runs 三次时间、status）、created。收到成功结果才确认创建；网络中断先 list 核对，不重复创建。')
def normalize(value):return validate(value,INPUT)
def register(registry):register_tool(registry,'create',INPUT,DESCRIPTION)

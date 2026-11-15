function event(id, total, date, options = {}) {
  const rows = [
    { isTotal: true, turnoverAmtStr: total, orderPaidCnt: 10, unitPriceStr: '123.45', goodsRefundAmtStr: '0.00', fixtureUnknownField: { retained: true } },
    { isTotal: false, dateStr: date, salesKdtName: '示例门店 A', turnoverAmtStr: total, orderPaidCnt: 10, unitPriceStr: '123.45', goodsRefundAmtStr: null, fixtureUnknownField: '完整保留' },
  ]
  return { id, call_id: id, name: 'galaxy_cli', command: 'galaxy-cli youzan business-report query', status: 'success', duration_ms: 10,
    result: { code: 200, success: true, data: { status: 1, data: { code: 200, success: true, data: { items: rows, paginator: { page: 1, pageSize: 20, totalCount: 41 } } } } }, ...options }
}

const markdown = `## 今日经营概览

营业额 **1,234.50 元**，较上一份报表增长 **23.45%**。

| 指标 | 本期 | 基期 |
| --- | ---: | ---: |
| 营业额 | 1,234.50 元 | 1,000.00 元 |
| 支付订单 | 10 笔 | 10 笔 |

### 值得关注

- 营业额上升，支付订单数持平。
- 客单价需要结合商品结构继续分析。

> 完整工具结果可在工具记录中展开查看。

\`\`\`json
{"营业额": 1234.50, "订单数": 10, "说明": "这是代码展示，不会执行"}
\`\`\`

查看[项目文档](https://github.com/vercel/streamdown)。
`

function conversation() {
  const emptyEvents = Array.from({ length: 10 }, (_, index) => {
    const value = event(`fixture-empty-${index}`, '0', '')
    value.result.data.data.data.items = []
    value.result.data.data.data.paginator.totalCount = 0
    return value
  })
  return { id: 'fixture-output-conversation', title: '经营报表展示示例', user_id: 'fixture-user', workspace_id: 'fixture-workspace', created_at: 0, updated_at: 0, model: 'fixture', provider_name: '界面验证',
    messages: [{ id: 'fixture-user-message', role: 'user', content: '对比两份经营报表，并展示完整数据。', created_at: 0 },
      { id: 'fixture-assistant-message', role: 'assistant', content: markdown, reasoning_content: '先读取两份报表，再比较接口返回的合计值。', created_at: 0,
        tool_events: [event('fixture-first', '1,000.00', '2026-11-08'), event('fixture-second', '1,234.50', '2026-11-09'), ...emptyEvents] }] }
}

module.exports = { event, markdown, conversation }

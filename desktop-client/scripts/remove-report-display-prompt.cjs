// Removes only the exact automatic report display guidance previously appended.
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { chromium } = require('../../session_research/runtime/node_modules/playwright-core')
const marker = '【Galaxy AI 客户端结果展示】'
const guidance = `${marker}
客户端会将经营报表工具的查询结果自动展示为指标卡、图表和完整字段表格，工具记录保留完整原始响应。对于这些已展示的报表，优先用简洁 Markdown 说明主要发现、变化和需要关注的问题，避免重复抄写整张报表，也无需为了排版生成 HTML、CSS 或 JavaScript。用户明确要求完整文字、表格、特定格式或详细分析时，按用户要求输出。用户要求 Excel 时，仍须实际调用生成与文件托管工具提供下载，不能用界面卡片代替文件。比较报表时核对日期范围、筛选条件和分页口径；不把缺失值当作零，不重复累计合计行。`

;(async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:19224')
  try {
    const page = browser.contexts()[0].pages().find(page => page.url().includes('index.html'))
    const original = await page.evaluate(async () => {
      const response = await window.desktop.service.request('admin.settings')
      if (!response.ok || typeof response.data.global_prompt !== 'string') throw new Error('Cannot read global prompt')
      return response.data.global_prompt
    })
    if (!original.includes(marker)) { console.log(JSON.stringify({ updated: false, alreadyRemoved: true })); return }
    assert(original.includes(guidance), 'Display guidance was edited; preserve user changes')
    const prompt = original.endsWith('\n\n' + guidance) ? original.slice(0, -guidance.length - 2) : original.replace(guidance, '')
    assert(prompt.length <= 20000, 'Existing prompt must be retained in full')
    const directory = path.resolve(__dirname, '../../backend/private/prompt-backups')
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 })
    fs.writeFileSync(path.join(directory, `before-disable-report-display-${Date.now()}.json`), JSON.stringify({ global_prompt: original }), { mode: 0o600, flag: 'wx' })
    const result = await page.evaluate(async ({ original, prompt }) => {
      const fresh = await window.desktop.service.request('admin.settings')
      if (!fresh.ok || fresh.data.global_prompt !== original) throw new Error('Global prompt changed during update')
      const response = await window.desktop.service.request('admin.prompt', { prompt })
      if (!response.ok) throw new Error('Cannot save global prompt')
      const saved = await window.desktop.service.request('admin.settings')
      return { verified: saved.ok && saved.data.global_prompt === prompt }
    }, { original, prompt })
    assert.equal(result.verified, true)
    console.log(JSON.stringify({ updated: true, otherInstructionsRetained: true, backupCreated: true, editableInConsole: true, verified: true }))
  } finally { await browser.close() }
})().catch(error => { console.error(error.message); process.exitCode = 1 })

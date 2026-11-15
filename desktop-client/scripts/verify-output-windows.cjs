// Reads existing cloud history; does not invoke AI or any business tool.
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { chromium } = require('../../session_research/runtime/node_modules/playwright-core')

;(async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:19224')
  const page = browser.contexts()[0].pages().find(value => value.url().includes('index.html'))
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  try {
    await page.locator('.workspace-page').waitFor()
    const selected = await page.evaluate(async () => {
      const response = await window.desktop.service.request('chat.list')
      if (!response.ok) throw new Error('History unavailable')
      for (const [index, item] of response.data.conversations.entries()) {
        const response = await window.desktop.service.request('chat.get', { id: item.id })
        if (!response.ok) continue
        const events = response.data.messages.flatMap(message => message.tool_events || [])
        if (events.some(event => event.status === 'success' && event.command === 'galaxy-cli youzan business-report query')) return { id: item.id, index }
      }
      throw new Error('No existing report conversation')
    })
    await page.getByRole('button', { name: '对话记录', exact: true }).click()
    await page.locator('.history-open').nth(selected.index).click()
    await page.locator('.markdown-message').first().waitFor()
    assert.equal(await page.locator('.business-report-results, .business-report-card, .report-result-toolbar, .report-comparison, .report-empty-state').count(), 0)
    const tools = page.locator('.tool-event')
    assert(await tools.count() > 0)
    const first = tools.first()
    await first.locator('summary').click()
    assert(await first.locator('pre').isVisible())
    JSON.parse(await first.locator('pre').textContent())
    await first.locator('summary').click()
    const reasoning = page.locator('.reasoning-disclosure').first()
    if (await reasoning.count()) {
      await reasoning.locator('summary').click()
      assert.equal(await reasoning.evaluate(element => element.open), true)
      await reasoning.locator('summary').click()
    }
    await page.locator('.markdown-message').last().scrollIntoViewIfNeeded()
    await page.locator('.message-scroll').evaluate(element => { element.scrollTop -= 65 })
    const dir = path.resolve(__dirname, '../artifacts')
    await page.screenshot({ path: path.join(dir, 'windows-output-report.png') })
    const summary = { passed: true, actualWindowsApp: true, source: 'existing cloud conversation', automaticReportUI: false,
      markdownMessages: await page.locator('.markdown-message').count(), rawToolResultsExpandable: true,
      existingDownloadCards: await page.locator('.chat-file-card').count(), aiCalls: 0, businessToolCalls: 0, rendererErrors: errors.length }
    assert.deepEqual(errors, [])
    fs.writeFileSync(path.join(dir, 'output-windows-check.json'), JSON.stringify(summary, null, 2))
    console.log(JSON.stringify(summary))
  } finally { await browser.close() }
})().catch(error => { console.error(error.message); process.exitCode = 1 })

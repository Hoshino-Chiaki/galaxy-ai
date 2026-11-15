const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { chromium } = require('../../session_research/runtime/node_modules/playwright-core')

;(async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:19224')
  const page = browser.contexts()[0].pages().find(p => p.url().includes('index.html'))
  const dir = path.resolve(__dirname, '../artifacts')
  const report = JSON.parse(fs.readFileSync(path.join(dir, process.env.EXCEL_VERIFY_REPORT || 'excel-live-check.json'), 'utf8'))
  const errors = []
  page.on('pageerror', () => errors.push('renderer error'))
  try {
    await page.getByRole('button', { name: 'AI', exact: true }).click()
    const response = await page.evaluate(async id => {
      const result = await window.desktop.service.request('chat.get', { id })
      return result.ok ? { id: result.data.id, title: result.data.title } : null
    }, report.conversation_id)
    assert(response)
    await page.getByRole('button', { name: '对话记录', exact: true }).click()
    await page.locator('.history-open').filter({ has: page.locator('span', { hasText: response.title }) }).first().click()
    const card = page.locator('.chat-file-card').filter({ hasText: report.filename })
    await card.waitFor()
    assert.equal(await card.locator('.chat-file-info strong').innerText(), report.filename)
    if (report.sheets) assert((await card.innerText()).includes(report.sheets.length + ' 个工作表'))
    else assert((await card.innerText()).includes('KB'))
    await card.scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(dir, 'windows-excel-chat.png') })
    assert.equal(await card.getByRole('button', { name: '下载 ' + report.filename }).isEnabled(), true)
    assert.deepEqual(errors, [])
    fs.writeFileSync(path.join(dir, 'excel-chat-ui-check.json'), JSON.stringify({ passed: true, actualWindowsApp: true,
      savedConversationAttachment: true, fileCard: true, downloadButton: true, sheets: report.sheets?.length ?? null, rendererErrors: 0 }, null, 2))
    // The caller completes the native Save dialog through the Windows desktop bridge.
    if (process.argv.includes('--download')) {
      await card.getByRole('button', { name: '下载 ' + report.filename }).click()
    }
    console.log(JSON.stringify({ passed: true, fileCard: true, downloadRequested: process.argv.includes('--download') }))
  } finally { await browser.close() }
})().catch(() => { console.error(JSON.stringify({ passed: false, message: 'Excel chat UI check failed.' })); process.exitCode = 1 })

const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { chromium } = require('../../session_research/runtime/node_modules/playwright-core')
const fixture = require('./fixtures/output-results.cjs')

;(async () => {
  const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', headless: true })
  const page = await browser.newPage({ viewport: { width: 1280, height: 960 } })
  const errors = [], externalRequests = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('request', request => { if (!request.url().startsWith('http://127.0.0.1:4173')) externalRequests.push(request.url()) })
  try {
    await page.addInitScript(({ conversation }) => {
      window.__fixtureConversation = conversation
      window.desktop = {
        auth: { current: async () => ({ ok: true, data: { user: { id: 'fixture-user', username: '示例用户', role: 'employee', workspace_id: 'fixture-workspace', permissions: [] }, expires_at: 9999999999, remember: false } }) },
        preferences: { read: async () => ({ remember: false, account: '' }), write: async () => {} },
        service: { request: async action => ({ ok: true, data: action === 'chat.status' ? { available: true, name: '界面验证' } : action === 'chat.list' ? { conversations: [window.__fixtureConversation] } : window.__fixtureConversation }) },
        openExternal: async url => { window.__lastExternal = url }, minimize() {}, maximize() {}, close() {},
      }
      Object.defineProperty(navigator, 'clipboard', { value: { writeText: async value => { window.__copied = value } } })
    }, { conversation: fixture.conversation() })
    await page.goto('http://127.0.0.1:4173')
    async function openHistory() {
      await page.getByRole('button', { name: '对话记录', exact: true }).click()
      await page.locator('.history-open').first().click()
      await page.locator('.markdown-message h2').waitFor()
    }
    await openHistory()
    assert.equal(await page.locator('.business-report-results, .business-report-card, .report-result-toolbar, .report-comparison, .report-empty-state').count(), 0)
    assert.equal(await page.locator('.tool-event').count(), 12)
    const native = JSON.parse(await page.locator('.tool-event pre').first().textContent())
    assert.equal(native.data.data.data.items[0].fixtureUnknownField.retained, true)
    assert.equal(await page.locator('.markdown-message table').count(), 1)
    assert.equal(await page.locator('.markdown-message pre').count(), 1)
    await page.getByRole('button', { name: '复制回复', exact: true }).click()
    assert.equal(await page.evaluate(() => window.__copied), fixture.markdown)
    await page.locator('.markdown-message a').click()
    assert.equal(await page.evaluate(() => window.__lastExternal), 'https://github.com/vercel/streamdown')
    const dir = path.resolve(__dirname, '../artifacts')
    await page.locator('.message-scroll').evaluate(element => { element.scrollTop = 0 })
    await page.screenshot({ path: path.join(dir, 'output-markdown-only-preview.png') })
    await page.locator('.markdown-message').scrollIntoViewIfNeeded()
    await page.waitForFunction(() => document.querySelectorAll('.markdown-message pre span[style]').length > 0)
    await page.screenshot({ path: path.join(dir, 'output-markdown-preview.png') })
    // Check hostile content and very wide output without sending data to any server.
    await page.evaluate(() => {
      const message = window.__fixtureConversation.messages[1]
      message.content = '## 安全渲染测试\n\n<script>window.__executed=true</script>\n<img src="https://invalid.example/pixel" onerror="window.__executed=true">\n\n[非法链接](javascript:alert(1))\n\n![外部图片](https://invalid.example/image.png)\n\n```text\n' + 'x'.repeat(2000) + '\n```\n\n| 长字段 | 另一个字段 |\n|---|---|\n| ' + '字段'.repeat(300) + ' | 保留 |'
    })
    await page.getByRole('button', { name: '新建对话', exact: true }).click()
    await openHistory()
    assert.equal(await page.locator('.markdown-message script, .markdown-message img, .markdown-message iframe').count(), 0)
    assert.equal(await page.locator('.markdown-message a[href^="javascript:"]').count(), 0)
    assert.equal(await page.evaluate(() => window.__executed), undefined)
    await page.setViewportSize({ width: 760, height: 820 })
    const overflow = await page.locator('.message-scroll').evaluate(element => element.scrollWidth > element.clientWidth + 2)
    assert.equal(overflow, false, 'wide tables and code must scroll inside the message')
    assert.deepEqual(errors, [])
    assert.deepEqual(externalRequests, [])
    const result = { passed: true, fixturesOnly: true, rendererErrors: 0, externalRequests: 0, checks: ['Markdown headings/table/code', 'automatic report UI removed', 'complete raw tool results retained', 'copy original Markdown', 'safe external links', 'raw HTML disabled', 'no remote image requests', '760px layout'] }
    fs.writeFileSync(path.join(dir, 'output-ui-check.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result))
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })

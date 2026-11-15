// Real model tool-call loop; supplier keys and login tokens remain on the server/main process.
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { chromium } = require('../../session_research/runtime/node_modules/playwright-core')

;(async () => {
  const browser = await chromium.connectOverCDP(process.env.UI_CDP_URL || 'http://127.0.0.1:19224')
  const page = browser.contexts()[0].pages().find(value => value.url().includes('index.html'))
  assert(page)
  const failures = []
  page.on('pageerror', () => failures.push('renderer error'))
  try {
    await page.waitForFunction(() => !!document.querySelector('.workspace-page') || !!(document.querySelector('#account') && !document.querySelector('#account').disabled))
    if (await page.locator('#account').count()) {
      const lines = fs.readFileSync(path.resolve(__dirname, '../../1.txt'), 'utf8').replace(/^\uFEFF/, '').split(/\r?\n/)
      await page.locator('#account').fill(lines[0])
      await page.locator('#password').fill(lines[1])
      await page.getByRole('checkbox', { name: '记住登录' }).check({ force: true })
      await page.locator('.login-button').click()
      await page.locator('.workspace-page').waitFor()
    }
    await page.getByRole('button', { name: 'AI', exact: true }).click()
    await page.getByRole('button', { name: '新建对话', exact: true }).click()
    await page.locator('#chat-message').fill('权限边界验证：请实际调用 galaxy_cli 工具，args=["help"]，根据实际结果简短回复。')
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await page.locator('.tool-event.success').first().waitFor({ timeout: 130000 })
    await page.locator('.tool-event.success summary').first().click()
    assert.equal(await page.locator('.tool-event.success pre').first().isVisible(), true)
    assert((await page.locator('.tool-event.success pre').first().innerText()).includes('customer-search'))
    assert((await page.locator('.tool-event.success pre').first().innerText()).includes('youzan'))
    await page.locator('.tool-event.success summary').first().click()
    await page.locator('#chat-message').fill('请再次调用 galaxy_cli，args=["help","help"]，简要说明 help 的输入输出。')
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await page.waitForFunction(() => document.querySelectorAll('.assistant .message-text').length === 2, undefined, { timeout: 130000 })
    const metadata = await page.evaluate(async () => {
      const list = await window.desktop.service.request('chat.list')
      const summary = list.data.conversations.find(item => item.title.startsWith('权限边界验证：'))
      const response = await window.desktop.service.request('chat.get', { id: summary.id })
      const messages = response.data.messages.filter(item => item.role === 'assistant')
      return { id: summary.id, model: response.data.model, privateContextExposed: messages.some(item => 'model_context' in item),
        events: messages.flatMap(item => item.tool_events || []).map(item => ({ id: item.id, name: item.name,
          command: item.command, status: item.status, version: item.result.version })) }
    })
    assert.equal(metadata.privateContextExposed, false)
    assert(metadata.events.filter(item => item.status === 'success').length >= 2)
    assert(metadata.events.every(item => item.name === 'galaxy_cli'))
    assert(metadata.events.filter(item => item.status === 'success').every(item => typeof item.version === 'string'))
    const dir = path.resolve(__dirname, '../artifacts')
    await page.screenshot({ path: path.join(dir, 'windows-galaxy-tool-calls.png') })
    assert.deepEqual(failures, [])
    const result = { passed: true, environment: 'Windows -> Arch -> real model -> fixed Galaxy CLI', ...metadata,
      checks: ['real CLI help', 'second tool round with reasoning context', 'tool result JSON viewer', 'private protocol context hidden'], rendererErrors: 0 }
    fs.writeFileSync(path.join(dir, 'galaxy-tools-check.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result))
  } finally { await browser.close() }
})().catch(() => { console.error(JSON.stringify({ passed: false, message: 'Galaxy tool verification failed; inspect UI without printing credentials.' })); process.exitCode = 1 })

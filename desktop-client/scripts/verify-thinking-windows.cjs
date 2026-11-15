const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { chromium } = require('../../session_research/runtime/node_modules/playwright-core')

;(async () => {
  let browser
  for (let n = 0; n < 15; n++) {
    try { browser = await chromium.connectOverCDP('http://127.0.0.1:19224'); break }
    catch { await new Promise(resolve => setTimeout(resolve, 400)) }
  }
  assert(browser)
  try {
    const page = browser.contexts()[0].pages().find(p => p.url().includes('index.html'))
    const errors = []
    page.on('pageerror', () => errors.push('renderer error'))
    await page.waitForFunction(() => !!document.querySelector('.workspace-page') || !!(document.querySelector('#account') && !document.querySelector('#account').disabled))
    if (await page.locator('#account').count()) {
      const lines = fs.readFileSync(path.resolve(__dirname, '../../1.txt'), 'utf8').replace(/^\uFEFF/, '').split(/\r?\n/)
      await page.locator('#account').fill(lines[0]); await page.locator('#password').fill(lines[1])
      await page.getByRole('checkbox', { name: '记住登录' }).check({ force: true })
      await page.locator('.login-button').click(); await page.locator('.workspace-page').waitFor()
    }
    await page.getByRole('button', { name: 'AI', exact: true }).click()
    await page.getByRole('button', { name: '新建对话', exact: true }).click()
    const toggle = page.getByRole('switch', { name: '深度思考', exact: true })
    const effort = page.getByRole('combobox', { name: '思考强度', exact: true })
    await toggle.waitFor()
    if (await toggle.getAttribute('aria-checked') !== 'true') await toggle.click()
    assert.deepEqual(await effort.locator('option').evaluateAll(options => options.map(e => e.value)), ['low', 'high', 'max'])
    await effort.selectOption('max')
    await toggle.click()
    assert.equal(await effort.isDisabled(), true)
    await page.locator('#chat-message').fill('界面设置联调，请只回复“已生效”。')
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await page.waitForFunction(() => document.querySelectorAll('.assistant .message-text').length === 1, undefined, { timeout: 180000 })
    const first = await page.evaluate(async () => {
      const list = await window.desktop.service.request('chat.list')
      const item = list.data.conversations.find(c => c.title.startsWith('界面设置联调，'))
      const result = await window.desktop.service.request('chat.get', { id: item.id })
      const reply = result.data.messages.at(-1)
      return { id: item.id, thinking: reply.thinking, effort: reply.reasoning_effort, hasReasoning: !!reply.reasoning_content?.trim() }
    })
    assert.equal(first.thinking, false); assert.equal(first.hasReasoning, false)
    await toggle.click(); await effort.selectOption('low')
    await page.locator('#chat-message').fill('继续只回复“已生效”。')
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await page.waitForFunction(() => document.querySelectorAll('.assistant .message-text').length === 2, undefined, { timeout: 180000 })
    const second = await page.evaluate(async id => {
      const result = await window.desktop.service.request('chat.get', { id })
      const reply = result.data.messages.at(-1)
      return { thinking: reply.thinking, effort: reply.reasoning_effort }
    }, first.id)
    assert.equal(second.thinking, true); assert.equal(second.effort, 'low')
    await page.reload(); await toggle.waitFor()
    assert.equal(await toggle.getAttribute('aria-checked'), 'true')
    assert.equal(await effort.inputValue(), 'low')
    await effort.selectOption('high')
    const dir = path.resolve(__dirname, '../artifacts')
    await page.screenshot({ path: path.join(dir, 'windows-thinking-controls.png') })
    assert.deepEqual(errors, [])
    const result = { passed: true, actualWindowsApp: true, realDeepSeekOff: true, realDeepSeekLow: true,
      effortOptions: ['low', 'high', 'max'], persistedAfterReload: true, defaultRestoredHigh: true, rendererErrors: 0 }
    fs.writeFileSync(path.join(dir, 'thinking-windows-check.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result))
  } finally { await browser.close() }
})().catch(() => { console.error(JSON.stringify({ passed: false, message: 'Windows thinking controls verification failed' })); process.exitCode = 1 })

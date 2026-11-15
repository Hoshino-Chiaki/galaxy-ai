// One short real AI request to verify the actual Windows pending/completed states.
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { chromium } = require('../../session_research/runtime/node_modules/playwright-core')

;(async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:19224')
  const page = browser.contexts()[0].pages().find(page => page.url().includes('index.html'))
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  try {
    await page.locator('.workspace-page').waitFor()
    await page.getByRole('button', { name: 'AI', exact: true }).click()
    await page.getByRole('button', { name: '新建对话', exact: true }).click()
    const form = page.locator('.chat-composer')
    const idleHeight = await form.evaluate(element => element.getBoundingClientRect().height)
    const bottomGap = await form.evaluate(element => innerHeight - element.getBoundingClientRect().bottom)
    assert(idleHeight <= 100 && bottomGap <= 12)
    assert.equal(await page.locator('.composer-footnote').count(), 0)
    assert.equal(await page.locator('.magic-shine-border').count(), 0)
    const question = '界面动效验证：不要调用工具，请用三句话说明如何安排一天的工作。'
    await page.locator('#chat-message').fill(question)
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    const border = page.locator('.magic-shine-border')
    await border.waitFor()
    assert.equal(await form.getAttribute('aria-busy'), 'true')
    const reducedMotion = await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)
    assert.equal(await border.evaluate(element => getComputedStyle(element).animationName), reducedMotion ? 'none' : 'galaxy-shine')
    const dir = path.resolve(__dirname, '../artifacts')
    await page.screenshot({ path: path.join(dir, 'windows-composer-busy.png') })
    await page.locator('.markdown-message').waitFor({ timeout: 180000 })
    await page.locator('.chat-composer[aria-busy=false]').waitFor()
    assert.equal(await border.count(), 0)
    await page.screenshot({ path: path.join(dir, 'windows-composer-complete.png') })
    const generated = await page.evaluate(async prefix => {
      const list = await window.desktop.service.request('chat.list')
      if (!list.ok) throw new Error('History unavailable')
      const item = list.data.conversations.find(item => item.title === prefix)
      if (!item) throw new Error('Test conversation missing')
      const chat = await window.desktop.service.request('chat.get', { id: item.id })
      if (!chat.ok) throw new Error('History unavailable')
      return { id: item.id, toolCalls: chat.data.messages.flatMap(message => message.tool_events || []).length }
    }, question.slice(0, 36))
    assert.equal(generated.toolCalls, 0)
    await page.evaluate(async id => {
      const result = await window.desktop.service.request('chat.delete', { id })
      if (!result.ok) throw new Error('Cannot remove UI test conversation')
    }, generated.id)
    await page.getByRole('button', { name: '新建对话', exact: true }).click()
    assert.deepEqual(errors, [])
    const result = { passed: true, actualWindowsApp: true, idleHeight, bottomGap, reducedMotion, rainbowWhilePending: true,
      removedAfterResponse: true, bottomTextRemoved: true, aiCalls: 1, businessToolCalls: 0, testConversationRemoved: true, rendererErrors: 0 }
    fs.writeFileSync(path.join(dir, 'composer-windows-check.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result))
  } finally { await browser.close() }
})().catch(error => { console.error(error.message); process.exitCode = 1 })

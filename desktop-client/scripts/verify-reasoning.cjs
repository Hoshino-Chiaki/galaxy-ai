// A single real message through Windows -> Arch -> the currently configured provider.
// Never logs supplier credentials, user passwords or provider reasoning text.
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
    const question = '界面功能验证：请比较 19×23 和 20×22 哪个大，简短回答。'
    await page.locator('#chat-message').fill(question)
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await page.locator('.assistant .message-text').waitFor({ timeout: 130000 })
    const details = page.locator('.reasoning-disclosure').first()
    await details.waitFor()
    assert.equal(await details.evaluate(element => element.open), false)
    assert.equal(await page.locator('.reasoning-text').first().isVisible(), false)
    const reasoningCharacters = await page.locator('.reasoning-text').first().evaluate(element => element.textContent.length)
    assert(reasoningCharacters > 0)
    await details.locator('summary').click()
    assert.equal(await details.evaluate(element => element.open), true)
    assert.equal(await page.locator('.reasoning-text').first().isVisible(), true)
    await details.locator('summary').click()
    assert.equal(await details.evaluate(element => element.open), false)
    const metadata = await page.evaluate(async () => {
      const list = await window.desktop.service.request('chat.list')
      const item = list.data.conversations.find(value => value.title.startsWith('界面功能验证：'))
      const result = await window.desktop.service.request('chat.get', { id: item.id })
      const answer = result.data.messages.at(-1)
      return { id: item.id, model: result.data.model, reasoningCharacters: answer.reasoning_content?.length || 0, replyCharacters: answer.content.length }
    })
    assert.equal(metadata.reasoningCharacters, reasoningCharacters)
    await page.reload()
    await page.locator('.workspace-page').waitFor()
    await page.getByRole('button', { name: '对话记录', exact: true }).click()
    await page.locator('.history-open').filter({ hasText: '界面功能验证：' }).first().click()
    await page.locator('.reasoning-disclosure').waitFor()
    assert.equal(await page.locator('.reasoning-disclosure').evaluate(element => element.open), false)
    const dir = path.resolve(__dirname, '../artifacts')
    await page.screenshot({ path: path.join(dir, 'windows-reasoning-collapsed.png') })
    await page.locator('.reasoning-disclosure summary').focus()
    await page.keyboard.press('Enter')
    assert.equal(await page.locator('.reasoning-disclosure').evaluate(element => element.open), true)
    await page.screenshot({ path: path.join(dir, 'windows-reasoning-expanded.png') })
    assert.deepEqual(failures, [])
    const result = { passed: true, environment: 'Windows Electron -> Arch HTTPS -> configured provider',
      ...metadata, checks: ['real provider reasoning persisted', 'collapsed by default', 'click expand/collapse', 'keyboard expand', 'restore from saved history'], rendererErrors: 0 }
    fs.writeFileSync(path.join(dir, 'reasoning-check.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result))
  } finally { await browser.close() }
})().catch(() => { console.error(JSON.stringify({ passed: false, message: 'Reasoning verification failed; inspect UI without logging credentials.' })); process.exitCode = 1 })

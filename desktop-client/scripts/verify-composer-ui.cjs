const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { chromium } = require('../../session_research/runtime/node_modules/playwright-core')

;(async () => {
  const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', headless: true })
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  try {
    await page.addInitScript(() => {
      let reply
      const user = { id: 'composer-test', username: '示例用户', role: 'employee', workspace_id: 'fixture', permissions: [] }
      window.__reply = success => reply(success)
      window.desktop = {
        auth: { current: async () => ({ ok: true, data: { user, expires_at: 9999999999, remember: false } }) },
        preferences: { read: async () => ({ remember: false, account: '' }), write: async () => {} },
        service: { request: async (action, value) => {
          if (action === 'chat.status') return { ok: true, data: { available: true, name: 'AI 对话', thinking_controls: true } }
          if (action === 'chat.list') return { ok: true, data: { conversations: [] } }
          if (action === 'chat.send') {
            const success = await new Promise(resolve => { reply = resolve })
            return success ? { ok: true, data: { conversation: { id: 'composer-test', title: '输入框验证', user_id: user.id, workspace_id: user.workspace_id,
              created_at: 0, updated_at: 0, model: 'fixture', provider_name: 'fixture', messages: [
                { id: 'user', role: 'user', content: value.message, created_at: 0 },
                { id: 'assistant', role: 'assistant', content: '已完成。', created_at: 0 },
              ] } } } : { ok: false, message: '界面测试：请求失败' }
          }
          throw new Error('Unexpected fixture request')
        } },
        openExternal: async () => {}, minimize() {}, maximize() {}, close() {},
      }
    })
    await page.goto('http://127.0.0.1:4173')
    const form = page.locator('.chat-composer'), editor = page.locator('#chat-message')
    await page.getByRole('switch', { name: '深度思考', exact: true }).waitFor()
    const idleHeight = await form.evaluate(element => element.getBoundingClientRect().height)
    assert(idleHeight >= 70 && idleHeight <= 100)
    assert.equal(await page.locator('.composer-footnote').count(), 0)
    const bottomGap = await form.evaluate(element => innerHeight - element.getBoundingClientRect().bottom)
    assert(bottomGap >= 0 && bottomGap <= 12, 'composer should sit near the bottom in a new conversation')
    assert.equal(await page.locator('.magic-shine-border').count(), 0)
    await editor.fill('第一行\n第二行\n第三行\n第四行')
    assert(await editor.evaluate(element => element.clientHeight) > 28)
    await editor.fill('输入框动效验证')
    await editor.press('Shift+Enter')
    assert((await editor.inputValue()).includes('\n'))
    await editor.press('Enter')
    await page.locator('.chat-composer[aria-busy=true]').waitFor()
    const border = page.locator('.magic-shine-border')
    await border.waitFor()
    assert.equal(await border.count(), 1)
    assert.equal(await border.evaluate(element => getComputedStyle(element).animationName), 'galaxy-shine')
    assert.equal(await border.evaluate(element => getComputedStyle(element).pointerEvents), 'none')
    const firstPosition = await border.evaluate(element => getComputedStyle(element).backgroundPosition)
    await page.waitForTimeout(220)
    assert.notEqual(await border.evaluate(element => getComputedStyle(element).backgroundPosition), firstPosition)
    assert.equal(await editor.isDisabled(), true)
    const dir = path.resolve(__dirname, '../artifacts')
    await page.screenshot({ path: path.join(dir, 'composer-busy-preview.png') })
    await page.evaluate(() => window.__reply(true))
    await page.locator('.chat-composer[aria-busy=false]').waitFor()
    assert(await form.evaluate(element => innerHeight - element.getBoundingClientRect().bottom) <= 12)
    assert.equal(await border.count(), 0)
    assert.equal(await editor.isDisabled(), false)
    await page.screenshot({ path: path.join(dir, 'composer-idle-preview.png') })
    await editor.fill('失败后也要关闭动画')
    await editor.press('Enter')
    await border.waitFor()
    await page.evaluate(() => window.__reply(false))
    await page.locator('.chat-failure').waitFor()
    assert.equal(await border.count(), 0)
    await page.getByRole('button', { name: '重试', exact: true }).click()
    await border.waitFor()
    await page.emulateMedia({ reducedMotion: 'reduce' })
    assert.equal(await border.evaluate(element => getComputedStyle(element).animationName), 'none')
    await page.evaluate(() => window.__reply(true))
    await page.locator('.chat-composer[aria-busy=false]').waitFor()
    await page.setViewportSize({ width: 760, height: 820 })
    await editor.fill(Array(20).fill('多行输入').join('\n'))
    assert.equal(await editor.evaluate(element => element.clientHeight), 144)
    assert.equal(await page.locator('.chat-panel').evaluate(element => element.scrollWidth > element.clientWidth + 1), false)
    assert.deepEqual(errors, [])
    const result = { passed: true, fixturesOnly: true, idleHeight, rendererErrors: 0,
      checks: ['compact initial height', 'multiline auto growth', 'Shift+Enter newline', 'Enter submit', 'animated rainbow while pending', 'success/error cleanup', 'retry', 'reduced motion', '760px layout'] }
    fs.writeFileSync(path.join(dir, 'composer-ui-check.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result))
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })

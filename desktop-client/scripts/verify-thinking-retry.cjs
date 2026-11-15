const fs = require('node:fs')
const path = require('node:path')
const http = require('node:http')
const assert = require('node:assert/strict')
const { chromium } = require('../../session_research/runtime/node_modules/playwright-core')

;(async () => {
  const dist = path.resolve(__dirname, '../dist')
  const server = http.createServer((req, res) => {
    const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname)
    const file = path.resolve(dist, '.' + (relative === '/' ? '/index.html' : relative))
    if (!file.startsWith(dist + path.sep)) { res.writeHead(404); res.end(); return }
    try {
      const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.mp4': 'video/mp4' }
      res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream')
      res.end(fs.readFileSync(file))
    } catch { res.writeHead(404); res.end() }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const browser = await chromium.launch({ executablePath: '/usr/sbin/chromium', headless: true })
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 820 } })
    await context.addInitScript(() => {
      const user = { id: localStorage.getItem('fixture.current') || 'employee-a', username: '演示员工', workspace_id: 'fixture', role: 'employee', permissions: ['workspace:read', 'ai:chat'] }
      window.__requests = []
      window.desktop = {
        auth: { current: async () => ({ ok: true, data: { user } }) },
        preferences: { read: async () => ({ remember: false, account: '' }), write: async () => {} },
        minimize() {}, maximize() {}, close() {},
        service: { request: async (action, value) => {
          if (action === 'chat.status') return { ok: true, data: { available: true, name: 'DeepSeek', model: 'deepseek-flash', thinking_controls: true } }
          if (action === 'chat.list') return { ok: true, data: { conversations: [] } }
          if (action === 'chat.send') {
            window.__requests.push(value)
            const attempt = Number(localStorage.getItem('fixture.attempts') || '0') + 1
            localStorage.setItem('fixture.attempts', String(attempt))
            await new Promise(resolve => setTimeout(resolve, 40))
            if (attempt === 1) return { ok: false, message: '模拟连接失败，请重试。' }
            return { ok: true, data: { conversation: { id: 'fixture-chat', title: value.message, messages: [
              { id: 'u', role: 'user', content: value.message }, { id: 'a', role: 'assistant', content: '已完成重试。' }] } } }
          }
          return { ok: false, message: 'unsupported fixture action' }
        } },
      }
    })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    await page.goto('http://127.0.0.1:' + server.address().port)
    const toggle = page.getByRole('switch', { name: '深度思考', exact: true })
    const effort = page.getByRole('combobox', { name: '思考强度', exact: true })
    await toggle.waitFor()
    assert.equal(await toggle.getAttribute('aria-checked'), 'true')
    assert.equal(await effort.inputValue(), 'high')
    await effort.selectOption('max')
    await toggle.click(); assert.equal(await effort.isDisabled(), true)
    await toggle.click(); assert.equal(await effort.inputValue(), 'max')
    await page.locator('#chat-message').fill('生成日报对比 Excel')
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await page.locator('.chat-failure').waitFor()
    assert((await page.locator('.chat-failure').innerText()).includes('模拟连接失败'))
    assert.equal(await page.locator('.user .message-text').innerText(), '生成日报对比 Excel')
    assert.equal(await page.locator('.welcome').count(), 0)
    await page.getByRole('button', { name: '编辑问题', exact: true }).click()
    assert.equal(await page.locator('#chat-message').inputValue(), '生成日报对比 Excel')
    assert.equal(await page.locator('.chat-failure').count(), 0)
    // Force one more failure to exercise the Retry path independently of Edit.
    await page.evaluate(() => localStorage.setItem('fixture.attempts', '0'))
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await page.locator('.chat-failure').waitFor()
    await effort.selectOption('low')
    await page.getByRole('button', { name: '重试', exact: true }).click()
    await page.locator('.assistant .message-text').filter({ hasText: '已完成重试。' }).waitFor()
    assert.equal(await page.locator('.chat-failure').count(), 0)
    const requests = await page.evaluate(() => window.__requests)
    assert.equal(requests.length, 3)
    assert.equal(requests[0].thinking, true); assert.equal(requests[0].reasoning_effort, 'max')
    assert.equal(requests[2].reasoning_effort, 'low')
    assert.equal(requests[2].message, requests[0].message)
    await toggle.click(); await page.reload(); await toggle.waitFor()
    assert.equal(await toggle.getAttribute('aria-checked'), 'false')
    assert.equal(await effort.inputValue(), 'low')
    await page.evaluate(() => localStorage.setItem('fixture.current', 'employee-b'))
    await page.reload(); await toggle.waitFor()
    assert.equal(await toggle.getAttribute('aria-checked'), 'true')
    assert.equal(await effort.inputValue(), 'high')
    assert.deepEqual(errors, [])
    const report = { passed: true, thinkingToggle: true, threeEfforts: true, disabledWhenOff: true,
      requestParameters: true, failureKeepsQuestion: true, edit: true, retryUsesCurrentSettings: true,
      preferencesSurviveReload: true, preferencesIsolatedByUser: true, rendererErrors: 0 }
    fs.writeFileSync(path.resolve(__dirname, '../artifacts/thinking-retry-ui-check.json'), JSON.stringify(report, null, 2))
    console.log(JSON.stringify(report))
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)) }
})().catch(() => { console.error(JSON.stringify({ passed: false, message: 'Thinking/retry renderer check failed' })); process.exitCode = 1 })

// Windows UI -> real Arch backend -> local test upstream. Cleans up its own fixtures.
const fs = require('node:fs')
const path = require('node:path')
const https = require('node:https')
const assert = require('node:assert/strict')
const { chromium } = require('../../session_research/runtime/node_modules/playwright-core')
const root = path.resolve(__dirname, '../..')
const lines = fs.readFileSync(path.join(root, '1.txt'), 'utf8').replace(/^\uFEFF/, '').split(/\r?\n/)
const accounts = [{ username: lines[0], password: lines[1] }, { username: lines[2], password: lines[3] }]
const agent = new https.Agent({ ca: fs.readFileSync(path.join(root, 'desktop-client/electron/certs/server.crt')) })
const marker = 'qa-ui-' + Date.now()
const artifacts = path.join(root, 'desktop-client/artifacts')
let baseline

function api(method, route, body, token) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null
    const request = https.request(new URL(route, 'https://192.168.122.1:8443'), { method, agent,
      headers: { ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {}),
        ...(token ? { Authorization: 'Bearer ' + token } : {}) } }, response => {
      const chunks = []
      response.on('data', chunk => chunks.push(chunk))
      response.on('end', () => {
        if (response.statusCode >= 300) return reject(new Error('Fixture cleanup API failed'))
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')))
      })
    })
    request.setTimeout(15000, () => request.destroy())
    request.on('error', () => reject(new Error('Fixture cleanup network failed')))
    if (payload) request.write(payload)
    request.end()
  })
}

async function cleanup() {
  for (let index = 0; index < accounts.length; index++) {
    const auth = await api('POST', '/api/v1/auth/login', { ...accounts[index], remember: false })
    try {
      const data = await api('GET', '/api/v1/chat/conversations', null, auth.access_token)
      for (const conversation of data.conversations.filter(item => item.title.startsWith(marker)))
        await api('DELETE', '/api/v1/chat/conversations/' + conversation.id, null, auth.access_token)
      if (index === 0 && baseline) {
        const config = await api('GET', '/api/v1/admin/ai', null, auth.access_token)
        for (const provider of config.providers.filter(item => item.name === marker))
          await api('DELETE', '/api/v1/admin/ai/providers/' + provider.id, null, auth.access_token)
        if (config.global_prompt === 'UI fixture global prompt')
          await api('PUT', '/api/v1/admin/ai/prompt', { prompt: baseline.global_prompt }, auth.access_token)
        if (baseline.default_provider_id)
          await api('PUT', '/api/v1/admin/ai/default', { provider_id: baseline.default_provider_id }, auth.access_token)
      }
    } finally { await api('POST', '/api/v1/auth/logout', null, auth.access_token) }
  }
}

async function login(page, index) {
  await page.locator('#account').waitFor()
  await page.waitForFunction(() => !document.querySelector('#account').disabled)
  await page.locator('#account').fill(accounts[index].username)
  await page.locator('#password').fill(accounts[index].password)
  await page.getByRole('checkbox', { name: '记住登录' }).setChecked(index === 0, { force: true })
  await page.locator('.login-button').click()
  await page.locator('.workspace-page').waitFor()
}

async function logout(page) {
  await page.getByRole('button', { name: '账户菜单', exact: true }).click()
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await page.locator('#account').waitFor()
}

;(async () => {
  const browser = await chromium.connectOverCDP(process.env.UI_CDP_URL || 'http://127.0.0.1:19224')
  const page = browser.contexts()[0].pages().find(value => value.url().includes('index.html'))
  assert(page)
  page.setDefaultTimeout(15000)
  const failures = []
  page.on('pageerror', () => failures.push('renderer error'))
  try {
    await page.waitForFunction(() => !!document.querySelector('.workspace-page') || !!(document.querySelector('#account') && !document.querySelector('#account').disabled))
    if (await page.locator('#account').count()) await login(page, 0)
    const current = await page.evaluate(() => window.desktop.auth.current())
    if (current.data.user.role !== 'admin') { await logout(page); await login(page, 0) }
    const initial = await page.evaluate(() => window.desktop.service.request('admin.settings'))
    assert.equal(initial.ok, true)
    baseline = initial.data
    // The test only provisions its fixture when no real model has been configured yet.
    assert.equal(baseline.providers.length, 0)
    await page.getByRole('button', { name: '控制台', exact: true }).click()
    await page.locator('.console-page-heading').waitFor()
    await page.getByRole('button', { name: '添加 AI', exact: true }).click()
    await page.locator('#provider-name').fill(marker)
    await page.locator('#provider-model').fill('ui-fixture-model')
    await page.locator('#provider-base-url').fill('http://127.0.0.1:18937/v1')
    await page.locator('#provider-api-key').fill('ui-fixture-disposable-key')
    await page.getByRole('button', { name: '保存配置', exact: true }).click()
    await page.locator('.provider-card').waitFor()
    await page.getByRole('button', { name: '测试连接', exact: true }).click()
    await page.getByRole('status').filter({ hasText: '连接成功' }).waitFor()
    await page.getByRole('button', { name: '编辑 ' + marker, exact: true }).click()
    assert.equal(await page.locator('#provider-api-key').inputValue(), '')
    await page.getByRole('button', { name: '保存配置', exact: true }).click()
    await page.getByRole('dialog').waitFor({ state: 'hidden' })
    await page.getByRole('button', { name: '测试连接', exact: true }).click()
    await page.getByRole('status').filter({ hasText: '连接成功' }).waitFor()
    await page.getByRole('tab', { name: '全局提示词', exact: true }).click()
    assert.equal(await page.locator('.provider-grid').count(), 0)
    await page.locator('#global-prompt').fill('UI fixture global prompt')
    await page.getByRole('button', { name: '保存提示词', exact: true }).click()
    await page.getByRole('status').filter({ hasText: '全局提示词已保存' }).waitFor()
    await page.getByRole('tab', { name: 'AI 模型', exact: true }).click()
    assert.equal(await page.locator('#global-prompt').count(), 0)
    const settings = await page.evaluate(() => window.desktop.service.request('admin.settings'))
    assert.equal(settings.data.providers[0].has_api_key, true)
    assert.equal('api_key' in settings.data.providers[0], false)
    assert.equal('encrypted_api_key' in settings.data.providers[0], false)
    await page.getByRole('button', { name: '控制台 有赞', exact: true }).click()
    await page.getByRole('heading', { name: '有赞', exact: true }).waitFor()
    await page.screenshot({ path: path.join(artifacts, 'windows-console-youzan.png') })
    await page.getByRole('button', { name: 'AI', exact: true }).click()
    const firstMessage = marker + ' 第一条消息'
    await page.locator('#chat-message').fill(firstMessage)
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await page.locator('.assistant .message-text').filter({ hasText: '联调回复：' + firstMessage }).waitFor()
    await page.locator('#chat-message').fill(marker + ' 第二条消息')
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await page.waitForFunction(() => document.querySelectorAll('.assistant .message-text').length === 2)
    const conversations = await page.evaluate(() => window.desktop.service.request('chat.list'))
    const adminConversation = conversations.data.conversations.find(item => item.title.startsWith(marker))
    assert(adminConversation)
    await page.getByRole('button', { name: '新建对话', exact: true }).click()
    await page.locator('#welcome-heading').waitFor()
    await page.getByRole('button', { name: '对话记录', exact: true }).click()
    await page.locator('.history-open').filter({ hasText: marker }).click()
    await page.waitForFunction(() => document.querySelectorAll('.assistant .message-text').length === 2)
    assert.equal(await page.locator('.message-avatar img').first().getAttribute('src'), './ai.png')
    await logout(page)
    await login(page, 1)
    assert.equal(await page.getByRole('button', { name: '控制台', exact: true }).count(), 0)
    const denied = await page.evaluate(() => window.desktop.service.request('admin.settings'))
    assert.equal(denied.ok, false)
    const foreign = await page.evaluate(id => window.desktop.service.request('chat.get', { id }), adminConversation.id)
    assert.equal(foreign.ok, false)
    await page.locator('#chat-message').fill(marker + ' 员工消息')
    await page.getByRole('button', { name: '发送消息', exact: true }).click()
    await page.locator('.assistant .message-text').filter({ hasText: '联调回复' }).waitFor()
    await logout(page)
    await login(page, 0)
    await cleanup()
    baseline = null
    await page.getByRole('button', { name: '控制台', exact: true }).click()
    await page.getByRole('heading', { name: '添加第一个 AI', exact: true }).waitFor()
    await page.screenshot({ path: path.join(artifacts, 'windows-console-ai.png') })
    assert.deepEqual(failures, [])
    const result = { passed: true, environment: 'Windows Electron -> Arch HTTPS -> local fixture',
      checks: ['provider create/edit and encrypted key retention', 'one-click model test', 'global prompt injection', 'real request/reply UI',
        'multi-turn context and saved history', 'AI avatar', 'employee console denial', 'cross-user conversation denial', 'fixture cleanup'], rendererErrors: 0 }
    fs.writeFileSync(path.join(artifacts, 'console-check.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result))
  } finally {
    try { if (baseline) await cleanup() } finally { await browser.close() }
  }
})().catch(() => { console.error(JSON.stringify({ passed: false, message: 'Console verification failed; inspect UI without logging credentials.' })); process.exitCode = 1 })

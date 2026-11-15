const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { chromium } = require('../../session_research/runtime/node_modules/playwright-core')

;(async () => {
  const browser = await chromium.launch({ executablePath: '/usr/bin/chromium', headless: true })
  const page = await browser.newPage({ viewport: { width: 1280, height: 920 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  try {
    await page.addInitScript(() => {
      const categories = ['excel', 'file', 'general', 'youzan'].map(category => ({ category, tool_count: category === 'youzan' ? 8 : 1,
        prompt: category === 'youzan' ? '默认查询全部授权店铺，逐店使用独立会话，分别注明来源。' : '' }))
      const settings = { configured: true, account: '示例商家', kdt_id: '111', has_password: true, auto_refresh: false,
        sid: { present: true, expired: false, updated_at: 1 }, csrf: { present: true, updated_at: 1 },
        shops: [{ id: '111', name: '示例总部' }, { id: '222', name: '示例分店' }], multi_shop: false,
        shop_sessions: [{ kdt_id: '111', name: '示例总部', ready: false }, { kdt_id: '222', name: '示例分店', ready: false }], job: null }
      window.__globalPrompt = '通用身份规则，不含工具说明。'
      window.__globalWrites = 0
      window.__prepareCount = 0
      window.desktop = {
        auth: { current: async () => ({ ok: true, data: { user: { id: 'fixture-admin', workspace_id: 'fixture', username: '示例管理员', role: 'admin', permissions: ['ai:configure', 'youzan:configure'] }, expires_at: 9999999999, remember: false } }) },
        preferences: { read: async () => ({ account: '', remember: false }), write: async () => {} },
        service: { request: async (action, value) => {
          let data
          if (action === 'chat.status') data = { available: false }
          else if (action === 'admin.settings') data = { providers: [], default_provider_id: null, global_prompt: window.__globalPrompt }
          else if (action === 'admin.toolPrompts') data = { categories: structuredClone(categories) }
          else if (action === 'admin.saveToolPrompt') { const item = categories.find(item => item.category === value.category); item.prompt = value.prompt.trim() ? value.prompt : ''; data = { category: item.category, prompt: item.prompt } }
          else if (action === 'admin.prompt') { window.__globalWrites++; throw new Error('Category editor must not write global prompt') }
          else if (action === 'youzan.settings' || action === 'youzan.shops') {
            if (settings.job?.status === 'running') {
              settings.multi_shop = true
              settings.shop_sessions.forEach(shop => { shop.ready = true; shop.updated_at = 1 })
              settings.job = { id: 'fixture-job', kind: 'all', status: 'success', code: 'all_ready', message: '全部店铺的 CRM 会话已就绪。', finished_at: 1 }
            }
            data = structuredClone(settings)
          } else if (action === 'youzan.refresh') {
            if (value.kind !== 'all') throw new Error('Only CRM preparation should be triggered')
            window.__prepareCount++
            settings.job = { id: 'fixture-job', kind: 'all', status: 'running', code: 'all_csrf', message: '正在逐店获取 CRM 会话。' }
            data = structuredClone(settings)
          } else throw new Error('Unexpected fixture action: ' + action)
          return { ok: true, data }
        } },
        minimize() {}, maximize() {}, close() {}, openExternal: async () => {},
      }
    })
    await page.goto('http://127.0.0.1:4173')
    await page.getByRole('button', { name: '控制台', exact: true }).click()
    await page.getByRole('tab', { name: '工具提示词', exact: true }).click()
    const editor = page.getByRole('textbox', { name: '分类提示词', exact: true })
    await editor.waitFor()
    assert.equal(await page.locator('.tools-categories button').count(), 4)
    assert((await editor.inputValue()).includes('默认查询全部授权店铺'))
    await editor.fill('用户指定店铺时只查指定范围；未指定时覆盖全部授权店铺。')
    await page.getByRole('button', { name: '保存', exact: true }).click()
    await page.getByRole('status').filter({ hasText: '分类提示词已保存' }).waitFor()
    await page.locator('.tools-categories button').filter({ hasText: 'file' }).click()
    assert.equal(await editor.inputValue(), '')
    await editor.fill('临时文件说明')
    await page.getByRole('button', { name: '保存', exact: true }).click()
    await page.getByRole('status').filter({ hasText: '分类提示词已保存' }).waitFor()
    await editor.fill('')
    await page.getByRole('button', { name: '保存', exact: true }).click()
    await page.getByRole('status').filter({ hasText: '该分类不附加提示词' }).waitFor()
    assert.equal(await page.evaluate(() => window.__globalWrites), 0)
    await page.locator('.tools-categories button').filter({ hasText: 'youzan' }).click()
    assert((await editor.inputValue()).includes('用户指定店铺'))
    const dir = path.resolve(__dirname, '../artifacts')
    await page.screenshot({ path: path.join(dir, 'tool-prompts-preview.png') })
    await page.getByRole('button', { name: '控制台 有赞', exact: true }).click()
    await page.waitForFunction(() => window.__prepareCount === 1)
    await page.waitForFunction(() => document.querySelectorAll('.shop-session-row .ready').length === 2)
    assert.equal(await page.evaluate(() => window.__prepareCount), 1)
    assert.equal(await page.locator('#youzan-shop').inputValue(), '111')
    await page.screenshot({ path: path.join(dir, 'shop-sessions-preview.png') })
    assert.deepEqual(errors, [])
    const result = { passed: true, fixturesOnly: true, categoryCount: 4, globalPromptUnchanged: true, clearDisablesCategoryPrompt: true,
      automaticPreparationCount: 1, perShopStatusVisible: true, selectedShopUnchanged: true, rendererErrors: 0 }
    fs.writeFileSync(path.join(dir, 'tool-prompts-ui-check.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result))
  } finally { await browser.close() }
})().catch(error => { console.error(error.message); process.exitCode = 1 })

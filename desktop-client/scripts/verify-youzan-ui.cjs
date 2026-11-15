// Exercise the actual Windows renderer and Arch routes without submitting Youzan credentials.
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
  const artifacts = path.resolve(__dirname, '../artifacts')
  try {
    await page.waitForFunction(() => !!document.querySelector('.workspace-page') || !!(document.querySelector('#account') && !document.querySelector('#account').disabled))
    if (await page.locator('.workspace-page').count()) {
      await page.getByRole('button', { name: '账户菜单', exact: true }).click()
      await page.getByRole('button', { name: '退出登录', exact: true }).click()
    }
    await page.locator('.login-brand').waitFor()
    assert.equal(await page.locator('.login-brand').innerText(), 'galaxy ai')
    await page.locator('#account').fill('')
    await page.locator('#password').fill('')
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.login-card')).opacity === '1')
    await page.screenshot({ path: path.join(artifacts, 'windows-galaxy-ai-login.png') })
    const lines = fs.readFileSync(path.resolve(__dirname, '../../1.txt'), 'utf8').replace(/^\uFEFF/, '').split(/\r?\n/)
    await page.locator('#account').fill(lines[0])
    await page.locator('#password').fill(lines[1])
    await page.getByRole('checkbox', { name: '记住登录' }).check({ force: true })
    await page.locator('.login-button').click()
    await page.locator('.workspace-page').waitFor()
    assert.equal(await page.title(), 'galaxy ai')
    await page.getByRole('button', { name: '控制台', exact: true }).click()
    await page.getByRole('button', { name: '控制台 有赞', exact: true }).click()
    await page.locator('#youzan-account').waitFor()
    assert.equal(await page.locator('#youzan-password').getAttribute('type'), 'password')
    const snapshot = await page.evaluate(async () => {
      const response = await window.desktop.service.request('youzan.settings')
      return { ok: response.ok, configured: response.data?.configured, autoRefresh: response.data?.auto_refresh,
        sidPresent: response.data?.sid?.present, csrfPresent: response.data?.csrf?.present,
        secretsExposed: response.data ? ['password','secret','profile'].some(key => Object.hasOwn(response.data, key)) : true }
    })
    assert.equal(snapshot.ok, true)
    assert.equal(snapshot.autoRefresh, false)
    assert.equal(snapshot.secretsExposed, false)
    assert.equal(await page.getByRole('button', { name: '仅刷新 CRM CSRF', exact: true }).count(), 1)
    if (!snapshot.configured) {
      assert.equal(await page.getByRole('button', { name: '刷新 SID', exact: true }).isDisabled(), true)
      assert.equal(await page.getByRole('button', { name: '仅刷新 CRM CSRF', exact: true }).isDisabled(), true)
      // HTML validation prevents saving an empty connection; no Youzan login request is made.
      await page.getByRole('button', { name: '保存配置', exact: true }).click()
      assert.equal(await page.locator('#youzan-account').evaluate(input => input.validity.valueMissing), true)
    }
    await page.locator('#youzan-account').blur()
    await page.screenshot({ path: path.join(artifacts, 'windows-galaxy-ai-youzan.png') })
    const layout = await page.evaluate(() => {
      const scroll = document.querySelector('.console-scroll')
      const cards = [...document.querySelectorAll('.youzan-credential-card')]
      return { horizontalOverflow: scroll.scrollWidth > scroll.clientWidth + 1, credentialCards: cards.length,
        buttonsWithinCards: cards.every(card => {
          const box=card.getBoundingClientRect(), button=card.querySelector('button').getBoundingClientRect()
          return button.left >= box.left && button.right <= box.right
        }) }
    })
    assert.equal(layout.horizontalOverflow, false)
    assert.equal(layout.credentialCards, 2)
    assert.equal(layout.buttonsWithinCards, true)
    assert.deepEqual(failures, [])
    const result = { passed: true, environment: 'Actual Windows GalaxyAI.exe -> Arch HTTPS', ...snapshot, ...layout,
      checks: ['login branding', 'masked password input', 'manual refresh controls', 'Arch settings endpoint', 'empty configuration validation', 'no automatic Youzan login'], rendererErrors: 0 }
    fs.writeFileSync(path.join(artifacts, 'youzan-ui-check.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result))
  } finally { await browser.close() }
})().catch(() => { console.error(JSON.stringify({ passed: false, message: 'Youzan UI verification failed; inspect UI without printing credentials.' })); process.exitCode = 1 })

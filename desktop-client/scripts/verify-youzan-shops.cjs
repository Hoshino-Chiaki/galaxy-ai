// Real Windows / Arch check using the saved SID. Never submit Youzan login credentials.
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const { chromium } = require('../../session_research/runtime/node_modules/playwright-core')

;(async () => {
  const browser = await chromium.connectOverCDP(process.env.UI_CDP_URL || 'http://127.0.0.1:19224')
  const page = browser.contexts()[0].pages().find(value => value.url().includes('index.html'))
  assert(page)
  const errors = []
  page.on('pageerror', () => errors.push('renderer error'))
  const dir = path.resolve(__dirname, '../artifacts')
  try {
    await page.waitForFunction(() => !!document.querySelector('.workspace-page') || !!(document.querySelector('#account') && !document.querySelector('#account').disabled))
    if (await page.locator('#account').count()) {
      const lines = fs.readFileSync(path.resolve(__dirname, '../../1.txt'), 'utf8').replace(/^\uFEFF/, '').split(/\r?\n/)
      await page.locator('#account').fill(lines[0]); await page.locator('#password').fill(lines[1])
      await page.getByRole('checkbox', { name: '记住登录' }).check({ force: true })
      await page.locator('.login-button').click()
      await page.locator('.workspace-page').waitFor()
    }
    await page.getByRole('button', { name: '控制台', exact: true }).click()
    await page.getByRole('button', { name: '控制台 有赞', exact: true }).click()
    await page.locator('#youzan-shop').waitFor()
    await page.waitForFunction(() => document.querySelector('#youzan-shop').options.length > 1 && !document.querySelector('#youzan-shop').disabled, undefined, { timeout: 35000 })
    assert.equal(await page.locator('#youzan-kdt').count(), 0)
    assert.equal(await page.locator('#youzan-shop').evaluate(e => e.tagName), 'SELECT')
    const initial = await page.evaluate(async () => (await window.desktop.service.request('youzan.settings')).data)
    assert(initial.sid.present && initial.kdt_id && initial.shops.length > 0)
    assert.equal(await page.locator('#youzan-shop').inputValue(), initial.kdt_id)
    assert.deepEqual(await page.locator('#youzan-shop option').allTextContents(), ['请选择要连接的店铺', ...initial.shops.map(shop => shop.name)])
    assert.equal(initial.auto_refresh, false)
    await page.locator('.console-scroll').evaluate(e => { e.scrollTop = 0 })
    await page.screenshot({ path: path.join(dir, 'windows-youzan-shop-selector.png') })
    // Explicitly run the authorized CRM refresh to verify the corrected real redirect chain.
    await page.getByRole('button', { name: '仅刷新 CRM CSRF', exact: true }).click()
    await page.waitForFunction(() => !!document.querySelector('.youzan-job.success'), undefined, { timeout: 45000 })
    const final = await page.evaluate(async () => (await window.desktop.service.request('youzan.settings')).data)
    assert.equal(final.job.kind, 'csrf')
    assert.equal(final.job.code, 'csrf_success')
    assert.equal(final.sid.updated_at, initial.sid.updated_at)
    assert.equal(final.kdt_id, initial.kdt_id)
    assert.equal(final.csrf.present, true)
    assert.equal(Object.hasOwn(final, 'password'), false)
    assert.equal(Object.hasOwn(final.sid, 'secret'), false)
    assert.equal(Object.hasOwn(final.csrf, 'secret'), false)
    await page.locator('.youzan-job').scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(dir, 'windows-youzan-crm-refreshed.png') })
    await page.locator('.console-scroll').evaluate(e => { e.scrollTop = 0 })
    assert.deepEqual(errors, [])
    const result = { passed: true, environment: 'Actual Windows client -> Arch -> live Youzan', shopCount: final.shops.length,
      manualIdInputRemoved: true, selectedShopPreserved: true, csrfRefreshed: true, savedSidUnchanged: true,
      jobKind: final.job.kind, jobCode: final.job.code, autoRefresh: false, rendererErrors: 0 }
    fs.writeFileSync(path.join(dir, 'youzan-shops-check.json'), JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result))
  } finally { await browser.close() }
})().catch(() => { console.error(JSON.stringify({ passed: false, message: 'Shop selector / CRM live check failed; inspect UI without printing credentials.' })); process.exitCode = 1 })

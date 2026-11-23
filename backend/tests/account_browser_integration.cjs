// Synthetic origins/cookies only. Network pages are intercepted; no platform login is submitted.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { chromium } = require('/opt/galaxy-ai/backend/browser/node_modules/playwright-core');
(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'galaxy-account-browser-'));
  let context;
  const open = () => chromium.launchPersistentContext(profile, { executablePath: '/usr/sbin/chromium',
    headless: false, viewport: { width: 1280, height: 900 },
    env: { ...process.env, HOME: profile, XDG_CONFIG_HOME: path.join(profile, 'config'), XDG_CACHE_HOME: path.join(profile, 'cache') } });
  try {
    context = await open();
    await context.addCookies([
      { name: 'fixture-youzan', value: 'fixture', domain: '.youzan.com', path: '/', expires: Date.now() / 1000 + 3600 },
      { name: 'fixture-other', value: 'fixture', domain: 'example.invalid', path: '/', expires: Date.now() / 1000 + 3600 },
    ]);
    let page = context.pages()[0] || await context.newPage();
    await page.route('**/*', route => route.fulfill({ status: 200, contentType: 'text/html', body: '<html>fixture</html>' }));
    await page.goto('https://account.youzan.com/login');
    await page.evaluate(() => localStorage.setItem('0876303504F93E80', 'fixture-youzan-state'));
    await page.goto('https://example.invalid');
    await page.evaluate(() => localStorage.setItem('fixture-other-channel', 'keep-this-state'));
    await context.close(); context = null;
    const child = spawnSync(process.execPath, [path.join(__dirname, '../youzan/reset-channel.cjs')], {
      input: JSON.stringify({ profile }), encoding: 'utf8', timeout: 30000,
      env: { ...process.env, HOME: profile, XDG_CONFIG_HOME: path.join(profile, 'config'), XDG_CACHE_HOME: path.join(profile, 'cache') },
    });
    assert.equal(child.status, 0, 'Channel reset helper failed');
    context = await open();
    const cookies = await context.cookies();
    assert(!cookies.some(cookie => cookie.name === 'fixture-youzan'));
    assert(cookies.some(cookie => cookie.name === 'fixture-other'));
    page = context.pages()[0] || await context.newPage();
    await page.route('**/*', route => route.fulfill({ status: 200, contentType: 'text/html', body: '<html>fixture</html>' }));
    await page.goto('https://account.youzan.com/login');
    assert.equal(await page.evaluate(() => localStorage.getItem('0876303504F93E80')), null);
    await page.goto('https://example.invalid');
    assert.equal(await page.evaluate(() => localStorage.getItem('fixture-other-channel')), 'keep-this-state');
    console.log(JSON.stringify({ shared_browser_program: true, one_persistent_profile: true,
      youzan_account_reset: true, unrelated_channel_cookies_preserved: true, unrelated_local_storage_preserved: true }));
  } finally {
    if (context) await context.close();
    fs.rmSync(profile, { recursive: true, force: true });
  }
})().catch(error => { console.error(error.name + ': ' + error.message); process.exitCode = 1; });

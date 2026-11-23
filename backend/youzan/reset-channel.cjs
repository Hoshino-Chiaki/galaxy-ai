// Reset only Youzan's site data when this Galaxy user explicitly changes its Youzan account.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../browser/node_modules/playwright-core');
const belongs = host => host === 'youzan.com' || host.endsWith('.youzan.com') ||
  host === 'youzanyun.com' || host.endsWith('.youzanyun.com');
(async () => {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  if (typeof input.profile !== 'string' || !path.isAbsolute(input.profile)) throw Error('Invalid profile');
  const context = await chromium.launchPersistentContext(input.profile, {
    executablePath: '/usr/sbin/chromium', headless: false, viewport: { width: 1280, height: 900 },
  });
  try {
    const state = await context.storageState();
    const page = context.pages()[0] || await context.newPage();
    const cdp = await context.newCDPSession(page);
    // A reopened persistent context need not enumerate origins visited by an older process.
    const origins = new Set(['https://account.youzan.com', 'https://www.youzan.com',
      'https://passport.youzan.com', 'https://store.youzan.com', 'https://crm.youzan.com',
      'https://risk-dfp-service-v2.youzan.com', 'https://fp.youzanyun.com']);
    for (const origin of state.origins) if (belongs(new URL(origin.origin).hostname)) origins.add(origin.origin);
    for (const origin of origins) {
      await cdp.send('Storage.clearDataForOrigin', { origin, storageTypes: 'all' });
    }
    await context.clearCookies({ domain: /(^|\.)(youzan\.com|youzanyun\.com)$/ });
    process.stdout.write(JSON.stringify({ event: 'channel_state_reset' }) + '\n');
  } finally { await context.close(); }
})().catch(() => { process.exitCode = 1; });

// Shared, server-owned browser primitive. No user-supplied launch arguments.
const { chromium } = require('./node_modules/playwright-core')
const path = require('node:path')
exports.openProfile = profile => chromium.launchPersistentContext(profile, {
  headless: false,
  viewport: { width: 1280, height: 900 },
  locale: 'zh-CN',
  timezoneId: 'Asia/Shanghai',
  acceptDownloads: false,
  env: { ...process.env, HOME: profile, XDG_CONFIG_HOME: path.join(profile, 'config'),
    XDG_CACHE_HOME: path.join(profile, 'cache') },
})

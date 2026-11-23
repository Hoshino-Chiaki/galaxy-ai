// Fixed real-browser login entry. Reuse the session; authenticate only when needed.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { spawnSync } = require('child_process');
const {createInteractiveSlider} = require('../backend/youzan/interactive-slider.cjs');
const interactiveSlider = createInteractiveSlider();

const args = process.argv.slice(2);
// Server integration keeps the tested browser/slider flow below unchanged.
// Credentials arrive through a private stdin pipe, never command-line arguments.
const interactive = args.length === 1 && args[0] === '--managed-interactive';
const managed = interactive || (args.length === 1 && args[0] === '--managed-stdin');
function readInitialLine() {
  const data = Buffer.alloc(16384);
  for (let i = 0; i < data.length; i++) {
    if (!fs.readSync(0, data, i, 1, null) || data[i] === 10) return data.subarray(0, i).toString();
  }
  process.exit(22);
}
let managedInput;
if (managed) {
  const source = interactive ? readInitialLine() : fs.readFileSync(0, 'utf8');
  if (source.length > 16384) process.exit(22);
  try { managedInput = JSON.parse(source); } catch (_) { process.exit(22); }
  if (typeof managedInput.account !== 'string' || !managedInput.account.trim() ||
      typeof managedInput.password !== 'string' || !managedInput.password ||
      typeof managedInput.profile !== 'string' || !path.isAbsolute(managedInput.profile)) process.exit(22);
}
if (args.includes('--help') || args.includes('-h')) {
  process.stdout.write('Usage: ./youzan-login.sh [--status]\n\n' +
    'Default: reuse a valid browser session, or perform one login attempt.\n' +
    '--status: check the session without submitting credentials.\n' +
    'Credentials: account_password.txt (account on line 1, password on line 2).\n' +
    'Exit codes: 0 authenticated; 10 login required; 20 SMS required;\n' +
    '21 slider rejected; 22 credentials/account mismatch; 23 runtime error;\n' +
    '24 another run holds the lock; 25 login rejected or unconfirmed.\n');
  process.exit(0);
}
if (!managed && (args.some(arg => arg !== '--status') || args.length > 1)) {
  process.stderr.write('Unknown option. Use --help.\n');
  process.exit(2);
}
const statusOnly = args.includes('--status');

const root = path.resolve(__dirname, '..');
// Local accounts keep independent browser state. Preserve the legacy profile for its owner.
function localPrivateDirectory() {
  const legacy = path.join(__dirname, 'private');
  const credentials = path.join(root, 'account_password.txt');
  if (!fs.existsSync(credentials)) return legacy;
  const selected = fs.readFileSync(credentials, 'utf8').replace(/^\uFEFF/, '').split(/\r?\n/)[0].trim();
  if (!selected) return legacy;
  try {
    const state = JSON.parse(fs.readFileSync(path.join(legacy, 'youzan-human-browser-state.json'), 'utf8'));
    const mobile = state.cookies?.find(c => c.name === 'mobile' &&
      (c.domain === 'youzan.com' || c.domain?.endsWith('.youzan.com')));
    if (mobile && decodeURIComponent(mobile.value) === selected) return legacy;
  } catch (_) { /* Unknown legacy ownership must not reuse another account's profile. */ }
  const accountKey = crypto.createHash('sha256').update(selected).digest('hex');
  return path.join(legacy, 'accounts', accountKey);
}
const privateDir = managed ? path.dirname(managedInput.profile) : localPrivateDirectory();
const profile = managed ? managedInput.profile : path.join(privateDir, 'youzan-human-browser-profile');
const reportFile = path.join(__dirname, 'youzan-login-result.json');
const runId = new Date().toISOString().replace(/[:.]/g, '-') + '-' + process.pid;
const report = { schema_version: 1, run_id: runId, started_at: new Date().toISOString(),
  mode: statusOnly ? 'status_only' : 'automatic_login', authenticated: false,
  profile_preexisting: fs.existsSync(profile), events: [], slider_submissions: 0,
  password_submissions: 0 };
let account = '';
let password = '';
const secrets = new Set();
let context;
let activePage;
let stage = 'launch';
const pendingObservations = new Set();
let sdkProfile = null;
let resolveSdkProfile;
const sdkProfileReady = new Promise(resolve => { resolveSdkProfile = resolve; });
const input = interactive ? require('node:readline').createInterface({input: process.stdin}) : null;
const actions = input?.[Symbol.asyncIterator]();
if (managed) process.once('SIGTERM', async () => {
  if (context) await context.close().catch(() => {});
  process.exit(143);
});

function writeJSON(file, value) {
  const temporary = file + '.' + crypto.randomUUID() + '.tmp';
  fs.writeFileSync(temporary, JSON.stringify(value, null, 2) + '\n', { mode: 0o600 });
  fs.renameSync(temporary, file);
  fs.chmodSync(file, 0o600);
}
function youzanDomain(domain) { return domain === 'youzan.com' || domain.endsWith('.youzan.com'); }
function configuredAccount() {
  if (managed) { secrets.add(managedInput.account.trim()); return managedInput.account.trim(); }
  const file = path.join(root, 'account_password.txt');
  if (!fs.existsSync(file)) return null;
  const value = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '').split(/\r?\n/)[0].trim();
  if (value) secrets.add(value);
  return value;
}
function loadCredentials() {
  if (managed) {
    account = managedInput.account.trim(); password = managedInput.password;
    secrets.add(account); secrets.add(password); return true;
  }
  const file = path.join(root, 'account_password.txt');
  if (!fs.existsSync(file)) return false;
  const lines = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '').split(/\r?\n/);
  if (!lines[0]?.trim() || !lines[1]) return false;
  account = lines[0].trim();
  password = lines[1];
  secrets.add(account);
  secrets.add(password);
  fs.chmodSync(file, 0o600);
  return true;
}
async function saveAuthenticatedState(page, result) {
  const cookies = await context.cookies();
  const sid = cookies.find(c => c.name === 'sid' && youzanDomain(c.domain));
  report.sid_present = !!sid;
  report.landing = endpoint(page.url());
  const protectedLanding = report.landing.host === 'www.youzan.com' &&
    report.landing.path.startsWith('/v4/');
  if (!sid || !protectedLanding) {
    report.result = 'login_unconfirmed';
    return;
  }
  const expectedAccount = configuredAccount();
  const mobile = cookies.find(c => c.name === 'mobile' && youzanDomain(c.domain));
  if (expectedAccount && mobile && decodeURIComponent(mobile.value) !== expectedAccount) {
    report.result = 'profile_account_mismatch';
    return;
  }
  report.sid_expiry = sid.expires > 0 ? new Date(sid.expires * 1000).toISOString() : null;
  if (managed) {
    // This JSON line is consumed only by the backend, encrypted before storage.
    report.authenticated = true;
    report.result = result;
    process.stdout.write(JSON.stringify({ event: 'managed_credentials', sid: sid.value,
      expires_at: sid.expires > 0 ? Math.floor(sid.expires) : null }) + '\n');
    return;
  }
  writeJSON(path.join(privateDir, 'youzan-human-browser-state.json'), await context.storageState());
  const rows = ['# Netscape HTTP Cookie File'];
  for (const cookie of cookies.filter(c => youzanDomain(c.domain))) {
    rows.push([(cookie.httpOnly ? '#HttpOnly_' : '') + cookie.domain,
      cookie.domain.startsWith('.') ? 'TRUE' : 'FALSE', cookie.path,
      cookie.secure ? 'TRUE' : 'FALSE', cookie.expires > 0 ? Math.floor(cookie.expires) : '',
      cookie.name, cookie.value].join('\t'));
  }
  const cookieFile = path.join(privateDir, 'youzan-cookies.txt');
  const temporary = cookieFile + '.' + crypto.randomUUID() + '.tmp';
  fs.writeFileSync(temporary, rows.join('\n') + '\n', { mode: 0o600 });
  fs.renameSync(temporary, cookieFile);
  fs.chmodSync(cookieFile, 0o600);
  report.authenticated = true;
  report.result = result;
}

function message(value) {
  let text = String(value || '');
  for (const secret of secrets) if (secret) text = text.split(secret).join('[redacted]');
  return text.slice(0, 200);
}
function log(event) {
  const row = { at: new Date().toISOString(), ...event };
  report.events.push(row);
  process.stdout.write(JSON.stringify(row) + '\n');
}
function endpoint(url) { const u = new URL(url); return { host: u.hostname, path: u.pathname }; }
function legacyPassword(value) {
  const raw = Buffer.from(value);
  const n = 16 - raw.length % 16;
  const padded = Buffer.concat([raw, crypto.randomBytes(n - 1), Buffer.from([n])]);
  const cipher = crypto.createCipheriv('aes-128-cbc', Buffer.from('youzan.com._key_'),
    Buffer.from('youzan.com.aesiv'));
  cipher.setAutoPadding(false);
  return Buffer.concat([cipher.update(padded), cipher.final()]).toString('base64');
}

async function observe(response) {
  if (interactive) await interactiveSlider.observe(response);
  const ep = endpoint(response.url());
  if (ep.host === 'risk-dfp-service-v2.youzan.com' && ep.path === '/web3_8/profile.json') {
    // Observe the page SDK's own JSONP response; never replay or fabricate its request.
    const text = await response.text();
    const start = text.indexOf('{'), end = text.lastIndexOf('}');
    if (start < 0 || end < start) return;
    let body;
    try { body = JSON.parse(text.slice(start, end + 1)); } catch (_) { return; }
    const xxid = body.result?.xxid;
    if (response.status() === 200 && body.code === '000' &&
        typeof xxid === 'string' && xxid.length > 0 && xxid.length <= 4096) {
      secrets.add(xxid);
      sdkProfile = { xxid, code: body.code };
      resolveSdkProfile(sdkProfile);
      log({ event: 'fingerprint_profile_received', http_status: response.status(),
        code: body.code, xxid_present: true, xxid_length: xxid.length });
    }
    return;
  }
  const relevant = ep.path.includes('/api/captcha/') ||
    ep.path === '/api/login/by-pass-with-tencent-image.json' ||
    ep.path.includes('/new/dfp/') || ep.path.endsWith('/risk-dfp-service-v2/fm.js');
  if (!relevant) return;
  const event = { event: 'response', method: response.request().method(), ...ep,
    http_status: response.status() };
  if (ep.path.includes('/api/captcha/') || ep.path === '/api/login/by-pass-with-tencent-image.json') {
    if (event.method !== 'OPTIONS') {
      const headers = await response.request().allHeaders();
      event.cookie_names = (headers.cookie || '').split(';').map(c => c.trim().split('=')[0]).filter(Boolean);
      event.browser_headers = {};
      for (const name of ['user-agent', 'origin', 'referer', 'sec-ch-ua-platform']) {
        if (headers[name]) event.browser_headers[name] = headers[name];
      }
      try {
        const body = await response.json();
        event.code = body.code;
        event.message = message(body.msg || body.message);
        for (const key of ['token', 'randomStr']) if (body.data?.[key]) secrets.add(body.data[key]);
        if (typeof body.data?.success === 'boolean') event.success = body.data.success;
        if (body.data?.captchaType !== undefined) event.captcha_type = body.data.captchaType;
        if (ep.path.includes('check-behavior-captcha-data')) {
          try { event.submitted_captcha_type = response.request().postDataJSON()?.captchaType; } catch (_) {}
        }
      } catch (_) { event.non_json = true; }
    }
  }
  log(event);
}

async function browserPost(page, url, data, form = false) {
  return await page.evaluate(async ({ url, data, form }) => {
    const response = await fetch(url, { method: 'POST', credentials: 'include',
      headers: { 'Content-Type': form ? 'application/x-www-form-urlencoded' : 'application/json',
                 Accept: 'application/json, text/plain, */*' },
      body: form ? new URLSearchParams(data).toString() : JSON.stringify(data) });
    return { status: response.status, body: await response.json() };
  }, { url, data, form });
}

async function verification(kind, details = {}) {
  // The parent binds this prompt to the active account and authenticated session.
  process.stdout.write(JSON.stringify({event: 'verification_input', kind, ...details}) + '\n');
  const next = await actions.next();
  if (next.done || next.value.length > 32768) throw new Error('verification_input_closed');
  const action = JSON.parse(next.value);
  if (action.kind !== kind) throw new Error('unexpected_verification_action');
  return action;
}

function accountResponse(page, pathname, method, timeout = 30000) {
  const pending = page.waitForResponse(response =>
    new URL(response.url()).origin === 'https://account.youzan.com' &&
    new URL(response.url()).pathname === pathname && response.request().method() === method, {timeout});
  pending.catch(() => {});
  return pending;
}

async function manualSlider(page) {
  return interactiveSlider.perform({page, context, verification});
}

async function smsLogin(page) {
  stage = 'native_sms_login';
  // The native password-login handler opens its account verification dialog.
  const dialog = page.locator('.captcha-dialog:visible');
  await dialog.waitFor({state: 'visible', timeout: 10000});
  const codeInput = dialog.getByPlaceholder('输入短信验证码', {exact: true});
  await verification('send');
  const send = accountResponse(page, '/api/captcha/sms/send.json', 'GET', 420000);
  const slider = page.locator('#slideBlockRef:visible').first();
  await dialog.locator('.js-get-identify-code').click();
  const first = await Promise.race([
    send.then(response => ({response})),
    slider.waitFor({state: 'visible', timeout: 20000}).then(() => ({})),
  ]);
  if (!first.response) {
    if (!await manualSlider(page)) {
      report.result = 'human_verification_required';
      return;
    }
  }
  let sentTimer;
  let sent;
  try {
    sent = await Promise.race([send, new Promise((_, reject) => {
      sentTimer = setTimeout(() => reject(new Error('sms_send_timeout')), 15000);
    })]);
  } finally { clearTimeout(sentTimer); }
  const sendResult = await sent.json();
  if (sendResult.code !== 0 || sendResult.data?.success !== true) {
    report.result = 'sms_send_failed';
    return;
  }
  for (let attempt = 0; attempt < 3; attempt++) {
    const {code} = await verification('code', {rejected: attempt > 0});
    if (typeof code !== 'string' || !/^[0-9]{6}$/.test(code)) throw new Error('invalid_verification_code');
    secrets.add(code);
    await codeInput.fill(code);
    const submitted = accountResponse(page, '/api/register-and-login.json', 'POST');
    await dialog.getByRole('button', {name: '确定', exact: true}).click();
    const result = await (await submitted).json();
    if (result.code === 0) {
      await finish(page, result, {nativeNavigation: true});
      return;
    }
  }
  report.result = 'sms_code_rejected';
}

async function finish(page, result, {nativeNavigation = false} = {}) {
  report.login_code = result.code;
  report.login_message = message(result.msg || result.message);
  if (result.code === 160210182 && interactive) {
    await smsLogin(page);
    return;
  }
  if (result.code !== 0) {
    report.result = result.code === 160210182 ? 'sms_verification_required' : 'login_rejected';
    return;
  }
  stage = 'verify_logged_in_state';
  let landed = false;
  if (nativeNavigation) {
    // The SMS dialog redirects on success. Give it time to land before starting our own navigation.
    try {
      await page.waitForURL(url => url.origin === 'https://www.youzan.com' && url.pathname.startsWith('/v4/'),
        {waitUntil: 'domcontentloaded', timeout: 8000});
      landed = true;
    } catch (error) {
      if (error.name !== 'TimeoutError') throw error;
    }
  }
  if (!landed) await page.goto('https://account.youzan.com/login/fill', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await saveAuthenticatedState(page, 'login_success_sid_saved');
}

(async () => {
  stage = 'verify_pinned_runtime';
  const locked = JSON.parse(fs.readFileSync(path.join(__dirname, 'youzan-login-runtime.json'), 'utf8'));
  for (const [file, expected] of Object.entries(locked.source_sha256)) {
    const actual = crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex');
    if (actual !== expected) throw new Error('pinned_source_changed: ' + file);
  }
  const runtime = require('./runtime/node_modules/playwright-core/package.json');
  if (runtime.version !== locked.playwright_core) throw new Error('playwright_version_changed');
  const { chromium } = require('./runtime/node_modules/playwright-core');
  report.runtime = { playwright_core: runtime.version, trajectory_commit: locked.trajectory_commit };
  fs.mkdirSync(profile, { recursive: true, mode: 0o700 });
  fs.chmodSync(privateDir, 0o700);
  fs.chmodSync(profile, 0o700);
  context = await chromium.launchPersistentContext(profile, {
    executablePath: '/usr/sbin/chromium', headless: false,
    viewport: { width: 1280, height: 900 },
  });
  context.on('response', response => {
    const task = observe(response).catch(() => {}).finally(() => pendingObservations.delete(task));
    pendingObservations.add(task);
  });
  const page = context.pages()[0] || await context.newPage();
  activePage = page;
  stage = 'open_login_page';
  await page.goto('https://account.youzan.com/login', { waitUntil: 'domcontentloaded', timeout: 30000 });
  report.browser = await page.evaluate(() => ({ user_agent: navigator.userAgent,
    platform: navigator.platform, language: navigator.language,
    viewport: { width: innerWidth, height: innerHeight } }));
  // The server redirects authenticated visits to its protected /v4/ area.
  const landing = endpoint(page.url());
  if (landing.host === 'www.youzan.com' && landing.path.startsWith('/v4/')) {
    stage = 'reuse_existing_session';
    await saveAuthenticatedState(page, 'session_reused');
    log({ event: 'session_checked', authenticated: report.authenticated,
      result: report.result, sid_expiry: report.sid_expiry, landing: report.landing });
    return;
  }
  if (statusOnly) {
    report.result = 'login_required';
    report.landing = landing;
    return;
  }
  if (!loadCredentials()) { report.result = 'credentials_missing'; return; }
  // Existing cookies alone do not prove that this page's SDK has finished initializing.
  stage = 'fingerprint_initialization';
  let sdkTimer;
  let fingerprint;
  try {
    fingerprint = await Promise.race([sdkProfileReady, new Promise((_, reject) => {
      sdkTimer = setTimeout(() => reject(new Error('fingerprint_sdk_response_timeout')), 25000);
    })]);
  } finally { clearTimeout(sdkTimer); }
  await page.waitForFunction(expected =>
    localStorage.getItem('0876303504F93E80') === expected &&
    /(?:^|;\s*)yz_fp_hash=/.test(document.cookie) &&
    /(?:^|;\s*)dfp=/.test(document.cookie), fingerprint.xxid, { timeout: 15000 });
  report.fingerprint_sdk = { profile_response_observed: true, code: fingerprint.code,
    xxid_present: true, xxid_length: fingerprint.xxid.length, local_storage_confirmed: true };
  log({ event: 'fingerprint_sdk_ready', ...report.fingerprint_sdk });
  const beforeCookies = await context.cookies();
  report.cookie_names_before_login = [...new Set(beforeCookies.map(c => c.name))].sort();
  report.device_fingerprint_cookies_present = ['yz_fp_hash', 'dfp'].every(
    name => beforeCookies.some(c => c.name === name));
  log({ event: 'browser_ready', browser: report.browser,
    cookie_names: report.cookie_names_before_login,
    device_fingerprint_ready: report.device_fingerprint_cookies_present });

  stage = 'select_password_login';
  if (!await page.locator('input[type=password]').count()) {
    await page.locator('.js-tab-password-login').first().click({ timeout: 10000 });
    await page.getByText('密码登录', { exact: true }).click({ timeout: 10000 });
  }
  const accountInput = page.getByPlaceholder('请输入手机号', { exact: true });
  const passwordInput = page.getByPlaceholder('输入登录密码', { exact: true });
  await accountInput.waitFor({ timeout: 10000 });
  // These browser actions are collected by the site's own behavior SDK.
  await page.mouse.move(150, 180, { steps: 12 });
  await accountInput.click();
  await accountInput.fill(account);
  await page.mouse.move(520, 400, { steps: 18 });
  await passwordInput.click();
  await passwordInput.fill(password);
  // Check the input itself; clicking the wrapper can hit its agreement links.
  for (const checkbox of await page.locator('input[type=checkbox]').all()) {
    if (!await checkbox.isChecked()) await checkbox.check({ force: true });
  }
  log({ event: 'form_ready', inputs: await page.locator('input').evaluateAll(es => es.map(e => ({
    type: e.type, placeholder: e.placeholder, checked: e.checked, has_value: !!e.value }))),
    login_button_disabled: await page.locator('.js-login-btn').isDisabled() });

  stage = 'native_behavior_check';
  const tokenResponse = page.waitForResponse(r =>
    new URL(r.url()).pathname.endsWith('/get-behavior-captcha-token-v2.json') &&
    r.request().method() === 'GET', { timeout: 25000 });
  const nextResponse = page.waitForResponse(r => {
    const p = new URL(r.url()).pathname;
    return r.request().method() !== 'OPTIONS' &&
      (p.endsWith('/get-behavior-captcha-data.json') || p.endsWith('/by-pass-with-tencent-image.json'));
  }, { timeout: 30000 });
  tokenResponse.catch(() => {});
  nextResponse.catch(() => {});
  await page.locator('.js-login-btn').click({ timeout: 10000 });
  const initial = (await (await tokenResponse).json()).data;
  secrets.add(initial.token);
  secrets.add(initial.randomStr);
  const next = await nextResponse;
  if (new URL(next.url()).pathname.endsWith('/by-pass-with-tencent-image.json')) {
    report.slider_not_requested = true;
    report.password_submissions++;
    await finish(page, await next.json());
    return;
  }
  if (interactive) {
    stage = 'native_password_slider';
    const submitted = accountResponse(page, '/api/login/by-pass-with-tencent-image.json', 'POST', 235000);
    if (!await manualSlider(page)) {
      report.result = 'human_verification_required';
      return;
    }
    report.password_submissions++;
    await finish(page, await (await submitted).json());
    return;
  }
  const challenge = (await next.json()).data.captchaObtainInfoResult;
  stage = 'prepare_adapted_slider_payload';
  const handle = page.locator('#slideBlockRef:visible').first();
  await handle.waitFor({ state: 'visible', timeout: 10000 });
  await page.waitForFunction(() => [...document.querySelectorAll('#bigImg')]
    .some(e => e.complete && e.naturalWidth > 0 && e.getBoundingClientRect().width > 0),
    undefined, { timeout: 10000 });
  const geometry = await handle.evaluate(e => {
    const component = e.closest('.content');
    if (!component) throw new Error('widget bounds unavailable');
    const box = component.getBoundingClientRect(), handle = e.getBoundingClientRect();
    return { width: box.width, start_x: Math.round(handle.x + handle.width / 2 - box.x),
      start_y: Math.round(handle.y + handle.height / 2 - box.y) };
  });
  const [background, piece] = await Promise.all([
    context.request.get(challenge.bigUrl), context.request.get(challenge.smallUrl)]);
  const child = spawnSync(path.join(root, 'investigation/youzan-slider/.venv/bin/python'),
    [path.join(__dirname, 'encode_youzan_human_request.py')], { encoding: 'utf8',
      input: JSON.stringify({ background: (await background.body()).toString('base64'),
        piece: (await piece.body()).toString('base64'), raw_cy: challenge.cy,
        display_width: geometry.width, start_x: geometry.start_x, start_y: geometry.start_y,
        random_string: initial.randomStr }), maxBuffer: 2 * 1024 * 1024, timeout: 20000 });
  let prepared;
  try { prepared = JSON.parse(child.stdout); } catch (_) { throw new Error('adapter_output_invalid'); }
  if (child.status !== 0 || prepared.error_type) throw new Error('adapter_failed');
  report.coordinates = prepared.coordinates;
  report.human_track = prepared.summary;
  log({ event: 'slider_payload_ready', coordinates: prepared.coordinates, track: prepared.summary });
  const remaining = prepared.mouse_up_ms - Date.now();
  if (remaining > 0) await new Promise(resolve => setTimeout(resolve, remaining));
  stage = 'browser_slider_submit';
  report.slider_submissions++;
  const checked = await browserPost(page,
    'https://passport.youzan.com/api/captcha/check-behavior-captcha-data.json',
    { token: initial.token, bizType: 15, bizData: '', captchaType: 1,
      userBehaviorData: prepared.userBehaviorData });
  report.slider_success = checked.body.data?.success === true;
  if (!report.slider_success) { report.result = 'slider_rejected'; return; }

  stage = 'browser_password_submit';
  const csrf = await page.evaluate(() => window._global?.csrf_token);
  if (!csrf) throw new Error('csrf_missing');
  secrets.add(csrf);
  report.password_submissions++;
  const loggedIn = await browserPost(page,
    'https://account.youzan.com/api/login/by-pass-with-tencent-image.json',
    { countryCode: '+86', mobile: account, password: legacyPassword(password),
      autoLogin: 'true', ticket: initial.token, csrf_token: csrf, fromSource: 'SOURCE_PC' }, true);
  await finish(page, loggedIn.body);
})().catch(error => {
  report.result = 'browser_or_adapter_error';
  report.error_type = error.name;
  report.error_hint = message(String(error.message).split('\n')[0]);
  report.failed_stage = stage;
}).finally(async () => {
  if (!interactive && report.result === 'browser_or_adapter_error' && activePage) {
    try {
      report.ui_text = message(await activePage.locator('body').innerText());
      report.input_status = await activePage.locator('input').evaluateAll(es => es.map(e => ({
        type: e.type, placeholder: e.placeholder, checked: e.checked, has_value: !!e.value })));
      log({ event: 'failed_ui_state', stage: report.failed_stage, text: report.ui_text,
        inputs: report.input_status, error_hint: report.error_hint });
    } catch (_) {}
  }
  if (context) {
    report.cookie_names_after = [...new Set((await context.cookies().catch(() => [])).map(c => c.name))].sort();
    await Promise.allSettled([...pendingObservations]);
    await context.close().catch(() => {});
  }
  input?.close();
  if (input) process.stdin.pause();
  report.finished_at = new Date().toISOString();
  if (!managed) {
    const history = path.join(__dirname, 'login-runs');
    fs.mkdirSync(history, { recursive: true, mode: 0o700 });
    writeJSON(path.join(history, runId + '.json'), report);
    writeJSON(reportFile, report);
  }
  const exits = { session_reused: 0, login_success_sid_saved: 0, login_required: 10,
    sms_verification_required: 20, slider_rejected: 21, credentials_missing: 22,
    profile_account_mismatch: 22, browser_or_adapter_error: 23,
    login_rejected: 25, login_unconfirmed: 25 };
  process.exitCode = exits[report.result] ?? 23;
  process.stdout.write(JSON.stringify({ result: report.result, authenticated: report.authenticated,
    exit_code: process.exitCode, failed_stage: report.failed_stage,
    slider_success: report.slider_success, login_code: report.login_code,
    ...(managed ? {} : { report: reportFile }) }) + '\n');
});

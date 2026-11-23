// Credentials and bearer tokens stay in the main process. Renderer gets user metadata only.
const { app, safeStorage, dialog, BrowserWindow } = require('electron')
const https = require('node:https')
const fs = require('node:fs/promises')
const path = require('node:path')
const { readFileSync } = require('node:fs')
const { baseUrl } = require('./backend.json')
const authority = new URL(baseUrl)
const uploads = require('./upload.cjs')
const storageTransfers = require('./storage-transfer.cjs')
if (authority.protocol !== 'https:') throw new Error('Authentication requires HTTPS')
const certificate = readFileSync(path.join(__dirname, 'certs/server.crt'))
const agent = createAgent()

function createAgent() {
  return new https.Agent({
    ca: certificate,
    keepAlive: true,
    keepAliveMsecs: 1000,
    maxSockets: 8,
    maxFreeSockets: 2,
    timeout: 20000,
  })
}
let token = null
let restored = false
let operations = Promise.resolve()
let responseLimits = { historyResponseBytes: 20 * 1024 * 1024 }
const sessionFile = () => path.join(app.getPath('userData'), 'session.encrypted')

function requestOnce(method, route, body, bearer, timeout = 15000, binary = false, fresh = false) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null
    const req = https.request(new URL(route, authority), {
      method, agent: fresh ? false : agent, ...(fresh ? { ca: certificate } : {}), headers: { Accept: 'application/json',
        ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {}),
        ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
      },
    }, res => {
      let bytes = 0
      const chunks = []
      const maxBytes = binary ? 20 * 1024 * 1024 : responseLimits.historyResponseBytes
      res.on('data', chunk => {
        bytes += chunk.length
        if (bytes > maxBytes) { res.destroy(); reject(new Error('服务器返回内容过大，请在 AI 设置的高级设置中提高响应上限。')); return }
        chunks.push(chunk)
      })
      res.on('aborted', () => {
        const error = new Error('服务器连接被中断。')
        error.code = 'ECONNRESET'
        reject(error)
      })
      res.on('error', error => reject(error))
      res.on('end', () => {
        if (binary && res.statusCode >= 200 && res.statusCode < 300) {
          if (res.headers['content-type']?.split(';')[0].trim() !== binary) {
            reject(new Error('文件类型异常。')); return
          }
          resolve(Buffer.concat(chunks)); return
        }
        let value
        try { value = JSON.parse(Buffer.concat(chunks).toString('utf8')) }
        catch { reject(new Error('登录服务返回异常。')); return }
        if (res.statusCode < 200 || res.statusCode >= 300) {
          const error = new Error(value.error?.message || '登录请求失败。')
          error.status = res.statusCode
          error.code = value.error?.code
          reject(error)
        } else resolve(value)
      })
    })
    if (timeout > 0) req.setTimeout(timeout, () => {
      const error = new Error('服务器连接超时。')
      error.code = 'ETIMEDOUT'
      req.destroy(error)
    })
    req.on('error', error => reject(error))
    if (payload) req.write(payload)
    req.end()
  })
}

function retryableNetwork(error) {
  return ['ECONNRESET', 'EPIPE', 'ETIMEDOUT', 'ESOCKETTIMEDOUT', 'ECONNREFUSED', 'ENETUNREACH', 'EHOSTUNREACH'].includes(error?.code)
    || /服务器连接被中断|服务器连接超时|socket hang up/i.test(error?.message || '')
}

function publicNetworkError(error) {
  if (error?.code === 'ETIMEDOUT' || error?.code === 'ESOCKETTIMEDOUT') return new Error('服务器连接超时，请稍后重试。')
  if (error?.code === 'ECONNRESET' || error?.code === 'EPIPE' || /socket hang up|连接被中断/i.test(error?.message || ''))
    return new Error('服务器连接中断，请稍后重试。')
  if (error?.code === 'ECONNREFUSED' || error?.code === 'ENETUNREACH' || error?.code === 'EHOSTUNREACH')
    return new Error('无法连接服务器，请检查网络。')
  return error
}

async function request(method, route, body, bearer, timeout = 15000, binary = false) {
  try {
    return await requestOnce(method, route, body, bearer, timeout, binary)
  } catch (error) {
    // Only idempotent reads are retried. A failed POST must never be replayed,
    // because it may have created a chat, uploaded a file, or changed data.
    if (method === 'GET' && retryableNetwork(error)) {
      // An isolated retry must not destroy sockets used by chat.send or uploads.
      await new Promise(resolve => setTimeout(resolve, 250))
      try { return await requestOnce(method, route, body, bearer, timeout, binary, true) }
      catch (retryError) { throw publicNetworkError(retryError) }
    }
    throw publicNetworkError(error)
  }
}

async function eraseSession() { await fs.rm(sessionFile(), { force: true }) }
async function encryptionAvailable() {
  const available = await safeStorage.isAsyncEncryptionAvailable()
  return available && (process.platform !== 'linux' || safeStorage.getSelectedStorageBackend() !== 'basic_text')
}
async function saveSession(value) {
  if (!await encryptionAvailable()) throw new Error('系统加密不可用，请取消记住登录后重试。')
  const encrypted = await safeStorage.encryptStringAsync(JSON.stringify({ version: 1, server: baseUrl, token: value }))
  await fs.mkdir(app.getPath('userData'), { recursive: true })
  const temporary = sessionFile() + '.tmp'
  await fs.writeFile(temporary, encrypted, { mode: 0o600 })
  await fs.rename(temporary, sessionFile())
}
async function restoreSession() {
  if (restored) return
  restored = true
  try {
    const encrypted = await fs.readFile(sessionFile())
    if (!await encryptionAvailable()) throw new Error('Encryption unavailable')
    const decrypted = await safeStorage.decryptStringAsync(encrypted)
    const value = JSON.parse(decrypted.result)
    if (value.version !== 1 || value.server !== baseUrl || typeof value.token !== 'string' || value.token.length > 256)
      throw new Error('Invalid session cache')
    token = value.token
    if (decrypted.shouldReEncrypt) await saveSession(token)
  } catch (error) {
    token = null
    if (error.code !== 'ENOENT') await eraseSession()
  }
}
async function current() {
  await restoreSession()
  if (!token) return null
  try { return await request('GET', '/api/v1/auth/me', null, token) }
  catch (error) {
    if (error.status === 401) { token = null; await eraseSession(); return null }
    throw error
  }
}
async function login(value) {
  if (!value || typeof value.username !== 'string' || !value.username.trim() || value.username.length > 160 ||
      typeof value.password !== 'string' || !value.password || value.password.length > 256 || typeof value.remember !== 'boolean')
    throw new Error('请检查账号和密码格式。')
  if (value.remember && !await encryptionAvailable())
    throw new Error('系统加密不可用，请取消记住登录后重试。')
  const result = await request('POST', '/api/v1/auth/login', value)
  try {
    if (value.remember) await saveSession(result.access_token)
    else await eraseSession()
  } catch {
    await request('POST', '/api/v1/auth/logout', null, result.access_token).catch(() => {})
    throw new Error('无法安全保存登录，请稍后重试。')
  }
  token = result.access_token
  restored = true
  return { user: result.user, expires_at: result.expires_at, remember: result.remember }
}
async function logout() {
  if (token) await request('POST', '/api/v1/auth/logout', null, token)
  token = null
  await eraseSession()
  return null
}

// Serialize restore/login/logout, including duplicate React development effects.
function invoke(action, value) {
  const pending = operations.then(async () => {
    try {
      const data = await ({ current, login, logout }[action])(value)
      return { ok: true, data }
    } catch (error) { return { ok: false, message: error.message } }
  })
  operations = pending.catch(() => {})
  return pending
}
const identifier = value => {
  if (typeof value !== 'string' || !/^[0-9a-f-]{36}$/i.test(value)) throw new Error('无效的记录标识。')
  return value
}
const routes = {
  'usage.mine': () => ['GET', '/api/v1/usage/me'],
  'usage.report': value => ['GET', '/api/v1/usage?' + new URLSearchParams({ start:value.start, end:value.end, ...(value.entry_point ? {entry_point:value.entry_point} : {}) })],
  'clawbot.settings': () => ['GET', '/api/v1/clawbot'],
  'clawbot.connect': () => ['POST', '/api/v1/clawbot/connect'],
  'clawbot.poll': value => ['POST', '/api/v1/clawbot/poll', { id: identifier(value.id), ...(value.verify_code ? {verify_code:value.verify_code} : {}) }],
  'clawbot.disconnect': () => ['DELETE', '/api/v1/clawbot'],
  'schedule.list': () => ['GET', '/api/v1/schedule'],
  'schedule.delete': value => ['DELETE', '/api/v1/schedule/' + identifier(value.id) + '?revision=' + identifier(value.revision)],
  'guanjia.settings': () => ['GET', '/api/v1/guanjia'],
  'guanjia.databases': () => ['GET', '/api/v1/guanjia/databases'],
  'guanjia.login': value => ['POST', '/api/v1/guanjia/login', value],
  'profile.get': () => ['GET', '/api/v1/me/profile'],
  'profile.save': value => ['PUT', '/api/v1/me/profile', value],
  'profile.password': value => ['PUT', '/api/v1/me/password', value],
  'erp.deleteDepartment': value => ['DELETE', '/api/v1/erp/departments/' + identifier(value.id)],
  'erp.departments': () => ['GET', '/api/v1/erp/departments'],
  'permissions.get': value => ['GET', `/api/v1/permissions/${value.kind === 'department' ? 'department' : 'user'}/${identifier(value.id)}`],
  'permissions.save': value => ['PUT', `/api/v1/permissions/${value.kind === 'department' ? 'department' : 'user'}/${identifier(value.id)}`, { revision: value.revision, effects: value.effects, ...(typeof value.management === 'boolean' ? { management: value.management } : {}) }],
  'erp.saveDepartment': value => value.id ? ['PUT', '/api/v1/erp/departments/' + identifier(value.id), {name:value.name}] : ['POST', '/api/v1/erp/departments', {name:value.name}],
  'erp.employees': value => ['GET', '/api/v1/erp/employees?' + new URLSearchParams({ q: value?.q || '', page: String(value?.page || 1), ...(value?.status ? {status:value.status} : {}), ...(typeof value?.department === 'string' ? {department:value.department} : {}) })],
  'erp.saveEmployee': value => value.id ? ['PUT', '/api/v1/erp/employees/' + identifier(value.id), value.employee] : ['POST', '/api/v1/erp/employees', value.employee],
  'feishu.bot': () => ['GET', '/api/v1/feishu/bot'],
  'feishu.settings': () => ['GET', '/api/v1/feishu'],
  'feishu.saveApp': value => ['PUT', '/api/v1/feishu/app', value],
  'feishu.authorize': value => ['POST', '/api/v1/feishu/authorize', value || {}],
  'feishu.poll': value => ['POST', '/api/v1/feishu/poll', { id: identifier(value.id) }],
  'feishu.disconnect': () => ['DELETE', '/api/v1/feishu'],
  'youzan.settings': () => ['GET', '/api/v1/admin/youzan'],
  'youzan.shops': () => ['GET', '/api/v1/admin/youzan/shops'],
  'youzan.selectShop': value => ['PUT', '/api/v1/admin/youzan/shop', { kdt_id: value.kdt_id }],
  'youzan.save': value => ['PUT', '/api/v1/admin/youzan', value],
  'youzan.refresh': value => ['POST', '/api/v1/admin/youzan/refresh', { kind: value.kind }],
  'youzan.verification': value => ['POST', '/api/v1/admin/youzan/verification', value],
  'youzan.cancel': () => ['POST', '/api/v1/admin/youzan/cancel'],
  'youzan.renewal': value => ['PUT', '/api/v1/admin/youzan/renewal', value],
  'admin.settings': () => ['GET', '/api/v1/admin/ai'],
  'admin.tools': () => ['GET', '/api/v1/admin/ai/tools'],
  'admin.toolPrompts': () => ['GET', '/api/v1/admin/ai/tool-prompts'],
  'admin.saveToolPrompt': value => {
    if (typeof value.category !== 'string' || !/^[a-z][a-z0-9_-]{0,63}$/.test(value.category)) throw new Error('无效的工具分类。')
    return ['PUT', '/api/v1/admin/ai/tool-prompts/' + value.category, { prompt: value.prompt }]
  },
  'admin.addProvider': value => ['POST', '/api/v1/admin/ai/providers', value],
  'admin.updateProvider': value => ['PUT', `/api/v1/admin/ai/providers/${identifier(value.id)}`, value.provider],
  'admin.deleteProvider': value => ['DELETE', `/api/v1/admin/ai/providers/${identifier(value.id)}`],
  'admin.providerBalance': value => ['GET', `/api/v1/admin/ai/providers/${identifier(value.id)}/balance`],
  'admin.testProvider': value => ['POST', `/api/v1/admin/ai/providers/${identifier(value.id)}/test`],
  'admin.defaultProvider': value => ['PUT', '/api/v1/admin/ai/default', { provider_id: identifier(value.id) }],
  'admin.prompt': value => ['PUT', '/api/v1/admin/ai/prompt', { prompt: value.prompt }],
  'admin.advanced': value => ['PUT', '/api/v1/admin/ai/advanced', value],
  'chat.status': () => ['GET', '/api/v1/ai/status'],
  'chat.list': () => ['GET', '/api/v1/chat/conversations'],
  'chat.get': value => ['GET', `/api/v1/chat/conversations/${identifier(value.id)}`],
  'chat.delete': value => ['DELETE', `/api/v1/chat/conversations/${identifier(value.id)}`],
  'chat.send': value => ['POST', '/api/v1/chat/send', value],
  'chat.answer': value => ['POST', '/api/v1/chat/answer', value],
  'chat.cancel': value => ['POST', '/api/v1/chat/cancel', value],
  'chat.progress': value => ['GET', `/api/v1/chat/runs/${identifier(value.request_id)}`],
  'upload.status': value => ['GET', `/api/v1/uploads/${identifier(value.id)}`],
  'storage.list': () => ['GET', '/api/v1/storage'],
  'storage.published': () => ['GET', '/api/v1/storage/published'],
  'storage.delete': value => ['DELETE', `/api/v1/storage/files/${identifier(value.id)}`],
  'admin.storage': () => ['GET', '/api/v1/admin/ai/storage'],
  'admin.storageSave': value => ['PUT', '/api/v1/admin/ai/storage', value],
  'upload.delete': value => ['DELETE', `/api/v1/uploads/${identifier(value.id)}`],
}
async function api(action, value, notify = () => {}) {
  try {
    if (!Object.hasOwn(routes, action) && !['file.download', 'file.upload', 'file.upload.cancel', 'storage.upload', 'storage.download', 'storage.thumbnail', 'storage.cancel'].includes(action)) throw new Error('不支持的操作。')
    await operations
    await restoreSession()
    if (!token) throw new Error('请先登录。')
    const captured = token
    if (action.startsWith('storage.') && ['storage.upload','storage.download','storage.thumbnail','storage.cancel'].includes(action)) {
      const context = { bearer: captured, authority, agent, request, isCurrent: () => token === captured,
        notify: progress => { if (token === captured) notify(progress) } }
      if (action === 'storage.upload') return { ok: true, data: await storageTransfers.upload(context) }
      if (action === 'storage.cancel') return { ok: true, data: storageTransfers.cancel(identifier(value?.id), captured) }
      if (action === 'storage.thumbnail') {
        const content = await request('GET', `/api/v1/storage/files/${identifier(value?.id)}/thumbnail`, null, captured, 30000, 'image/jpeg')
        if (token !== captured) throw Error('账号已切换。')
        return { ok: true, data: { image: 'data:image/jpeg;base64,' + content.toString('base64') } }
      }
      const route = value?.kind === 'published'
        ? `/api/v1/storage/published/${value.scope === 'shared' ? 'shared' : 'personal'}/${encodeURIComponent(value.name)}/metadata`
        : `/api/v1/storage/files/${identifier(value?.id)}/metadata`
      const metadata = await request('GET', route, null, captured)
      if (token !== captured) throw Error('账号已切换。')
      return { ok: true, data: await storageTransfers.download(metadata, context) }
    }
    if (action === 'file.upload.cancel') return { ok: true, data: uploads.cancel(identifier(value?.id), captured) }
    if (action === 'file.upload') {
      if (!value || Object.keys(value).some(key => key !== 'id')) throw new Error('上传仅接受已申请的入口标识。')
      const data = await uploads.upload(identifier(value.id), { bearer: captured, authority, agent, request,
        isCurrent: () => token === captured, notify: progress => { if (token === captured) notify(progress) } })
      return { ok: true, data }
    }
    if (action === 'file.download') {
      const id = identifier(value?.id)
      const metadata = await request('GET', `/api/v1/files/${id}/metadata`, null, captured)
      if (token !== captured) throw new Error('账号已切换，请重新操作。')
      const filename = path.basename(metadata.filename).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')
      const selected = await dialog.showSaveDialog(BrowserWindow.getFocusedWindow(), {
        title: '保存文件', defaultPath: path.join(app.getPath('downloads'), filename),
        filters: [{ name: '文件', extensions: [path.extname(filename).slice(1) || '*'] }, { name: '所有文件', extensions: ['*'] }],
      })
      if (selected.canceled || !selected.filePath) return { ok: true, data: { canceled: true } }
      if (token !== captured) throw new Error('账号已切换，请重新操作。')
      const downloadPath = metadata.download_path || `/api/v1/files/${id}`
      if (typeof downloadPath !== 'string' || !downloadPath.startsWith('/files/') && downloadPath !== `/api/v1/files/${id}` || new URL(downloadPath, authority).origin !== authority.origin)
        throw new Error('文件下载地址无效。')
      const content = await request('GET', downloadPath, null, captured, 60000, metadata.media_type || 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
      if (token !== captured) throw new Error('账号已切换，请重新操作。')
      if (content.length !== metadata.size) throw new Error('文件下载不完整，请重试。')
      await fs.writeFile(selected.filePath, content)
      return { ok: true, data: { saved: true, filename } }
    }
    const [method, route, body] = routes[action](value)
    const data = await request(method, route, body, captured, action === 'chat.send' ? 0 : action === 'guanjia.databases' ? 40000 : action === 'guanjia.login' ? 120000 : action === 'admin.testProvider' ? 140000 : 15000)
    if (token !== captured) throw new Error('账号已切换，请重新操作。')
    if (action === 'chat.status' && Number.isFinite(data?.history_response_mb)) {
      responseLimits.historyResponseBytes = Math.max(2, Math.min(64, data.history_response_mb)) * 1024 * 1024
    }
    return { ok: true, data }
  } catch (error) { return { ok: false, message: error.message } }
}
module.exports = { invoke, api }

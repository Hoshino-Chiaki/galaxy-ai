// Credentials and bearer tokens stay in the main process. Renderer gets user metadata only.
const { app, safeStorage, dialog, BrowserWindow } = require('electron')
const https = require('node:https')
const fs = require('node:fs/promises')
const path = require('node:path')
const { readFileSync } = require('node:fs')
const { baseUrl } = require('./backend.json')
const authority = new URL(baseUrl)
const uploads = require('./upload.cjs')
if (authority.protocol !== 'https:') throw new Error('Authentication requires HTTPS')
const agent = new https.Agent({
  ca: readFileSync(path.join(__dirname, 'certs/server.crt')),
  keepAlive: true, maxSockets: 4,
})
let token = null
let restored = false
let operations = Promise.resolve()
const sessionFile = () => path.join(app.getPath('userData'), 'session.encrypted')

function request(method, route, body, bearer, timeout = 15000, binary = false) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null
    const req = https.request(new URL(route, authority), {
      method, agent, headers: { Accept: 'application/json',
        ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {}),
        ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
      },
    }, res => {
      let bytes = 0
      const chunks = []
      res.on('data', chunk => {
        bytes += chunk.length
        if (bytes > (binary ? 20 : 2) * 1024 * 1024) { res.destroy(); reject(new Error('服务器返回内容过大。')); return }
        chunks.push(chunk)
      })
      res.on('error', () => reject(new Error('无法连接服务器，请检查网络。')))
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
    const deadline = timeout > 0 ? setTimeout(() => req.destroy(new Error('timeout')), timeout) : null
    req.on('close', () => clearTimeout(deadline))
    req.on('error', () => reject(new Error('无法连接服务器，请检查网络。')))
    if (payload) req.write(payload)
    req.end()
  })
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
  'admin.testProvider': value => ['POST', `/api/v1/admin/ai/providers/${identifier(value.id)}/test`],
  'admin.defaultProvider': value => ['PUT', '/api/v1/admin/ai/default', { provider_id: identifier(value.id) }],
  'admin.prompt': value => ['PUT', '/api/v1/admin/ai/prompt', { prompt: value.prompt }],
  'chat.status': () => ['GET', '/api/v1/ai/status'],
  'chat.list': () => ['GET', '/api/v1/chat/conversations'],
  'chat.get': value => ['GET', `/api/v1/chat/conversations/${identifier(value.id)}`],
  'chat.delete': value => ['DELETE', `/api/v1/chat/conversations/${identifier(value.id)}`],
  'chat.send': value => ['POST', '/api/v1/chat/send', value],
  'chat.cancel': value => ['POST', '/api/v1/chat/cancel', value],
  'chat.progress': value => ['GET', `/api/v1/chat/runs/${identifier(value.request_id)}`],
  'upload.status': value => ['GET', `/api/v1/uploads/${identifier(value.id)}`],
  'upload.delete': value => ['DELETE', `/api/v1/uploads/${identifier(value.id)}`],
}
async function api(action, value, notify = () => {}) {
  try {
    if (!Object.hasOwn(routes, action) && !['file.download', 'file.upload', 'file.upload.cancel'].includes(action)) throw new Error('不支持的操作。')
    await operations
    await restoreSession()
    if (!token) throw new Error('请先登录。')
    const captured = token
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
    const data = await request(method, route, body, captured, action === 'chat.send' ? 0 : action === 'admin.testProvider' ? 140000 : 15000)
    if (token !== captured) throw new Error('账号已切换，请重新操作。')
    return { ok: true, data }
  } catch (error) { return { ok: false, message: error.message } }
}
module.exports = { invoke, api }

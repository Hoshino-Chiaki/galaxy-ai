// Only a validated release from the pinned Galaxy server can become an installer.
const https = require('node:https')
const path = require('node:path')
const fs = require('node:fs')
const fsp = require('node:fs/promises')
const { downloadPackage, hashFile } = require('./update-download.cjs')
const { spawn } = require('node:child_process')
const { baseUrl } = require('./backend.json')
const origin = new URL(baseUrl)
const VERSION = /^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$/

function compareVersions(left, right) {
  if (!VERSION.test(left) || !VERSION.test(right)) throw Error('版本号格式无效。')
  const a = left.split('.').map(Number), b = right.split('.').map(Number)
  for (let i = 0; i < 3; i++) { if (a[i] !== b[i]) return a[i] > b[i] ? 1 : -1 }
  return 0
}
function validateRelease(value) {
  if (!value || !VERSION.test(value.version)) throw Error('更新信息格式无效。')
  const filename = `galaxy-ai-Setup-${value.version}.exe`
  if (value.filename !== filename || value.download_path !== `/api/v1/updates/packages/${filename}`
    || !Number.isSafeInteger(value.size) || value.size < 1 || value.size > 512 * 1024 * 1024
    || typeof value.sha256 !== 'string' || !/^[a-f0-9]{64}$/.test(value.sha256)
    || typeof value.notes !== 'string' || value.notes.length > 12000
    || typeof value.published_at !== 'string' || value.published_at.length > 64) throw Error('更新信息格式无效。')
  return { version: value.version, filename, download_path: value.download_path,
    size: value.size, sha256: value.sha256, notes: value.notes, published_at: value.published_at }
}
function installerArguments(directory) {
  if (!path.win32.isAbsolute(directory) || /["\r\n\0]/.test(directory)) throw Error('安装目录无效。')
  // NSIS requires /D= to be last and unquoted, even when the path has spaces.
  return ['/S', '--updated', '--force-run', `/D=${directory}`]
}
function createUpdater({ app, notify = () => {} }) {
  const certificate = fs.readFileSync(path.join(__dirname, 'certs/server.crt'))
  const agent = new https.Agent({ ca: certificate, keepAlive: true })
  let state = { phase: 'idle', current_version: app.getVersion(), release: null, percent: 0, message: '' }
  let checking = null, installing = null, verifiedPath = null
  const publish = value => { state = { ...state, ...value }; notify(state); return state }
  function response(route, offset) {
    if (origin.protocol !== 'https:' || !route.startsWith('/api/v1/updates/')) return Promise.reject(Error('更新地址无效。'))
    return new Promise((resolve, reject) => {
      const req = https.get(new URL(route, origin), { agent, headers: { Accept: 'application/json, application/octet-stream', ...(offset > 0 ? { Range: `bytes=${offset}-` } : {}) } }, res => {
        if (res.statusCode !== 200 && !(offset > 0 && res.statusCode === 206)) { res.resume(); reject(Error(`更新服务暂不可用（${res.statusCode}）。`)); return }
        res.on('error', reject)
        resolve(res)
      })
      req.setTimeout(30000, () => req.destroy(Error('更新服务器连接超时，请重试。')))
      req.on('error', reject)
    })
  }
  async function manifest() {
    const stream = await response('/api/v1/updates/latest')
    let bytes = 0; const chunks = []
    for await (const chunk of stream) {
      bytes += chunk.length
      if (bytes > 65536) { stream.destroy(); throw Error('更新信息过大。') }
      chunks.push(chunk)
    }
    try { return validateRelease(JSON.parse(Buffer.concat(chunks).toString('utf8'))) }
    catch { throw Error('更新信息无效，请稍后重试。') }
  }
  async function check() {
    if (installing) return state
    if (checking) return checking
    checking = (async () => {
      publish({ phase: 'checking', message: '' })
      try {
        const release = await manifest()
        return publish({ phase: compareVersions(release.version, app.getVersion()) > 0 ? 'available' : 'current', release, percent: 0 })
      } catch (error) { return publish({ phase: 'error', message: error.message || '检查更新失败，请稍后重试。' }) }
      finally { checking = null }
    })()
    return checking
  }
  async function downloadAndInstall() {
    if (installing) return installing
    installing = (async () => {
      let partial
      try {
        const release = await manifest()
        if (compareVersions(release.version, app.getVersion()) <= 0) return publish({ phase: 'current', release, message: '' })
        if (process.platform !== 'win32' || !app.isPackaged) throw Error('请在已安装的 Windows 客户端中更新。')
        const directory = path.join(app.getPath('userData'), 'updates')
        await fsp.mkdir(directory, { recursive: true })
        const filename = path.join(directory, release.filename)
        partial = filename + '.partial'
        publish({ phase: 'downloading', release, percent: 0, downloaded: 0, speed_bytes_per_second: 0, remaining_seconds: null, message: '' })
        let cached = false
        try { cached = (await fsp.stat(filename)).size === release.size && await hashFile(filename) === release.sha256 } catch {}
        if (!cached) {
          const samples = []
          let lastNotice = 0
          await downloadPackage({ release, partial, request: offset => response(release.download_path, offset), onProgress(received) {
            const now = Date.now()
            if (samples.length && received < samples[samples.length - 1].received) samples.length = 0
            samples.push({ time: now, received })
            while (samples.length > 2 && now - samples[1].time > 5000) samples.shift()
            const elapsed = (now - samples[0].time) / 1000
            const speed = elapsed > .2 ? Math.max(0, Math.round((received - samples[0].received) / elapsed)) : 0
            if (now - lastNotice >= 200 || received === release.size) {
              lastNotice = now
              publish({ percent: Math.floor(received / release.size * 100), downloaded: received,
                speed_bytes_per_second: speed, remaining_seconds: speed ? Math.ceil((release.size - received) / speed) : null })
            }
          } })
          publish({ phase: 'verifying', percent: 100, downloaded: release.size, remaining_seconds: null })
          if ((await fsp.stat(partial)).size !== release.size || await hashFile(partial) !== release.sha256) {
            await fsp.unlink(partial).catch(() => {})
            await fsp.unlink(partial + '.json').catch(() => {})
            throw Error('安装包校验失败，请重试下载。')
          }
          const handle = await fsp.open(partial, 'r')
          try { const header = Buffer.alloc(2); await handle.read(header, 0, 2, 0); if (header.toString() !== 'MZ') throw Error('安装包格式无效。') }
          finally { await handle.close() }
          await fsp.rename(partial, filename)
          await fsp.unlink(partial + '.json').catch(() => {})
        }
        verifiedPath = filename
        const args = installerArguments(path.dirname(process.execPath))
        publish({ phase: 'installing', percent: 100, message: '正在安装，客户端即将重新启动。' })
        await new Promise((resolve, reject) => {
          const child = spawn(verifiedPath, args, { detached: true, stdio: 'ignore', windowsHide: true, windowsVerbatimArguments: true })
          child.once('error', reject)
          child.once('spawn', () => { child.unref(); resolve() })
        })
        // --force-run reopens the installed app; --updated preserves its data.
        setTimeout(() => app.quit(), 300)
        return state
      } catch (error) {
        return publish({ phase: 'error', message: error.message || '更新失败，请稍后重试。' })
      } finally { installing = null }
    })()
    return installing
  }
  return { state: () => state, check, install: downloadAndInstall }
}
module.exports = { createUpdater, compareVersions, validateRelease, installerArguments }

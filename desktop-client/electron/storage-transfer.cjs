// All native paths come from system dialogs. The renderer supplies only server file IDs.
const { dialog, BrowserWindow, app } = require('electron')
const fs = require('node:fs/promises')
const { createWriteStream } = require('node:fs')
const path = require('node:path')
const https = require('node:https')
const { pipeline } = require('node:stream/promises')
const active = new Map()

function cancel(id, owner) {
  const transfer = active.get(id)
  if (transfer?.owner === owner) transfer.cancel()
  return { canceled: true }
}

async function upload(context) {
  const { bearer, authority, agent, request, isCurrent, notify } = context
  const selected = await dialog.showOpenDialog(BrowserWindow.getFocusedWindow(), { title: '上传到我的 AI 文件存储', properties: ['openFile', 'multiSelections'] })
  if (selected.canceled || !selected.filePaths?.length) return { canceled: true, files: [], failures: [] }
  const files = [], failures = []
  for (const source of selected.filePaths) {
    if (!isCurrent()) throw new Error('账号已切换，已停止上传。')
    let handle, stream, ticket, complete = false
    const filename = path.basename(source)
    try {
      handle = await fs.open(source, 'r')
      const before = await handle.stat()
      if (!before.isFile()) throw new Error('请选择普通文件。')
      ticket = await request('POST', '/api/v1/storage/uploads', { filename, size: before.size }, bearer)
      if (!isCurrent()) throw new Error('账号已切换。')
      const transfer = { owner: bearer, cancel: () => {} }; active.set(ticket.id, transfer)
      stream = handle.createReadStream({ autoClose: false, highWaterMark: 256 * 1024 })
      const receipt = await new Promise((resolve, reject) => {
        const req = https.request(new URL(`/api/v1/storage/uploads/${ticket.id}/content`, authority), {
          method: 'PUT', agent, headers: { Authorization: `Bearer ${bearer}`, 'Content-Type': 'application/octet-stream', 'Content-Length': before.size },
        }, res => {
          const parts = []; let total = 0
          res.on('data', part => { total += part.length; if (total > 1024 * 1024) res.destroy(Error('返回内容过大')); else parts.push(part) })
          res.on('error', reject)
          res.on('end', () => {
            try {
              const data = JSON.parse(Buffer.concat(parts).toString('utf8'))
              if (res.statusCode < 200 || res.statusCode >= 300) throw Error(data.error?.message || '上传失败。')
              if (!isCurrent()) throw Error('账号已切换。')
              resolve(data)
            } catch (error) { reject(error) }
          })
        })
        transfer.cancel = () => { stream.destroy(); req.destroy(Error('已取消上传。')) }
        req.setTimeout(120000, () => req.destroy(Error('上传连接超时。')))
        req.on('error', error => { stream.destroy(); reject(error) })
        stream.on('error', error => req.destroy(error))
        let sent = 0, last = 0
        stream.on('data', part => {
          if (!isCurrent()) { req.destroy(Error('账号已切换。')); return }
          sent += part.length
          if (Date.now() - last > 100 || sent === before.size) {
            last = Date.now(); notify({ upload_request_id: ticket.id, filename, sent, total: before.size, percent: Math.min(99, Math.floor(sent * 100 / (before.size || 1))) })
          }
        })
        stream.pipe(req)
      })
      const after = await handle.stat()
      if (before.size !== after.size || before.mtimeMs !== after.mtimeMs) {
        await request('DELETE', `/api/v1/storage/files/${ticket.id}`, null, bearer).catch(() => {})
        throw Error('文件在上传时发生变化，请重新选择。')
      }
      complete = true; files.push(receipt)
      notify({ upload_request_id: ticket.id, filename, sent: before.size, total: before.size, percent: 100 })
    } catch (error) {
      failures.push({ filename, message: error.message || '上传失败。' })
    } finally {
      stream?.destroy(); await handle?.close().catch(() => {})
      if (ticket) {
        active.delete(ticket.id)
        if (!complete) await request('DELETE', `/api/v1/storage/uploads/${ticket.id}`, null, bearer).catch(() => {})
      }
    }
  }
  return { files, failures }
}

async function download(metadata, context) {
  const { bearer, authority, agent, isCurrent } = context
  const route = metadata.download_path
  if (typeof route !== 'string' || !route.startsWith('/api/v1/storage/') || new URL(route, authority).origin !== authority.origin) throw Error('下载地址无效。')
  const filename = path.basename(metadata.filename).replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')
  const selected = await dialog.showSaveDialog(BrowserWindow.getFocusedWindow(), { title: '下载文件', defaultPath: path.join(app.getPath('downloads'), filename) })
  if (selected.canceled || !selected.filePath) return { canceled: true }
  if (!isCurrent()) throw Error('账号已切换。')
  const temporary = selected.filePath + '.galaxy-' + require('node:crypto').randomUUID() + '.part'
  let received = 0
  try {
    await new Promise((resolve, reject) => {
      const req = https.get(new URL(route, authority), { agent, headers: { Authorization: `Bearer ${bearer}` } }, res => {
        if (res.statusCode !== 200) { res.resume(); reject(Error('文件下载失败或没有权限。')); return }
        const output = createWriteStream(temporary, { flags: 'wx' })
        res.on('data', part => {
          received += part.length
          if (!isCurrent() || received > metadata.size) req.destroy(Error('账号已切换或文件大小异常。'))
        })
        pipeline(res, output).then(resolve, reject)
      })
      req.setTimeout(120000, () => req.destroy(Error('下载连接超时。'))); req.on('error', reject)
    })
    if (!isCurrent() || received !== metadata.size) throw Error('下载不完整，请重试。')
    await fs.rename(temporary, selected.filePath)
    return { saved: true, filename }
  } finally { await fs.unlink(temporary).catch(() => {}) }
}
module.exports = { upload, download, cancel }

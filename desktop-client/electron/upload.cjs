// Native selection and streaming only. Renderer never supplies a local filesystem path.
const { dialog, BrowserWindow } = require('electron')
const fs = require('node:fs/promises')
const https = require('node:https')
const path = require('node:path')
const active = new Map()

function cancel(id, owner) {
  const transfer = active.get(id)
  if (transfer?.owner === owner) transfer.cancel()
  return { canceled: true }
}

async function upload(id, context) {
  const { bearer, authority, agent, request, isCurrent, notify } = context
  if (active.has(id)) throw new Error('这个入口正在上传文件。')
  const invitation = await request('GET', `/api/v1/uploads/${id}`, null, bearer)
  if (active.has(id)) throw new Error('这个入口正在上传文件。')
  if (invitation.status !== 'pending') throw new Error(invitation.status === 'ready' ? '文件已上传，请继续处理。' : '上传入口已过期或不可用。')
  if (!isCurrent()) throw new Error('账号已切换，请重新操作。')
  // Reserve the native interaction before opening the picker, including duplicate clicks.
  let canceled = false
  const transfer = { owner: bearer, cancel: () => { canceled = true } }
  active.set(id, transfer)
  let handle, stream
  try {
    const selected = await dialog.showOpenDialog(BrowserWindow.getFocusedWindow(), {
      title: invitation.purpose, properties: ['openFile'],
      filters: [...(invitation.accepted_extensions?.length ? [{ name: '所需文件', extensions: invitation.accepted_extensions }] : []),
        { name: '所有文件（含无扩展名文件）', extensions: ['*'] }],
    })
    if (selected.canceled || !selected.filePaths?.length || canceled) return { canceled: true }
    if (!isCurrent()) throw new Error('账号已切换，请重新操作。')
    handle = await fs.open(selected.filePaths[0], 'r')
    const before = await handle.stat()
    if (!before.isFile() || before.size <= 0 || before.size > Math.min(invitation.max_size_bytes, 256 * 1024 * 1024))
      throw new Error(`请选择不超过 ${Math.floor(invitation.max_size_bytes / 1024 / 1024)} MB 的普通文件。`)
    const filename = path.basename(selected.filePaths[0])
    const suffix = path.extname(filename).slice(1).toLowerCase()
    if (suffix && invitation.accepted_extensions?.length && !invitation.accepted_extensions.includes(suffix))
      throw new Error('文件类型不符合当前上传要求。')
    stream = handle.createReadStream({ autoClose: false, highWaterMark: 256 * 1024 })
    const receipt = await new Promise((resolve, reject) => {
      let settled = false
      const finish = (error, value) => { if (!settled) { settled = true; error ? reject(error) : resolve(value) } }
      const req = https.request(new URL(`/api/v1/uploads/${id}/content`, authority), {
        method: 'PUT', agent, headers: { Authorization: `Bearer ${bearer}`, 'Content-Type': 'application/octet-stream',
          'Content-Length': before.size, 'X-Upload-Filename': encodeURIComponent(filename), Accept: 'application/json' },
      }, res => {
        const chunks = []; let total = 0
        res.on('data', chunk => { total += chunk.length; if (total > 1024 * 1024) res.destroy(); else chunks.push(chunk) })
        res.on('error', () => finish(new Error('上传结果接收失败，请重试。')))
        res.on('end', () => {
          try {
            const value = JSON.parse(Buffer.concat(chunks).toString('utf8'))
            if (res.statusCode < 200 || res.statusCode >= 300) throw new Error(value.error?.message || '上传失败。')
            if (!isCurrent()) throw new Error('账号已切换，请重新操作。')
            finish(null, value)
          } catch (error) { finish(error) }
        })
      })
      req.setTimeout(120000, () => req.destroy(new Error('上传连接超时，请重试。')))
      transfer.cancel = () => { canceled = true; stream.destroy(); req.destroy(new Error('已取消上传。')) }
      req.on('error', error => { stream.destroy(); finish(new Error(canceled ? '已取消上传。' : error.message || '上传连接失败，请重试。')) })
      stream.on('error', () => req.destroy(new Error('无法读取所选文件。')))
      let sent = 0, last = 0
      stream.on('data', chunk => {
        if (!isCurrent()) { req.destroy(new Error('账号已切换，请重新操作。')); return }
        sent += chunk.length
        const now = Date.now()
        if (now - last > 100 || sent === before.size) {
          last = now
          notify({ upload_request_id: id, sent, total: before.size, percent: Math.min(99, Math.floor(sent * 100 / before.size)) })
        }
      })
      stream.pipe(req)
    })
    const after = await handle.stat()
    if (before.size !== after.size || before.mtimeMs !== after.mtimeMs) {
      await request('DELETE', `/api/v1/uploads/${id}`, null, bearer).catch(() => {})
      throw new Error('文件在上传过程中发生变化，请重新申请上传。')
    }
    notify({ upload_request_id: id, sent: before.size, total: before.size, percent: 100 })
    return { uploaded: true, receipt }
  } finally {
    stream?.destroy()
    await handle?.close().catch(() => {})
    active.delete(id)
  }
}

module.exports = { upload, cancel }

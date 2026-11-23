const fs = require('node:fs')
const fsp = require('node:fs/promises')
const crypto = require('node:crypto')
const { Transform } = require('node:stream')
const { pipeline } = require('node:stream/promises')

async function hashFile(filename) {
  const digest = crypto.createHash('sha256')
  for await (const chunk of fs.createReadStream(filename)) digest.update(chunk)
  return digest.digest('hex')
}

/** Resume only bytes belonging to the exact immutable, checksum-identified release. */
async function downloadPackage({ release, partial, request, onProgress = () => {}, attempts = 3 }) {
  const metadata = partial + '.json'
  const signature = JSON.stringify({ size: release.size, sha256: release.sha256 })
  let offset = 0
  try {
    if (await fsp.readFile(metadata, 'utf8') === signature) offset = (await fsp.stat(partial)).size
    if (!Number.isSafeInteger(offset) || offset < 0 || offset > release.size) offset = 0
  } catch {}
  if (!offset) await fsp.unlink(partial).catch(() => {})
  await fsp.writeFile(metadata, signature, { mode: 0o600 })
  onProgress(offset)
  for (let attempt = 0; offset < release.size; attempt++) {
    try {
      const response = await request(offset)
      if (response.statusCode === 206) {
        if (response.headers['content-range'] !== `bytes ${offset}-${release.size - 1}/${release.size}`) {
          response.destroy(); throw Error('安装包续传信息异常，请稍后重试。')
        }
      } else if (response.statusCode === 200) offset = 0
      else { response.destroy(); throw Error('更新服务暂不可用。') }
      if (response.headers['content-length'] !== undefined && Number(response.headers['content-length']) !== release.size - offset) {
        response.destroy(); throw Error('安装包大小异常，请稍后重试。')
      }
      let received = offset
      onProgress(received)
      const measure = new Transform({ transform(chunk, _encoding, callback) {
        received += chunk.length
        if (received > release.size) { callback(Error('安装包大小异常，已停止更新。')); return }
        onProgress(received)
        callback(null, chunk)
      } })
      await pipeline(response, measure, fs.createWriteStream(partial, { flags: offset ? 'a' : 'w', mode: 0o600 }))
      offset = (await fsp.stat(partial)).size
      if (offset !== release.size) throw Error('安装包未下载完整。')
    } catch (error) {
      offset = await fsp.stat(partial).then(value => value.size).catch(() => 0)
      onProgress(offset)
      if (attempt + 1 >= attempts) throw Error('下载中断，已保留进度，请重试继续下载。', { cause: error })
    }
  }
  return { metadata }
}
module.exports = { downloadPackage, hashFile }

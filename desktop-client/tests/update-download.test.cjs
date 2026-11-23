const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const crypto = require('node:crypto')
const { Readable } = require('node:stream')
const { downloadPackage, hashFile } = require('../electron/update-download.cjs')
const payload = Buffer.concat([Buffer.from('MZ'), crypto.randomBytes(256 * 1024)])
const release = { size: payload.length, sha256: crypto.createHash('sha256').update(payload).digest('hex') }
async function fixture(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'galaxy-download-test-'))
  t.after(() => fs.rm(directory, { recursive: true, force: true }))
  return path.join(directory, 'installer.partial')
}
function response(offset, options = {}) {
  const stream = options.stream || Readable.from([payload.subarray(offset)])
  stream.statusCode = options.status || (offset ? 206 : 200)
  stream.headers = { 'content-length': String(payload.length - offset), ...(offset ? { 'content-range': `bytes ${offset}-${payload.length - 1}/${payload.length}` } : {}) }
  return stream
}
test('fresh download and checksum preserve exact installer bytes', async t => {
  const partial = await fixture(t)
  await downloadPackage({release,partial,request: async offset => response(offset)})
  assert.equal(await hashFile(partial), release.sha256)
})
test('interrupted transfer resumes the actual bytes on disk instead of restarting', async t => {
  const partial = await fixture(t), offsets = []
  await downloadPackage({release,partial,request: async offset => {
    offsets.push(offset)
    if (offsets.length === 1) return response(0,{stream: Readable.from((async function*(){yield payload.subarray(0,65536);await new Promise(resolve=>setTimeout(resolve,25));throw Error('network reset')})())})
    return response(offset)
  }})
  assert.deepEqual(offsets,[0,65536])
  assert.equal(await hashFile(partial),release.sha256)
})
test('retry after process restart keeps a matching partial; another release starts fresh', async t => {
  const partial = await fixture(t)
  await fs.writeFile(partial,payload.subarray(0,1024))
  await fs.writeFile(partial+'.json',JSON.stringify(release))
  const offsets=[]
  await downloadPackage({release,partial,request:async offset=>{offsets.push(offset);return response(offset)}})
  assert.deepEqual(offsets,[1024]);assert.equal(await hashFile(partial),release.sha256)
  await fs.writeFile(partial+'.json',JSON.stringify({...release,sha256:'0'.repeat(64)}))
  await downloadPackage({release,partial,request:async offset=>{assert.equal(offset,0);return response(offset)}})
  assert.equal(await hashFile(partial),release.sha256)
})
test('an origin ignoring Range restarts safely rather than appending duplicate bytes', async t => {
  const partial = await fixture(t)
  await fs.writeFile(partial,payload.subarray(0,1024));await fs.writeFile(partial+'.json',JSON.stringify(release))
  await downloadPackage({release,partial,request:async offset=>{assert.equal(offset,1024);return response(0)}})
  assert.equal(await hashFile(partial),release.sha256)
})
test('wrong Content-Range is rejected and a good partial is retained', async t => {
  const partial = await fixture(t)
  await fs.writeFile(partial,payload.subarray(0,1024));await fs.writeFile(partial+'.json',JSON.stringify(release))
  await assert.rejects(downloadPackage({release,partial,attempts:1,request:async offset=>{const stream=response(offset);stream.headers['content-range']='bytes 0-99/100';return stream}}),/已保留进度/)
  assert.equal((await fs.stat(partial)).size,1024)
})

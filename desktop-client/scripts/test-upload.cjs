const test = require('node:test')
const assert = require('node:assert/strict')
const http = require('node:http')
const fs = require('node:fs/promises')
const path = require('node:path')
const os = require('node:os')
const vm = require('node:vm')
const { readFileSync } = require('node:fs')

const id = '11111111-1111-4111-8111-111111111111'

async function fixture(t, { canceled = false, status = 'pending', fail = false, content = Buffer.alloc(600000, 65) } = {}) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'galaxy-upload-test-'))
  const filename = path.join(directory, 'capture.har')
  await fs.writeFile(filename, content)
  t.after(async () => {
    const resolved = await fs.realpath(directory)
    assert.equal(path.dirname(resolved), await fs.realpath(os.tmpdir()))
    assert.ok(path.basename(resolved).startsWith('galaxy-upload-test-'))
    await fs.rm(resolved, { recursive: true, force: true })
  })
  const observed = { picker: 0, requests: 0, bytes: 0, headers: {}, progress: [] }
  const server = http.createServer((req, res) => {
    observed.requests++; observed.headers = req.headers
    req.on('data', chunk => { observed.bytes += chunk.length })
    req.on('end', () => {
      res.writeHead(fail ? 413 : 200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify(fail ? { error: { message: '额度不足' } } : { file_id: id, path: `uploads/${id}/payload`, purpose: '解析任务资料' }))
    })
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections() }))
  const module = { exports: {} }
  const code = readFileSync(path.join(__dirname, '../electron/upload.cjs'), 'utf8')
  vm.runInNewContext(code, { module, Buffer, Date, Map, Error, require: name => name === 'electron'
    ? { dialog: { showOpenDialog: async () => { observed.picker++; return { canceled, filePaths: [filename] } } }, BrowserWindow: { getFocusedWindow: () => null } }
    : name === 'node:https' ? http : require(name), URL })
  const context = { bearer: 'fixture-owner', authority: new URL(`http://127.0.0.1:${server.address().port}`),
    agent: undefined, isCurrent: () => true, notify: value => observed.progress.push(value),
    request: async () => ({ status, purpose: '上传后解析 HAR 请求', max_size_bytes: 1024 * 1024, accepted_extensions: ['har'] }) }
  return { api: module.exports, context, observed }
}

test('native picker streams the exact selected bytes and reports progress, then server receipt', async t => {
  const { api, context, observed } = await fixture(t)
  const result = await api.upload(id, context)
  assert.equal(result.uploaded, true)
  assert.equal(result.receipt.file_id, id)
  assert.equal(observed.bytes, 600000)
  assert.equal(observed.headers['content-length'], '600000')
  assert.equal(observed.headers.authorization, 'Bearer fixture-owner')
  assert.equal(observed.headers['x-upload-filename'], 'capture.har')
  assert.ok(observed.progress.some(p => p.percent > 0 && p.percent < 100))
  assert.equal(observed.progress.at(-1).percent, 100)
  assert.ok(!JSON.stringify(observed.progress).includes('fixture-owner'))
})

test('canceling native selection sends no file and no upload request', async t => {
  const { api, context, observed } = await fixture(t, { canceled: true })
  const result = await api.upload(id, context)
  assert.equal(result.canceled, true)
  assert.equal(observed.requests, 0)
})

test('expired invitation and account changes are rejected before local file selection', async t => {
  const { api, context, observed } = await fixture(t, { status: 'expired' })
  await assert.rejects(api.upload(id, context), /过期/)
  assert.equal(observed.picker, 0)
  context.request = async () => ({ status: 'pending' })
  context.isCurrent = () => false
  await assert.rejects(api.upload(id, context), /账号已切换/)
  assert.equal(observed.picker, 0)
})

test('server rejection is shown and never reports completion', async t => {
  const { api, context, observed } = await fixture(t, { fail: true })
  await assert.rejects(api.upload(id, context), /额度不足/)
  assert.ok(!observed.progress.some(p => p.percent === 100))
})

test('canceling transfer interrupts streaming and can only be done by its owner', async t => {
  const { api, context, observed } = await fixture(t)
  context.notify = value => {
    observed.progress.push(value)
    api.cancel(id, 'different-owner')
    api.cancel(id, context.bearer)
  }
  await assert.rejects(api.upload(id, context), /取消上传/)
  assert.ok(!observed.progress.some(p => p.percent === 100))
})

test('duplicate requests cannot open two file pickers for one invitation', async t => {
  const { api, context, observed } = await fixture(t)
  const results = await Promise.allSettled([api.upload(id, context), api.upload(id, context)])
  assert.equal(results.filter(r => r.status === 'fulfilled').length, 1)
  assert.equal(observed.picker, 1)
  assert.equal(observed.requests, 1)
})

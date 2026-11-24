const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const { readFileSync } = require('node:fs')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')
const vm = require('node:vm')

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'galaxy-prompt-transfer-'))
  t.after(() => fs.rm(root, { recursive: true, force: true }))
  const source = path.join(root, 'guide.md')
  await fs.writeFile(source, 'personal instructions')
  const received = [], requests = [], progress = []
  const server = http.createServer((req, res) => {
    const chunks = []
    req.on('data', chunk => chunks.push(chunk))
    req.on('end', () => {
      received.push({ path: req.url, data: Buffer.concat(chunks).toString() })
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ id: 'fixture-id', filename: 'guide.md', size: 21 }))
    })
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve) }))
  const module = { exports: {} }
  const dialogs = { showOpenDialog: async () => ({ canceled: false, filePaths: [source] }), showSaveDialog: async () => { throw Error('must not open a dialog for invalid paths') } }
  const nativeRequire = name => name === 'electron' ? { dialog: dialogs, BrowserWindow: { getFocusedWindow: () => null }, app: { getPath: () => root } } : name === 'node:https' ? http : require(name)
  vm.runInNewContext(readFileSync(path.join(__dirname, '../electron/storage-transfer.cjs'), 'utf8'), { require: nativeRequire, module, Buffer, URL, console })
  const context = { bearer: 'fixture-owner', authority: new URL(`http://127.0.0.1:${server.address().port}`), agent: undefined,
    isCurrent: () => true, notify: item => progress.push(item), request: async (method, url, body) => { requests.push({ method, url, body }); return { id: 'fixture-id' } } }
  return { transfer: module.exports, context, received, requests, progress }
}

test('prompt uploads stream through their own API namespace', async t => {
  const { transfer, context, received, requests, progress } = await fixture(t)
  const result = await transfer.upload({ ...context, apiRoot: '/api/v1/personal-prompt' })
  assert.equal(result.files.length, 1)
  assert.equal(result.failures.length, 0)
  assert.equal(requests[0].url, '/api/v1/personal-prompt/uploads')
  assert.equal(received[0].path, '/api/v1/personal-prompt/uploads/fixture-id/content')
  assert.equal(received[0].data, 'personal instructions')
  assert.equal(progress.at(-1).percent, 100)
})

test('ordinary chat uploads keep their existing namespace', async t => {
  const { transfer, context, received, requests } = await fixture(t)
  assert.equal((await transfer.upload(context)).files.length, 1)
  assert.equal(requests[0].url, '/api/v1/storage/uploads')
  assert.equal(received[0].path, '/api/v1/storage/uploads/fixture-id/content')
})

test('prompt downloads reject another namespace and external hosts', async t => {
  const { transfer, context } = await fixture(t)
  for (const route of ['/api/v1/storage/files/a/content', 'https://invalid.example/api/v1/personal-prompt/files/a/content']) {
    await assert.rejects(transfer.download({ download_path: route, filename: 'x', size: 0 }, { ...context, apiRoot: '/api/v1/personal-prompt' }), /下载地址无效/)
  }
})

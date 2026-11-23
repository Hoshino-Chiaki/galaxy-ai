const test = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')
const { EventEmitter } = require('node:events')

function harness() {
  const pending = [], agents = []
  class Agent {
    constructor() { this.requests = []; agents.push(this) }
    destroy() {
      for (const request of this.requests) {
        const error = new Error('socket hang up'); error.code = 'ECONNRESET'
        request.emit('error', error)
      }
    }
  }
  const https = { Agent, request(url, options, callback) {
    const request = new EventEmitter()
    request.setTimeout = () => request
    request.write = () => {}
    request.end = () => { pending.push({ url, options, callback, request }) }
    if (options.agent) options.agent.requests.push(request)
    return request
  } }
  const filename = path.resolve(__dirname, '../electron/auth.cjs')
  const context = { module: { exports: {} }, __dirname: path.dirname(filename), URL, Buffer,
    setTimeout: callback => { callback(); return 0 },
    require(name) {
      if (name === 'electron') return {}
      if (name === 'node:https') return https
      if (name === './backend.json') return { baseUrl: 'https://fixture.invalid' }
      if (name === './upload.cjs' || name === './storage-transfer.cjs') return {}
      return require(name)
    } }
  // Expose transport only inside the test VM, without expanding application IPC.
  vm.runInNewContext(fs.readFileSync(filename, 'utf8') + '\nmodule.exports.testRequest = request;', context)
  function respond(entry, body = { ok: true }) {
    const response = new EventEmitter(); response.statusCode = 200; response.headers = {}
    entry.callback(response)
    response.emit('data', Buffer.from(JSON.stringify(body))); response.emit('end')
  }
  return { request: context.module.exports.testRequest, pending, respond, agents }
}

test('retrying a failed progress GET preserves an active chat POST and certificate pinning', async () => {
  const h = harness()
  const chat = h.request('POST', '/api/v1/chat/send', { message: 'fixture' }, 'fixture', 0)
  const progress = h.request('GET', '/api/v1/chat/runs/fixture', null, 'fixture')
  const failure = new Error('socket hang up'); failure.code = 'ECONNRESET'
  h.pending[1].request.emit('error', failure)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(h.pending.length, 3)
  assert.equal(h.pending[2].options.agent, false)
  assert.ok(Buffer.isBuffer(h.pending[2].options.ca))
  h.respond(h.pending[2], { status: 'running' })
  h.respond(h.pending[0], { conversation: { id: 'fixture' } })
  assert.equal((await progress).status, 'running')
  assert.equal((await chat).conversation.id, 'fixture')
  assert.equal(h.agents.length, 1)
})

test('failed POST is not replayed and failed isolated GET returns a readable error', async () => {
  const h = harness()
  const post = h.request('POST', '/api/v1/chat/send', {}, 'fixture', 0)
  const rejected = assert.rejects(post, /服务器连接中断/)
  const failure = new Error('socket hang up'); failure.code = 'ECONNRESET'
  h.pending[0].request.emit('error', failure)
  await rejected
  assert.equal(h.pending.length, 1)
  const get = h.request('GET', '/fixture', null, 'fixture')
  const getRejected = assert.rejects(get, /服务器连接中断/)
  h.pending[1].request.emit('error', failure)
  await new Promise(resolve => setImmediate(resolve))
  h.pending[2].request.emit('error', failure)
  await getRejected
  assert.equal(h.pending.length, 3)
})

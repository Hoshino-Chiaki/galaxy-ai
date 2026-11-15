// Run inside Windows against the real application, without sending an AI message.
// Clipboard backup/restore is owned by the enclosing STA PowerShell script.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { execFileSync } = require('node:child_process')

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
let stage = 'connect'

async function connect() {
  let targets
  for (let i = 0; i < 100; i++) {
    try { targets = await fetch('http://127.0.0.1:19224/json').then(r => r.json()); if (targets.some(t => t.type === 'page')) break }
    catch { /* Wait for the Windows application to finish starting. */ }
    await sleep(100)
  }
  assert(targets, 'Windows debug endpoint unavailable')
  const target = targets.find(t => t.type === 'page' && t.url.startsWith('file:') && t.url.includes('index.html'))
  assert(target, 'Application page missing')
  const socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }) })
  let sequence = 0
  const pending = new Map()
  socket.addEventListener('message', event => {
    const value = JSON.parse(String(event.data))
    const task = pending.get(value.id)
    if (!task) return
    pending.delete(value.id); clearTimeout(task.timer)
    if (value.error) task.reject(new Error('DevTools command failed'))
    else task.resolve(value.result)
  })
  function send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++sequence
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('DevTools command timed out')) }, 20000)
      pending.set(id, { resolve, reject, timer })
      socket.send(JSON.stringify({ id, method, params }))
    })
  }
  async function evaluate(expression) {
    const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (result.exceptionDetails) throw new Error('Application evaluation failed')
    return result.result.value
  }
  async function until(expression) {
    for (let i = 0; i < 150; i++) { if (await evaluate(expression)) return; await sleep(100) }
    throw new Error('Application did not reach expected state')
  }
  async function click(selector, index = 0) {
    const point = await evaluate(`(() => { const e=Array.from(document.querySelectorAll(${JSON.stringify(selector)}))[${index}]; if(!e) throw Error('Missing target'); e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2} })()`)
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point })
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...point })
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...point })
  }
  return { send, evaluate, until, click, close: () => socket.close() }
}

function readClipboard() {
  const encoded = execFileSync('powershell.exe', ['-NoProfile', '-STA', '-Command', '[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((Get-Clipboard -Raw)))'], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 })
  return Buffer.from(encoded.trim(), 'base64').toString('utf8').replace(/\r\n/g, '\n')
}

;(async () => {
  const cdp = await connect()
  let expected
  try {
    await cdp.until(`!!document.querySelector('.workspace-page')`)
    assert(await cdp.evaluate(`!document.querySelector('#chat-message')?.value`), 'Do not overwrite a user draft')
    assert(await cdp.evaluate(`typeof window.desktop.clipboard?.writeText==='function' && !('readText' in window.desktop.clipboard)`), 'Native write bridge is missing')
    stage = 'find_existing_reply'
    const sample = await cdp.evaluate(`(async()=>{
      const list=await window.desktop.service.request('chat.list'); if(!list.ok) throw Error('Chat list failed');
      for(const item of list.data.conversations){
        const response=await window.desktop.service.request('chat.get',{id:item.id}); if(!response.ok) continue;
        const reply=response.data.messages.filter(m=>m.role==='assistant'&&m.content?.trim()).at(-1);
        if(reply)return {title:item.title,content:reply.content};
      }
      throw Error('No existing reply');
    })()`)
    expected = sample.content
    await cdp.evaluate(`Array.from(document.querySelectorAll('.workspace-nav .nav-item')).find(e=>e.textContent.trim()==='AI').click()`)
    await cdp.click('button[aria-label="对话记录"]')
    await cdp.until(`!!document.querySelector('.history-open')`)
    await cdp.evaluate(`(() => { const title=${JSON.stringify(sample.title)}; const e=Array.from(document.querySelectorAll('.history-open')).find(b=>b.querySelector('span')?.textContent===title); if(!e)throw Error('History entry missing'); e.click() })()`)
    await cdp.until(`!!document.querySelector('.assistant-response .copy-response')`)
    stage = 'copy_reply'
    const count = await cdp.evaluate(`document.querySelectorAll('.assistant-response .copy-response').length`)
    await cdp.click('.assistant-response .copy-response', count - 1)
    await cdp.until(`Array.from(document.querySelectorAll('.assistant-response .copy-response')).at(-1)?.textContent.includes('已复制')`)
    assert(readClipboard() === expected.replace(/\r\n/g, '\n'), 'Copied reply differs from source text')
    stage = 'copy_permission_json'
    await cdp.evaluate(`(() => { const b=document.querySelector('.console-nav'); if(b.getAttribute('aria-expanded')!=='true')b.click() })()`)
    await cdp.until(`!!document.querySelector('button[aria-label="控制台 飞书"]')`)
    await cdp.click('button[aria-label="控制台 飞书"]')
    await cdp.until(`!!document.querySelector('.feishu-settings .feishu-scopes button')`)
    const expectedJson = await cdp.evaluate(`(async()=>{const response=await window.desktop.service.request('feishu.settings');if(!response.ok)throw Error('Settings missing');return response.data.permission_import})()`)
    await cdp.evaluate(`document.querySelector('.feishu-settings .feishu-scopes').open=true`)
    await cdp.click('.feishu-settings .feishu-scopes button')
    await cdp.until(`!!document.querySelector('.console-success')?.textContent.includes('权限 JSON 已复制')`)
    const actualJson = JSON.parse(readClipboard())
    assert(JSON.stringify(actualJson) === JSON.stringify(expectedJson), 'Copied permission JSON differs from source')
    stage = 'invalid_payload'
    assert(await cdp.evaluate(`window.desktop.clipboard.writeText(null).then(()=>false,()=>true)`), 'Invalid clipboard payload accepted')
    const report = { passed: true, actualWindowsApplication: true, replyCopiedExactly: true, permissionJsonCopiedExactly: true, invalidPayloadRejected: true, clipboardReadNotExposed: true, aiMessagesSent: 0, serverConfigurationWrites: 0 }
    fs.writeFileSync('C:\\Dev\\galaxy-ai-installer\\copy-verification.json', JSON.stringify(report, null, 2))
    console.log(JSON.stringify(report))
  } finally { expected = null; cdp.close() }
})().catch(() => { console.error(JSON.stringify({ passed: false, stage })); process.exitCode = 1 })

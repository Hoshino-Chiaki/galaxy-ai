// Use the provided accounts in memory; restore the original administrator session.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict')
const {chromium}=require('../../session_research/runtime/node_modules/playwright-core')
;(async()=>{
 const lines=fs.readFileSync(path.resolve(__dirname,'../../1.txt'),'utf8').replace(/^\uFEFF/,'').split(/\r?\n/)
 const browser=await chromium.connectOverCDP('http://127.0.0.1:19224')
 const page=browser.contexts()[0].pages().find(p=>p.url().includes('index.html'))
 const current=await page.evaluate(()=>window.desktop.auth.current())
 assert(current.ok&&current.data.user.role==='admin')
 let switched=false
 try{
  assert.equal(await page.locator('#chat-message').inputValue(),'','Do not overwrite a user draft')
  const login=await page.evaluate(value=>window.desktop.auth.login(value),{username:lines[2],password:lines[3],remember:false})
  assert(login.ok);switched=true
  await page.reload();await page.locator('.workspace-page').waitFor()
  assert.equal(await page.getByRole('button',{name:'AI 设置',exact:true}).count(),0)
  await page.getByRole('button',{name:'控制台',exact:true}).click()
  assert(await page.getByRole('button',{name:'控制台 有赞',exact:true}).isVisible())
  await page.getByRole('button',{name:'控制台 飞书',exact:true}).click()
  await page.getByRole('heading',{name:'我的飞书账号',exact:true}).waitFor()
  assert.equal(await page.locator('#feishu-app-id').count(),0)
  assert.equal(await page.locator('#feishu-app-secret').count(),0)
  const permissions=await page.evaluate(async()=>{
   const ai=await window.desktop.service.request('admin.settings')
   const own=await window.desktop.service.request('youzan.settings')
   return {aiRejected:!ai.ok,ownConnectionVisible:own.ok,ownConfigured:own.data?.configured}
  })
  assert(permissions.aiRejected&&permissions.ownConnectionVisible&&!permissions.ownConfigured)
  const dir=path.resolve(__dirname,'../artifacts')
  await page.screenshot({path:path.join(dir,'windows-feishu-employee.png')})
  const result={passed:true,actualWindowsApp:true,employeeAiSettingsHidden:true,employeeAppSecretHidden:true,employeeConnectionsVisible:true,serverRejectsAiConfig:true,adminCredentialsNotReused:true}
  fs.writeFileSync(path.join(dir,'employee-windows-check.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result))
 }finally{
  if(switched){
   const restored=await page.evaluate(value=>window.desktop.auth.login(value),{username:lines[0],password:lines[1],remember:current.data.remember})
   if(!restored.ok)throw Error('Restore administrator session failed')
   await page.reload();await page.locator('.workspace-page').waitFor()
   await page.getByRole('button',{name:'控制台',exact:true}).click()
   await page.getByRole('button',{name:'控制台 飞书',exact:true}).click()
   await page.getByRole('heading',{name:'我的飞书账号',exact:true}).waitFor()
  }
  await browser.close()
 }
})().catch(()=>{console.error('Employee GUI verification failed');process.exitCode=1})

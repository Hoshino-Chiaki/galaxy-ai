const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict')
const {chromium}=require('../../session_research/runtime/node_modules/playwright-core')
;(async()=>{
 const browser=await chromium.connectOverCDP('http://127.0.0.1:19224')
 const page=browser.contexts()[0].pages().find(p=>p.url().includes('index.html'))
 const dir=path.resolve(__dirname,'../artifacts'),errors=[]
 page.on('pageerror',e=>errors.push(e.message))
 let testId
 try{
  await page.locator('.workspace-page').waitFor()
  await page.getByRole('button',{name:'AI 设置',exact:true}).click()
  await page.getByRole('tab',{name:'AI 模型',exact:true}).waitFor()
  await page.getByRole('tab',{name:'工具列表',exact:true}).click()
  await page.getByRole('tab',{name:/feishu/}).click()
  assert.equal(await page.locator('.tool-card').count(),4)
  await page.screenshot({path:path.join(dir,'windows-feishu-tools.png')})
  await page.getByRole('button',{name:'控制台',exact:true}).click()
  await page.getByRole('button',{name:'控制台 飞书',exact:true}).click()
  await page.getByRole('heading',{name:'我的飞书账号',exact:true}).waitFor()
  assert.equal(await page.locator('#feishu-app-secret').count(),1)
  assert(await page.getByRole('button',{name:'授权连接',exact:true}).isDisabled())
  await page.screenshot({path:path.join(dir,'windows-feishu-admin.png')})
  await page.getByRole('button',{name:'AI',exact:true}).click()
  const oldDraft=await page.locator('#chat-message').inputValue()
  assert.equal(oldDraft,'','Preserve user draft before testing')
  await page.getByRole('button',{name:'新建对话',exact:true}).click()
  const question='聊天打断功能验证：不要调用工具，请写一篇5000字的文章，详细介绍如何整理每天的工作。'
  await page.locator('#chat-message').fill(question)
  await page.getByRole('button',{name:'发送消息',exact:true}).click()
  const stop=page.getByRole('button',{name:'停止回复',exact:true})
  await stop.waitFor()
  await page.screenshot({path:path.join(dir,'windows-chat-stop-pending.png')})
  await stop.click()
  await page.getByText('已停止回复。',{exact:true}).waitFor({timeout:30000})
  await page.getByRole('button',{name:'发送消息',exact:true}).waitFor()
  assert.equal(await page.locator('.magic-shine-border').count(),0)
  await page.screenshot({path:path.join(dir,'windows-chat-stopped.png')})
  const first=await page.evaluate(async title=>{
   const list=await window.desktop.service.request('chat.list')
   const item=list.data.conversations.find(c=>c.title===title)
   if(!item)throw Error('Stopped conversation missing')
   const chat=await window.desktop.service.request('chat.get',{id:item.id})
   return {id:item.id,interrupted:chat.data.messages.at(-1).interrupted,count:chat.data.messages.length}
  },question.slice(0,36))
  testId=first.id;assert.equal(first.interrupted,true);assert.equal(first.count,2)
  await page.locator('#chat-message').fill('后续消息验证：请只回复 OK，不要调用工具。')
  await page.getByRole('button',{name:'发送消息',exact:true}).click()
  await page.locator('.chat-composer[aria-busy=false]').waitFor({timeout:180000})
  const followup=await page.evaluate(async id=>{
   const chat=await window.desktop.service.request('chat.get',{id})
   return {count:chat.data.messages.length,contentPresent:!!chat.data.messages.at(-1).content}
  },testId)
  assert.equal(followup.count,4);assert(followup.contentPresent)
  assert.deepEqual(errors,[])
  const result={passed:true,actualWindowsApp:true,version:'0.6.0',aiSettingsSeparate:true,feishuTools:4,appConfigurationAwaitingUser:true,stopButtonWorks:true,serverInterruptedSaved:true,followupWorks:true,rendererErrors:0}
  fs.writeFileSync(path.join(dir,'feishu-windows-check.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result))
 }finally{
  if(testId){await page.evaluate(async id=>{await window.desktop.service.request('chat.delete',{id})},testId);await page.getByRole('button',{name:'新建对话',exact:true}).click()}
  await browser.close()
 }
})().catch(e=>{console.error(e.message);process.exitCode=1})

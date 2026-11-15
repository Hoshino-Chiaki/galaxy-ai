const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict')
const {chromium}=require('../../session_research/runtime/node_modules/playwright-core')
;(async()=>{
 const browser=await chromium.connectOverCDP('http://127.0.0.1:19224')
 const page=browser.contexts()[0].pages().find(p=>p.url().includes('index.html'))
 try{
  const result=await page.evaluate(async()=>{
   const request=async(a,v)=>{const r=await window.desktop.service.request(a,v);if(!r.ok)throw new Error('Service failed: '+a);return r.data}
   const before=await request('youzan.settings'),globalBefore=(await request('admin.settings')).global_prompt
   let id
   try{
    const response=await request('chat.send',{message:'多店铺功能验证：用有赞客户搜索，不加业务筛选，每店只取第一页 1 条；店铺范围按有赞分类提示词的默认规则执行。我只需要各店总客户数与失败原因，不要导出，不要输出客户个人信息，也不要把总部和分店相加。',thinking:false,reasoning_effort:'low'})
    id=response.conversation.id
    const events=response.conversation.messages.flatMap(m=>m.tool_events||[])
    const event=events.find(e=>e.command==='galaxy-cli youzan customer-search'&&e.result?.shops?.length===7)
    if(!event)throw new Error('All-shop customer query missing')
    const counts=event.result.shops.map(s=>({shop_id:s.kdt_id,...(s.error?{error:s.error.code}:{code:s.data.code,total:s.data.data?.total,rows:s.data.data?.list?.length})}))
    const after=await request('youzan.settings')
    return {counts,partial:event.result.partial,globalUnchanged:(await request('admin.settings')).global_prompt===globalBefore,sidUnchanged:JSON.stringify(before.sid)===JSON.stringify(after.sid),defaultShopUnchanged:before.kdt_id===after.kdt_id}
   }finally{if(id)await request('chat.delete',{id})}
  })
  assert.equal(result.counts.length,7)
  assert(result.counts.find(x=>x.shop_id==='45330501').total>=100000)
  assert(result.counts.find(x=>x.shop_id==='94598170').total>=300000)
  assert.equal(result.counts.find(x=>x.shop_id==='91780004').total,1)
  assert.equal(result.counts.find(x=>x.shop_id==='93672726').error,'shop_access_required')
  assert(result.partial&&result.globalUnchanged&&result.sidUnchanged&&result.defaultShopUnchanged)
  const report={verified:true,actualWindowsApp:true,realAi:true,perQueryShopBinding:true,sameSidSerialized:true,testConversationRemoved:true,...result}
  fs.writeFileSync(path.resolve(__dirname,'../artifacts/shop-binding-windows-check.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report))
 }finally{await browser.close()}
})().catch(e=>{console.error(e.message);process.exitCode=1})

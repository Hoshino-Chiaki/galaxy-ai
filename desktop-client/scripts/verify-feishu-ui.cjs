const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict')
const {chromium}=require('../../session_research/runtime/node_modules/playwright-core')
;(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true})
 const dir=path.resolve(__dirname,'../artifacts')
 try{
  for(const role of ['employee','admin']){
   const page=await browser.newPage({viewport:{width:1280,height:920}})
   await page.addInitScript(({role})=>{
    const config={configured:true,app_id:'cli_fixtureapp',has_app_secret:true,connected:false,cli_ready:true,identity:null,expires_at:null,refresh_expires_at:null,scopes:[],required_scopes:['docx:document:readonly','drive:file:download'],pending:null}
    const permissions=['youzan:configure','feishu:configure','feishu:read',...(role==='admin'?['ai:configure','feishu:app:configure']:[])]
    let finish
    window.__requests=[];window.__polls=0;window.__stops=0
    window.desktop={auth:{current:async()=>({ok:true,data:{user:{id:'fixture-'+role,workspace_id:'fixture',username:role==='admin'?'管理员':'员工',role,permissions},expires_at:9999999999,remember:false}})},preferences:{read:async()=>({remember:false,account:''}),write:async()=>{}},
     service:{request:async(action,value)=>{
      window.__requests.push(action);let data
      if(action==='chat.status')data={available:true,name:'AI',model:'fixture'}
      else if(action==='chat.list')data={conversations:[]}
      else if(action==='chat.send')data=await new Promise(resolve=>{finish=()=>resolve({interrupted:true,conversation:{id:'fixture-chat',title:value.message,messages:[{id:'u',role:'user',content:value.message},{id:'a',role:'assistant',content:'已停止回复。',interrupted:true}]}})})
      else if(action==='chat.cancel'){window.__stops++;finish();data={requested:true}}
      else if(action==='feishu.settings')data=structuredClone(config)
      else if(action==='feishu.authorize'){config.pending={id:'fixture-id',url:'https://accounts.feishu.cn/device?user_code=TEST',user_code:'TEST-CODE',expires_at:Math.floor(Date.now()/1000)+60,interval:1};data=structuredClone(config)}
      else if(action==='feishu.poll'){window.__polls++;config.pending=null;config.connected=true;config.identity={name:'测试员工',open_id:'ou_fixture'};config.expires_at=Math.floor(Date.now()/1000)+7200;data=structuredClone(config)}
      else if(action==='feishu.saveApp'){config.app_id=value.app_id;data=structuredClone(config)}
      else if(action==='admin.settings'){if(role!=='admin')throw Error('Employee accessed admin settings');data={providers:[],default_provider_id:null,global_prompt:'通用规则'}}
      else if(action==='youzan.settings')data={configured:false,account:'',kdt_id:'',has_password:false,sid:{present:false},csrf:{present:false},shops:[],shop_sessions:[],job:null}
      else throw Error('Unexpected action '+action)
      return {ok:true,data}
     }},openExternal:async url=>{window.__opened=url},minimize(){},maximize(){},close(){}}
   },{role})
   await page.goto('http://127.0.0.1:4173')
   await page.getByRole('button',{name:'控制台',exact:true}).click()
   assert.equal(await page.getByRole('button',{name:'控制台 AI',exact:true}).count(),0)
   await page.getByRole('button',{name:'控制台 飞书',exact:true}).click()
   await page.getByRole('heading',{name:'我的飞书账号',exact:true}).waitFor()
   assert.equal(await page.locator('#feishu-app-secret').count(),role==='admin'?1:0)
   assert.equal(await page.getByRole('button',{name:'AI 设置',exact:true}).count(),role==='admin'?1:0)
   if(role==='employee'){
    assert(!(await page.evaluate(()=>window.__requests)).includes('admin.settings'))
    await page.screenshot({path:path.join(dir,'feishu-employee.png')})
    await page.getByRole('button',{name:'授权连接',exact:true}).click()
    await page.getByText('TEST-CODE',{exact:true}).waitFor()
    await page.waitForFunction(()=>window.__polls===1)
    await page.getByText('已连接 · 测试员工',{exact:true}).waitFor()
    assert((await page.evaluate(()=>window.__opened)).startsWith('https://accounts.feishu.cn/'))
    await page.getByRole('button',{name:'AI',exact:true}).click()
    await page.locator('#chat-message').fill('打断验证')
    await page.getByRole('button',{name:'发送消息',exact:true}).click()
    await page.getByRole('button',{name:'停止回复',exact:true}).waitFor()
    await page.screenshot({path:path.join(dir,'chat-stop-pending.png')})
    await page.getByRole('button',{name:'停止回复',exact:true}).click()
    await page.getByText('已停止回复。',{exact:true}).waitFor()
    assert.equal(await page.evaluate(()=>window.__stops),1)
    assert.equal(await page.locator('.magic-shine-border').count(),0)
    await page.getByRole('button',{name:'发送消息',exact:true}).waitFor()
   }else{
    await page.screenshot({path:path.join(dir,'feishu-admin.png')})
    await page.getByRole('button',{name:'AI 设置',exact:true}).click()
    await page.getByRole('tab',{name:'AI 模型',exact:true}).waitFor()
   }
   await page.close()
  }
  const result={passed:true,fixturesOnly:true,employeeConnectionsVisible:true,aiSettingsAdminOnly:true,appSecretAdminOnly:true,oauthPoll:true,chatStop:true}
  fs.writeFileSync(path.join(dir,'feishu-ui-check.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result))
 }finally{await browser.close()}
})().catch(e=>{console.error(e.message);process.exitCode=1})

const test=require('node:test'),assert=require('node:assert/strict');
const {createInteractiveSlider}=require('../youzan/interactive-slider.cjs');
const fakeResponse=(url,data)=>({url:()=>url,request:()=>({method:()=> 'GET'}),json:async()=>({code:0,data})});

test('current challenge reaches encoder and the native SDK gets the returned pointer actions',async()=>{
 const bridge=createInteractiveSlider(),image='https://images.invalid/current.png';
 await bridge.observe(fakeResponse('https://passport.youzan.com/api/captcha/get-behavior-captcha-data.json?token=current',
  {captchaObtainInfoResult:{bigUrl:image,smallUrl:'https://images.invalid/piece.png',cy:100}}));
 await bridge.observe(fakeResponse('https://passport.youzan.com/api/captcha/get-behavior-captcha-token-v2.json',
  {token:'current',randomStr:'fixture-private-random'}));
 const moves=[];let resolved;
 const slider={waitFor:async()=>{},locator:()=>({screenshot:async()=>Buffer.from('preview')}),
  evaluate:async fn=>fn({closest:()=>({getBoundingClientRect:()=>({x:10,y:10,width:280})}),getBoundingClientRect:()=>({x:20,y:50,width:20,height:20})}),
  boundingBox:async()=>({x:20,y:50,width:20,height:20})};
 const big={evaluate:async fn=>fn({decode:async()=>{},currentSrc:image})};
 const page={locator:s=>({first:()=>s.startsWith('#slide')?slider:big}),
  waitForResponse:()=>new Promise(resolve=>{resolved=resolve}),
  mouse:{move:async(x,y)=>moves.push(['move',x,y]),down:async()=>moves.push(['down']),up:async()=>{moves.push(['up']);resolved({json:async()=>({data:{success:true}})})}}};
 const fetched=[];const context={request:{get:async url=>{fetched.push(url);return{ok:()=>true,body:async()=>Buffer.from(url)}}}};
 let input;
 const verification=async(kind,details)=>{assert.equal(kind,'slider');input=details.encoder_input;
  return{kind:'slider',points:[{x:0,y:0,ms:0},{x:150,y:1,ms:5}],release_delay_ms:5}};
 assert.equal(await bridge.perform({page,context,verification}),true);
 assert.equal(input.random_string,'fixture-private-random');assert.equal(input.raw_cy,100);
 assert.equal(input.display_width,280);assert.equal(input.start_x,20);assert.equal(input.start_y,50);
 assert.equal(Buffer.from(input.background,'base64').toString(),image);
 assert.deepEqual(fetched,[image,'https://images.invalid/piece.png']);
 assert.deepEqual(moves,[['move',30,60],['down'],['move',30,60],['move',180,61],['up']]);
});

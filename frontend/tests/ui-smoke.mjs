// Component integration check against the running loopback API. No real browser/visual claims.
import assert from 'node:assert/strict'
import {writeFile,unlink} from 'node:fs/promises'
import {fileURLToPath,pathToFileURL} from 'node:url'
import {dirname,resolve} from 'node:path'
import {build} from 'esbuild'
import {JSDOM} from 'jsdom'
import React,{act} from 'react'
import {createRoot} from 'react-dom/client'

const folder=resolve(dirname(fileURLToPath(import.meta.url)),'..')
const temp=resolve(folder,'.ui-smoke.mjs')
const nodeFetch=globalThis.fetch
const base=process.env.INSIGHT_TEST_BASE_URL||'http://127.0.0.1:8765'
const tasks=await (await nodeFetch(base+'/api/tasks')).json()
let demo=tasks.find(t=>t.config.source==='demo')
if(!demo)demo=await (await nodeFetch(base+'/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product:'飞书',source:'demo',analysis_mode:'rules',insight_version:2})})).json()
let multi
if(process.env.INSIGHT_TEST_FIXTURES==='1'){
  // Only opt in on an isolated test data directory, never represent these as live data.
  multi=await (await nodeFetch(base+'/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({product:'飞书',source:'import',platforms:['dy','bili'],analysis_mode:'rules',insight_version:2})})).json()
  const payload={notes:['dy','bili'].map(platform=>({platform,note_id:'1',title:'飞书 · 人工构造的多平台观点验收样本'})),comments:['dy','bili'].flatMap(platform=>[{platform,note_id:'1',comment_id:'1',text:'飞书文档协作顺手，但跨端同步卡顿'},{platform,note_id:'1',comment_id:'2',parent_comment_id:'1',text:'我也是'}])}
  const response=await nodeFetch(base+'/api/tasks/'+multi.id+'/import',{method:'POST',headers:{'X-Filename':'MULTIPLATFORM_TEST_FIXTURE.json'},body:JSON.stringify(payload)})
  assert.equal(response.status,200)
  const opinion=(aspect,topic,quote,sentiment)=>({product_scope:'target',aspect,topic,kind:sentiment==='negative'?'problem':'experience',sentiment,basis:'direct',scenario:null,pain_point:sentiment==='negative'?topic:null,impact:null,expected_outcome:null,keywords:[aspect],quote,context_quote:null})
  for(const platform of ['dy','bili']){
    const response=await nodeFetch(base+'/api/tasks/'+multi.id+'/comments/'+platform+':1',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({points:[opinion('文档协作','文档协作顺手','文档协作顺手','positive'),opinion('多端同步','跨端同步卡顿','跨端同步卡顿','negative')],note:'人工构造的组件协议验收'})})
    assert.equal(response.status,200)
  }
  await nodeFetch(base+'/api/tasks/'+multi.id+'/analyze',{method:'POST'})
}
const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:base+'/#/'})
globalThis.window=dom.window
globalThis.document=dom.window.document
globalThis.location=dom.window.location
globalThis.HTMLElement=dom.window.HTMLElement
globalThis.IS_REACT_ACT_ENVIRONMENT=true
globalThis.fetch=(url,options)=>nodeFetch(new URL(String(url),base),options)
const errors=[]
dom.window.addEventListener('error',e=>errors.push(e.message))
const originalError=console.error
console.error=(...args)=>{if(!String(args[0]).includes('not wrapped in act'))errors.push(args.join(' '))}
let root
async function until(check,label){
  const end=Date.now()+15000
  while(Date.now()<end){
    await act(async()=>{await new Promise(r=>setTimeout(r,60))})
    if(check())return
  }
  throw Error('Timed out: '+label+' / '+document.body.textContent.slice(-800))
}
async function click(node){assert.ok(node,'target element exists');await act(async()=>{node.click();await new Promise(r=>setTimeout(r,60))})}
function button(text){return [...document.querySelectorAll('button')].find(x=>x.textContent.includes(text))}
async function navigate(path){await act(async()=>{window.location.hash=path;await new Promise(r=>setTimeout(r,60))})}
try{
  const bundle=await build({entryPoints:[resolve(folder,'src/App.tsx')],bundle:true,platform:'node',format:'esm',jsx:'automatic',external:['react','react-dom','lucide-react'],write:false})
  await writeFile(temp,bundle.outputFiles[0].text)
  const {default:App}=await import(pathToFileURL(temp).href)
  root=createRoot(document.getElementById('root'))
  await act(async()=>root.render(React.createElement(App)))
  await until(()=>document.querySelector('.new-task'),'home loaded')
  assert.ok(document.body.textContent.includes('听见反馈'))
  assert.equal(document.querySelectorAll('.platform-options input').length,7)
  await click(button('多平台采集'))
  await click(button('高级设置'))
  const sort=()=>[...document.querySelectorAll('label')].find(x=>x.textContent.includes('平台支持的排序'))?.querySelector('select')
  await act(async()=>{sort().value='time_descending';sort().dispatchEvent(new dom.window.Event('change',{bubbles:true}))})
  await click(document.querySelector('[aria-label="选择快手"]'))
  assert.equal(sort().value,'general','unsupported sort resets on platform change')
  assert.equal(sort().options.length,1)
  await click(button('全选平台'))
  assert.equal(document.querySelectorAll('.platform-options input:checked').length,7)
  assert.ok(document.body.textContent.includes('合计最多 35 条内容、700 条评论'))
  await click(button('恢复默认'))
  assert.equal(document.querySelectorAll('.platform-options input:checked').length,1)
  await click(button('模型与采集设置'))
  assert.ok(document.querySelector('[aria-label="模型与采集设置"]'))
  assert.ok(document.body.textContent.includes('密钥不会返回浏览器'))
  assert.equal(document.querySelectorAll('.settings-platforms>div').length,7)
  await click(document.querySelector('[aria-label="关闭设置"]'))
  await navigate('/tasks/'+demo.id)
  await until(()=>document.querySelectorAll('.comment-table tbody tr').length>0,'workbench comments')
  const reply=()=>[...document.querySelectorAll('.comment-table tbody tr')].find(x=>x.textContent.includes('我也是'))
  await click(reply())
  assert.ok(document.querySelector('.context-block.parent').textContent.includes('权限设置入口'))
  await click(button('排除评论'))
  await until(()=>!document.querySelector('.drawer'),'review saved')
  let data=await (await nodeFetch(base+'/api/tasks/'+demo.id+'/comments/xhs:demo-03')).json()
  assert.equal(data.effective.excluded,true)
  let task=await (await nodeFetch(base+'/api/tasks/'+demo.id)).json()
  assert.equal(task.report,null)
  assert.ok(button('仅重算统计（不调用模型）'))
  await click(button('仅重算统计（不调用模型）'))
  await until(()=>document.body.textContent.includes('已离线重算统计'),'offline recount complete')
  task=await (await nodeFetch(base+'/api/tasks/'+demo.id)).json()
  assert.equal(task.report.generation_mode,'cached_narrative_recount')
  assert.equal(task.report.support_check.new_model_check,false)
  assert.ok(!task.report.evidence['xhs:demo-03'])
  await click(button('重新生成分析'))
  await until(()=>document.body.textContent.includes('分析完成'),'reanalysis complete')
  await click(reply())
  await click(button('恢复评论'))
  await until(()=>!document.querySelector('.drawer'),'restored')
  await click(button('重新生成分析'))
  await until(()=>document.body.textContent.includes('分析完成'),'restored report complete')
  await navigate('/tasks/'+demo.id+'/report')
  await until(()=>document.querySelector('.report-sheet'),'report route')
  assert.ok(document.body.textContent.includes('人工虚构样本'))
  assert.equal(document.querySelectorAll('.report-toolbar a').length,3)
  await click(document.querySelector('.evidence-item'))
  await until(()=>document.querySelector('.drawer'),'report evidence opens')
  assert.ok(document.querySelector('.original-quote p').textContent.length>0)
  await click(document.querySelector('[aria-label="关闭证据抽屉"]'))
  if(multi){
    await navigate('/tasks/'+multi.id)
    await until(()=>document.querySelectorAll('.comment-table tbody tr').length===4,'multi-platform comments')
    assert.ok(document.body.textContent.includes('抖音、B站'))
    const select=document.querySelector('[aria-label="平台筛选"]')
    await act(async()=>{select.value='dy';select.dispatchEvent(new dom.window.Event('change',{bubbles:true}))})
    await until(()=>document.querySelectorAll('.comment-table tbody tr').length===2,'platform filter')
    assert.ok([...document.querySelectorAll('.platform-label')].every(x=>x.textContent==='抖音'))
    await click(document.querySelector('.comment-table tbody tr'))
    await until(()=>document.querySelector('.opinion-editor'),'opinion editor opens')
    assert.equal(document.querySelectorAll('.opinion-edit-card').length,2)
    assert.ok(document.querySelector('.opinion-editor').textContent.includes('文档协作'))
    const attitude=document.querySelector('.opinion-edit-card select:nth-of-type(1)')
    assert.ok(attitude)
    await click(document.querySelector('[aria-label="关闭证据抽屉"]'))
    await navigate('/tasks/'+multi.id+'/report')
    await until(()=>document.querySelector('.platform-distribution tbody tr'),'platform report')
    assert.equal(document.querySelectorAll('.platform-distribution tbody tr').length,2)
    assert.ok(document.querySelector('.platform-distribution').textContent.includes('B站'))
    assert.ok(document.querySelector('.aspect-table'))
    assert.ok(document.querySelectorAll('.keyword-cloud button').length>=2)
    assert.ok(document.querySelector('.pain-card').textContent.includes('跨端同步卡顿'))
    await click(document.querySelector('.keyword-cloud button'))
    assert.ok(document.querySelector('.selected-evidence'))
    await click(document.querySelector('.evidence-item'))
    await until(()=>document.querySelector('.drawer'),'platform evidence drawer')
    assert.match(document.querySelector('.evidence-id').textContent,/抖音|B站/)
  }
  assert.deepEqual(errors,[],'no uncaught component/runtime errors')
  console.log('PASS: 7-platform selection, sort capabilities, budgets, settings, comments/context, exclude/restore, offline recount, reanalysis, report/evidence/export links'+(multi?', platform filters, V2 aspect sentiments, keywords, pain points, opinion editor and evidence panels':'')+' (jsdom + local API)')
}finally{
  if(root)await act(async()=>root.unmount())
  console.error=originalError
  await unlink(temp).catch(()=>{})
  dom.window.close()
}

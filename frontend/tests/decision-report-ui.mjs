// Read-only React/local API acceptance. Never starts model calls or modifies real labels.
import assert from 'node:assert/strict'
import {writeFile,unlink} from 'node:fs/promises'
import {fileURLToPath,pathToFileURL} from 'node:url'
import {dirname,resolve} from 'node:path'
import {build} from 'esbuild'
import {JSDOM} from 'jsdom'
import React,{act} from 'react'
import {createRoot} from 'react-dom/client'
const folder=resolve(dirname(fileURLToPath(import.meta.url)),'..'),temp=resolve(folder,'.decision-ui-check.mjs')
const base=process.env.INSIGHT_TEST_BASE_URL||'http://127.0.0.1:8765',id=process.env.INSIGHT_TEST_TASK_ID||'27587ab4-ab78-48af-8a42-7e8c41c04feb'
const nativeFetch=globalThis.fetch
const task=await (await nativeFetch(base+'/api/tasks/'+id)).json()
assert.equal(task.report.decision_version,3);assert.ok(['completed','partial'].includes(task.status))
const dom=new JSDOM('<html><body><div id="root"></div></body></html>',{url:base+'/#/tasks/'+id+'/report'})
globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.location=dom.window.location;globalThis.HTMLElement=dom.window.HTMLElement
globalThis.IS_REACT_ACT_ENVIRONMENT=true;globalThis.fetch=(url,options)=>nativeFetch(new URL(String(url),base),options)
const errors=[];dom.window.addEventListener('error',e=>errors.push(e.message));const originalError=console.error
console.error=(...args)=>{if(!String(args[0]).includes('not wrapped in act'))errors.push(args.join(' '))}
let root
async function until(check,label){const end=Date.now()+15000;while(Date.now()<end){await act(async()=>{await new Promise(r=>setTimeout(r,60))});if(check())return}throw Error('Timed out '+label)}
async function click(node){assert.ok(node);await act(async()=>{node.click();await new Promise(r=>setTimeout(r,60))})}
try{
 const bundle=await build({entryPoints:[resolve(folder,'src/App.tsx')],bundle:true,platform:'node',format:'esm',jsx:'automatic',external:['react','react-dom','lucide-react'],write:false})
 await writeFile(temp,bundle.outputFiles[0].text);const {default:App}=await import(pathToFileURL(temp).href)
 root=createRoot(document.getElementById('root'));await act(async()=>root.render(React.createElement(App)))
 await until(()=>document.querySelector('.decision-report'),'decision report')
 assert.equal(document.querySelector('.dr-appendix'),null)
 assert.equal(document.querySelectorAll('.dr-dimension-row').length,task.report.aspects.length)
 assert.equal(document.querySelectorAll('.dr-issue').length,Math.min(6,task.report.decision.issues.length))
 assert.equal(document.querySelectorAll('.dr-matrix circle').length,Math.min(6,task.report.decision.issues.length))
 assert.ok(document.querySelector('.dr-sample-line').textContent.includes(String(task.stats.included)))
 assert.ok(document.querySelector('.dr-source-line').textContent.includes('已知账户'))
 await click([...document.querySelectorAll('.dr-view-switch button')].find(b=>b.textContent.includes('完整研究版')))
 assert.ok(document.querySelector('.dr-appendix'))
 assert.ok([...document.querySelectorAll('.dr-toolbar a')].some(a=>a.href.endsWith('export/md?view=full')))
 assert.equal(document.querySelectorAll('.dr-full-evidence').length,task.stats.included)
 await click([...document.querySelectorAll('.dr-view-switch button')].find(b=>b.textContent.includes('精简决策版')))
 assert.equal(document.querySelector('.dr-appendix'),null)
 await click(document.querySelector('.dr-bars button:not(:disabled)'))
 assert.ok(document.querySelector('[aria-label="关联原文"]'))
 await click(document.querySelector('.dr-evidence-panel>div>button'))
 await until(()=>document.querySelector('.drawer'),'original evidence drawer')
 assert.ok(document.querySelector('.original-quote').textContent.trim())
 assert.ok(document.querySelector('.research-edit-fields'))
 if(task.report.decision.evidence_links)assert.ok(document.querySelector('.evidence-trace'))
 await click(document.querySelector('[aria-label="关闭证据抽屉"]'))
 assert.deepEqual(errors,[])
 console.log('PASS: real V3 report, brief/full modes, dynamic taxonomy charts, denominators, research priorities, all evidence index, original evidence drawer, independent research axes and export variants (read-only jsdom + real API)')
}finally{if(root)await act(async()=>root.unmount());console.error=originalError;await unlink(temp).catch(()=>{});dom.window.close()}

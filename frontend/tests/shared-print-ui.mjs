// Read-only parity acceptance: web and export render the same DecisionReport component.
import assert from 'node:assert/strict'
import {readFile,writeFile,unlink} from 'node:fs/promises'
import {fileURLToPath,pathToFileURL} from 'node:url'
import {resolve,dirname} from 'node:path'
import {build} from 'esbuild'
import React from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {JSDOM} from 'jsdom'
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..'),tmp=resolve(root,'.shared-print-test.mjs')
const base=process.env.INSIGHT_TEST_BASE_URL||'http://127.0.0.1:8765',id=process.env.INSIGHT_TEST_TASK_ID||'0de4b927-d03c-4704-a180-49b42f6ace01'
const task=await(await fetch(`${base}/api/tasks/${id}`)).json()
const bundle=await build({entryPoints:[resolve(root,'src/DecisionReport.tsx')],bundle:true,platform:'node',format:'esm',jsx:'automatic',external:['react','react-dom','lucide-react'],write:false})
await writeFile(tmp,bundle.outputFiles[0].text)
try{
 const {default:DecisionReport}=await import(pathToFileURL(tmp).href)
 for(const view of ['brief','full']){
  const web=new JSDOM(renderToStaticMarkup(React.createElement(DecisionReport,{task,onEvidence:()=>{},initialView:view})))
  const download=web.window.document.querySelector('a[download]')
  assert.ok(download&&download.textContent.includes('下载 HTML'))
  assert.ok(download.href.endsWith(`/export/html?view=${view}&download=true`))
  const response=await fetch(`${base}/api/tasks/${id}/export/html?view=${view}`);assert.equal(response.status,200)
  const html=await response.text(),print=new JSDOM(html)
  const texts=(dom,selector)=>[...dom.window.document.querySelectorAll(selector)].map(e=>e.textContent.trim())
  for(const selector of ['.dr-findings li p','.dr-section-heading h2:not(.dr-print-evidence h2)','.dr-bars button','.dr-legend span','.dr-dimension-row','.dr-issue h3','.dr-fact-grid','.dr-hypothesis','.dr-actions>div>h3','.dr-actions p','.dr-metric','.dr-source-method']){
   const printed=texts(print,selector).filter(t=>t!=='原文证据索引')
   assert.deepEqual(printed,texts(web,selector),`${view}: ${selector}`)
  }
  for(const selector of ['.dr-matrix circle','.dr-matrix g.matrix-point'])assert.equal(print.window.document.querySelectorAll(selector).length,web.window.document.querySelectorAll(selector).length)
  assert.equal(print.window.document.querySelectorAll('details:not([open])').length,0)
  assert.ok(print.window.document.querySelector('.dr-donut'))
  assert.ok(print.window.document.querySelector('.dr-print-evidence'))
  for(const a of print.window.document.querySelectorAll('a[href^="#"]'))assert.ok(print.window.document.getElementById(a.getAttribute('href').slice(1)))
  assert.equal(print.window.document.querySelectorAll('script').length,0)
  assert.ok(html.includes('print-color-adjust:exact'))
  assert.ok(print.window.document.querySelector('.dr-cover-meta').textContent.includes('报告 v'+task.version))
  if(view==='full')assert.equal(print.window.document.querySelectorAll('.dr-print-source').length,task.stats.included)
  web.window.close();print.window.close()
 }
 console.log('PASS: brief/full web/export parity for findings, dimensions, sentiment, bars, matrix, issues, recommendations, methods, expanded details, source anchors and snapshot version; no model calls.')
}finally{await unlink(tmp).catch(()=>{})}

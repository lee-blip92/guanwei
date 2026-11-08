import React from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {readFileSync} from 'node:fs'
import {join} from 'node:path'
import DecisionReport from '../src/DecisionReport'

const {report,view,version}=JSON.parse(readFileSync(0,'utf8'))
if(report.decision_version!==3||!['brief','full'].includes(view))throw Error('Invalid report snapshot')
const task={id:'snapshot',report,version,config:report.config}
const css=['report-base.css','report-decision.css','report-print.css'].map(name=>readFileSync(join(__dirname,name),'utf8')).join('\n')
const escape=(s)=>s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))
const content=renderToStaticMarkup(<DecisionReport task={task} onEvidence={()=>{}} initialView={view} printMode/>)
process.stdout.write(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escape(report.product)} 决策洞察</title><style>${css}</style></head><body class="report-print-page"><div class="report-print-controls"><p>与网页共用报告内容、图表和样式 · ${view==='brief'?'精简决策版':'完整研究版'}</p><button onclick="window.print()">打印 / 另存为 PDF</button><small>A4 分页；已展开详细内容与引用原文。打印时建议关闭浏览器页眉页脚。</small></div>${content}</body></html>`)

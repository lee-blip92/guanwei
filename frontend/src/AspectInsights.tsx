import {useState} from 'react'
import {ArrowUpRight,ChevronDown,Focus,MessageSquare,Quote,Sparkles,X} from 'lucide-react'
import type {Report} from './types'
import {platformName} from './platforms'

export default function AspectInsights({report:r,onEvidence}:{report:Report;onEvidence:(id:string)=>void}){
  const [selection,setSelection]=useState<{name:string;ids:string[]}|null>(null)
  const evidence=(ids:string[])=><div className="evidence-list">{ids.map(id=><button key={id} className="evidence-item" onClick={()=>onEvidence(id)}><Quote size={14}/><div><p>{r.evidence[id]?.text||'原文不可用'}</p><span>{platformName(r.evidence[id]?.platform||id.split(':')[0])} · {id}</span></div><ArrowUpRight size={13}/></button>)}</div>
  const [allAspects,setAllAspects]=useState(false)
  const pain=r.pain_points||[],needs=r.needs||[],strengths=r.strengths||[]
  return <div className="aspect-insights">
    <div className="opinion-summary"><span><Sparkles size={16}/>{r.opinion_count||0} 条独立观点</span><span>{r.aspects?.length||0} 个产品关注点</span><span>{pain.length} 类明确痛点</span><small>一条评论可以表达多个观点；以下数量按各自覆盖的评论去重。</small></div>
    <section className="report-section aspect-section"><div className="report-section-title"><Focus size={20}/><div><h2>用户在关注什么</h2><p>关注点覆盖率以 {r.stats.included} 条有效评论为分母；正负提及包含混合表达</p></div></div>
      {r.aspects?.length?<div className="aspect-table-wrap"><table className="aspect-table"><thead><tr><th>产品关注点</th><th>提及评论</th><th>正面</th><th>负面</th><th>依赖父评论附和</th><th/></tr></thead><tbody>{(allAspects?r.aspects:r.aspects.slice(0,10)).map(a=><tr key={a.name}><td><strong>{a.name}</strong><div className="aspect-share"><i style={{width:a.share+'%'}}/></div><small>有效评论覆盖率 {a.share}%</small></td><td>{a.count}</td><td className="positive-number">{a.positive_count}</td><td className="negative-number">{a.negative_count}</td><td>{a.agreement_count}</td><td><button className="text-action" onClick={()=>setSelection({name:a.name,ids:a.evidence_ids})}>原文<ArrowUpRight size={12}/></button></td></tr>)}</tbody></table></div>:<p className="no-claim">尚无可统计的产品关注点。</p>}
      {(r.aspects?.length||0)>10&&<button className="text-action" onClick={()=>setAllAspects(!allAspects)}>{allAspects?'收起关注点':`展开全部 ${r.aspects?.length} 个关注点`}<ChevronDown size={13}/></button>}
      <div className="keyword-heading"><h3>评论中的关键词</h3><span>AI 归纳短语的评论提及数，非字面词频</span></div><div className="keyword-cloud">{r.keywords?.slice(0,24).map(k=><button key={k.word} title={'覆盖 '+k.share+'% 的有效评论'} onClick={()=>setSelection({name:'关键词：'+k.word,ids:k.evidence_ids})}>{k.word}<b>{k.count}</b></button>)}</div>
      {selection&&<div className="selected-evidence"><header><strong>{selection.name} · {selection.ids.length} 条原文</strong><button aria-label="关闭关联原文" onClick={()=>setSelection(null)}><X size={15}/></button></header>{evidence(selection.ids)}</div>}
    </section>
    <section className="report-section pain-section"><div className="report-section-title"><MessageSquare size={20}/><div><h2>用户不满意什么，痛在哪里</h2><p>只收录明确表达的问题；担忧与提问不当作已经发生的故障</p></div></div>
      {pain.length?pain.map((p,index)=>{const topic=r.themes.find(t=>t.id===p.topic_id);return <details className="pain-card" key={p.topic_id}><summary><span className="pain-index">{String(index+1).padStart(2,'0')}</span><div><span className="pain-aspect">{p.aspect}</span><h3>{p.name}</h3><p>{p.count} 条评论 · 直接表达 {p.direct_count} · 上下文附和 {p.agreement_count}</p></div><strong>{p.share}<small>%</small></strong><ChevronDown size={15}/></summary><div className="pain-body"><dl><dt>用户表达的问题</dt><dd>{p.descriptions.join('；')||'见原文'}</dd><dt>明确提到的后果</dt><dd>{p.impacts.join('；')||'未知，样本未明确说明'}</dd><dt>用户明确期待</dt><dd>{p.expectations.join('；')||'未明确表达，不代替用户推测'}</dd><dt>建议尝试</dt><dd>{topic?.recommendation||'暂无通过证据检查的具体建议'}</dd><dt>如何验证</dt><dd>{topic?.hypothesis||'暂无生成的验证方案'}</dd></dl>{evidence(p.evidence_ids)}</div></details>}) : <p className="no-claim">本次尚未提取到有原文支持的明确痛点。规则预览不具备可靠的语义痛点识别能力。</p>}
      <p className="help">按本次样本提及数排列，供研究核查；不代表开发优先级或商业影响。</p>
    </section>
    <div className="experience-columns"><section><h3>用户满意什么</h3>{strengths.length?strengths.slice(0,8).map(s=><details key={s.topic_id}><summary><span>{s.name}</span><b>{s.count} 条</b></summary>{evidence(s.evidence_ids)}</details>):<p>暂无有支持的正面观点。</p>}</section><section><h3>用户明确提出的需求</h3>{needs.length?needs.slice(0,8).map(n=><details key={n.topic_id}><summary><span>{n.descriptions.join('；')||n.name}</span><b>{n.count} 条</b></summary>{evidence(n.evidence_ids)}</details>):<p>未提取到明确诉求；不会把系统猜测当作用户需求。</p>}</section></div>
    {!!r.quality?.repeated_text_group_count&&<details className="same-text-note"><summary>数据核查：{r.quality.repeated_text_group_count} 组同文，涉及 {r.quality.repeated_text_comment_count} 条不同 ID 的评论，全部保留<ChevronDown size={13}/></summary><p>同文可能是附和、引用或复制，无法仅凭文本判定重复用户。身份去重仍按平台＋评论 ID。</p>{r.quality.repeated_text_examples.map((g,i)=><div key={i}>{evidence(g.evidence_ids)}</div>)}</details>}
  </div>
}

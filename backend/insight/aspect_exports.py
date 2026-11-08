"""Render V2 computed statistics without asking a model to write numbers."""
import html
from . import db
from .aspect_analysis import effective_points,unique

def md_cell(value):return str(value).replace('|','\\|').replace('\n',' ')

def markdown_sections(r):
    if r.get('analysis_version')!=2:return []
    lines=['## 观点与关注点', '', f'共提取 {r["opinion_count"]} 条目标产品观点；数量按各项覆盖的唯一评论 ID 统计，一评可多观点。', '',
        '| 产品关注点 | 提及评论 | 有效样本占比 | 正面 | 负面 | 上下文附和 |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for a in r['aspects']:
        lines.append('| '+md_cell(a['name'])+' | '+' | '.join(str(a[k])+('%' if k=='share' else '') for k in ('count','share','positive_count','negative_count','agreement_count'))+' |')
    lines += ['', '正面/负面提及均可包含混合表达；附和保留但不等同于独立的详细问题描述。', '', '### 评论关键词', '',
        '关键词为模型归纳短语，数量为覆盖评论数，非字面词频。', '',
        '；'.join(f'{k["word"]}（{k["count"]} 条）' for k in r['keywords']) or '暂无', '', '## 用户明确表达的痛点', '']
    if r.get('generation_warnings'):lines[0:0]=['> '+x for x in r['generation_warnings']]+['']
    topics={t['id']:t for t in r['themes']}
    for p in r['pain_points']:
        t=topics[p['topic_id']]
        lines += [f'### {p["name"]} · {p["count"]} 条（{p["share"]}%）','',
            f'直接表达 {p["direct_count"]}；上下文附和 {p["agreement_count"]}。',
            '问题：'+'；'.join(p['descriptions']), '用户明确提到的后果：'+('；'.join(p['impacts'][:10]) or '未知'),
            '用户明确期待：'+('；'.join(p['expectations'][:10]) or '未明确表达'),
            '建议尝试：'+t['recommendation'],'如何验证：'+t['hypothesis'],
            '原文证据：'+'、'.join(p['evidence_ids']),'']
    if not r['pain_points']:lines += ['暂无有原文支持的明确痛点。规则预览不提供可靠的语义痛点识别。','']
    for key,title in [('strengths','用户满意点'),('needs','用户明确提出的需求')]:
        lines += ['## '+title,'']
        for p in r[key]:
            lines += [f'- {p["name"]}：{p["count"]} 条；'+'；'.join(p['descriptions']), '  原文证据：'+'、'.join(p['evidence_ids'])]
        if not r[key]:lines += ['本次未提取到相应的明确表达。']
        lines += ['']
    q=r['quality']
    lines += [f'同文核查：{q["repeated_text_group_count"]} 组，涉及 {q["repeated_text_comment_count"]} 条不同 ID 评论，均保留；仅按平台＋评论 ID 去重。','']
    return lines

def html_sections(r):
    if r.get('analysis_version')!=2:return ''
    esc=lambda value:html.escape(str(value),quote=True)
    def refs(ids):return '、'.join(f'<a href="#ev-{esc(eid)}">{esc(eid)}</a>' for eid in ids)
    rows=''.join('<tr><td>'+esc(a['name'])+'</td>'+''.join('<td>'+esc(a[k])+('%' if k=='share' else '')+'</td>' for k in ('count','share','positive_count','negative_count','agreement_count'))+'</tr>' for a in r['aspects'])
    body='<section><h2>用户在关注什么</h2><p>目标产品观点 '+str(r['opinion_count'])+' 条；以下统计按各项覆盖的唯一评论计数，一评可多观点。</p><table><thead><tr><th>关注点</th><th>提及</th><th>样本占比</th><th>正面</th><th>负面</th><th>附和</th></tr></thead><tbody>'+rows+'</tbody></table><small>正负提及均可包含混合表达；附和不等于独立的详细问题描述。</small><h3>评论关键词</h3><p>'+esc('；'.join(f'{k["word"]}（{k["count"]} 条）' for k in r['keywords']) or '暂无')+'</p><small>AI归纳短语的覆盖评论数，非字面词频。</small></section>'
    if r.get('generation_warnings'):body='<p>'+esc('；'.join(r['generation_warnings']))+'</p>'+body
    topics={t['id']:t for t in r['themes']}
    body+='<section><h2>用户明确表达的痛点</h2>'
    for p in r['pain_points']:
        t=topics[p['topic_id']]
        body+='<article><h3>'+esc(p['name'])+' · '+str(p['count'])+' 条 / '+str(p['share'])+'%</h3><p>'+esc('；'.join(p['descriptions']))+'</p><small>直接表达 '+str(p['direct_count'])+'；上下文附和 '+str(p['agreement_count'])+'</small><p>用户明确后果：'+esc('；'.join(p['impacts'][:10]) or '未知')+'</p><p>明确期待：'+esc('；'.join(p['expectations'][:10]) or '未表达')+'</p><p>建议：'+esc(t['recommendation'])+'</p><p>验证：'+esc(t['hypothesis'])+'</p><small>证据：'+refs(p['evidence_ids'])+'</small></article>'
    if not r['pain_points']:body+='<p>尚无有支持的明确痛点。规则预览不具备可靠的语义痛点识别。</p>'
    body+='</section>'
    for key,title in [('strengths','用户满意点'),('needs','用户明确提出的需求')]:
        body+='<section><h2>'+title+'</h2>'
        for p in r[key]:body+='<article><h3>'+esc(p['name'])+' · '+str(p['count'])+' 条</h3><p>'+esc('；'.join(p['descriptions']))+'</p><small>证据：'+refs(p['evidence_ids'])+'</small></article>'
        if not r[key]:body+='<p>本次未提取到明确表达。</p>'
        body+='</section>'
    q=r['quality']
    body+=f'<p>同文核查：{q["repeated_text_group_count"]} 组、{q["repeated_text_comment_count"]} 条不同 ID 评论，全部保留；仅按平台＋评论 ID 去重。</p>'
    return body

def csv_fields(item,report):
    points=effective_points(item);owned=[p for p in points if p['product_scope']=='target']
    return {'analysis_version':item['effective'].get('analysis_version',1),
        'keywords':' | '.join(unique(k for p in owned for k in p['keywords'])),
        'aspects':' | '.join(unique(p['aspect'] for p in owned)),
        'pain_points':' | '.join(unique(p.get('pain_point') for p in owned if p['kind']=='problem')),
        'expressed_impacts':' | '.join(unique(p.get('impact') for p in owned)),
        'explicit_requests':' | '.join(unique(p.get('expected_outcome') for p in owned)),
        'opinion_points_json':db.dumps(points),
        'report_topics':' | '.join(t['name'] for t in (report or {}).get('themes',[]) if item['evidence_id'] in t['evidence_ids'])}

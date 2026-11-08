"""One structured report, two reading depths; no model-generated statistics."""
import re
from datetime import datetime,timezone,timedelta

def cell(value):return str(value).replace('|','\\|').replace('\n',' ')
def claim_text(claim,fallback='现有证据不足，先补充具体场景。'):
    return re.sub(r'^(?:(?:待验证潜在需求|待验证原因假设|待验证|假设)[:：]\s*)+','',claim['text']) if claim else fallback
def display_time(value):
    return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M（北京时间）')
def readable_warnings(r):
    warnings=[w for w in r.get('generation_warnings',[]) if not w.startswith('模型输出结构不合格')]
    warnings=['本报告沿用已完成的AI研究结果；分类与统计经程序复算，文案仅保留证据未变化的内容。' if w.startswith('仅离线重算') else w for w in warnings]
    if len(warnings)!=len(r.get('generation_warnings',[])) and not any('研究标注未通过' in w for w in warnings):
        warnings.append('部分模型输出未通过校验；已逐项保留可用结果，未确认内容不支撑结论。')
    return warnings
def coverage(g):
    users='未知' if g['known_user_count'] is None else str(g['known_user_count'])+'个已知账户'
    return f"{g['count']}条评论 / {g['point_count']}条观点提及 / {g['thread_count']}个可解析讨论串 / {users}；用户ID覆盖{g['user_id_comments']}/{g['user_id_denominator']}条，串关系未知{g['thread_unknown_comments']}条"

def brief_evidence_ids(r):
    """Include every source cited by the brief, including contrary evidence."""
    ids=[e for c in r['findings'] for e in c['evidence_ids']]
    for strength in r['strengths'][:3]:ids.extend(strength['evidence_ids'])
    for issue in r['decision']['issues'][:6]:
        ids.extend(issue['representative_ids'])
        if issue['disputed']:
            ids.extend(issue['opposing_positive_ids'][:2]);ids.extend(issue['opposing_negative_ids'][:2])
        for claim in [issue.get(k) for k in ('observation','deeper_need','direction','validation')]+issue['cause_hypotheses']:
            if claim:ids.extend(claim['evidence_ids'])
    return list(dict.fromkeys(ids))

def markdown(r,view='brief'):
    d=r['decision'];s=r['stats'];top=d['issues'][:6]
    refs=lambda ids:'、'.join(f'[{eid}](#证据-{eid.replace(":","-")})' for eid in ids)
    lines=[f'# {r["product"]} · 产品反馈决策洞察', '', '精简版' if view=='brief' else '完整版（含研究附录）',
        f'生成时间：{display_time(r["generated_at"])}；分析：{r["model"]}。',
        '数据来源：'+('演示模式，人工构造样本' if r['source']=='demo' else r.get('source_name') or '当前任务真实采集/导入'),'',
        '## 一、执行摘要','',f"本次{ s['notes']}条来源内容、{s['raw']}条原始评论，去重后{s['unique']}条，纳入{s['included']}条；结论仅代表本次样本。",'']
    for c in r['findings'][:5]:lines+=['- '+c['text']+' 证据：'+refs(c['evidence_ids'])]
    if not r['findings']:lines+=['暂未形成通过证据检查的整体归纳；以下仅列可核查的议题统计，不补造摘要。']
    lines+=['','明确优势：'+'；'.join(f'{x["name"]}（{x["count"]}条明确正面体验）' for x in r['strengths'][:3]) if r['strengths'] else '明确优势：证据不足。',
        '优先核查：'+'；'.join(i['priority']['level']+' '+i['name'] for i in top[:3]) if top else '暂无可支持的核心问题。',
        '研究可信度：基于可追溯原话与程序统计，尚无独立人工准确率；已知账户/讨论串不是独立故障人数。','']
    if readable_warnings(r):lines+=['> '+w for w in readable_warnings(r)]+['']
    lines+=['## 二、用户反馈全貌','',
        f"原始{s['raw']}；去重后{s['unique']}；有效{s['included']}；排除{s['excluded']}；待确认{s['uncertain']}；待处理{s['pending']}。",'',
        '来源多样性：'+coverage(d['sample_evidence']), '',
        '| 一级产品维度 | 覆盖评论 | 有效评论覆盖率 | 明确正面 | 明确负面 | 负面占本维度 |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for a in r['aspects']:lines+=['| '+cell(a['name'])+f" | {a['count']} | {a['share']}%（分母{s['included']}） | {a['positive_count']} | {a['negative_count']} | {a['negative_share']}%（分母{a['count']}） |"]
    labels={'positive':'正面','negative':'负面','mixed':'混合','neutral':'中性','unknown':'未知'}
    lines+=['','情绪分布（分母为有效评论'+str(s['included'])+'条）：'+'；'.join(f'{labels[x["key"]]} {x["count"]}条/{x["share"]}%' for x in d['charts']['sentiment']),
        '意图覆盖（可多选，分母同为有效评论）：'+'；'.join(f'{x["name"]} {x["count"]}条/{x["share"]}%' for x in d['intent_distribution']),
        '排除/待确认原因：'+'；'.join(f'{x["name"]} {x["count"]}条' for x in d['reason_summary']),
        '同一评论在同一维度只覆盖一次；正负体验可交叉。需求、问题、观点、账户与讨论串不能相互加总。','',
        '## 三、核心用户痛点与需求','']
    for i in top:
        lines += [f'### {i["name"]}', '',claim_text(i['observation'],'样本具体表现：'+'；'.join(i['variants'][:3])),
            '证据范围：'+coverage(i),'典型场景：'+('；'.join(i['scenarios'][:3]) or '未知'),
            '用户明确后果：'+('；'.join(i['impacts'][:3]) or '未明确表达，不能推演损失'),
            '用户明确方案/期待：'+('；'.join(i['user_solutions'][:3]) or '未明确表达'),
            '潜在需求（待验证）：'+claim_text(i['deeper_need'],'尚无法判断'),
            '原因假设（未验证）：'+('；'.join(claim_text(c) for c in i['cause_hypotheses']) or '尚无足够证据，不自动补根因'),
            '代表原文证据：'+refs(i['representative_ids'])]
        if i['disputed']:lines+=['分化反馈：同议题中正负体验并存，是否同场景尚待验证。正面证据：'+refs(i['opposing_positive_ids'][:2])+'；负面证据：'+refs(i['opposing_negative_ids'][:2])]
        lines+=['']
    lines+=['## 四、产品优化与研究优先级','', '| 优先级 | 核心议题 | 评论 | 可解析串 | 影响线索 | 判断依据 |','| --- | --- | ---: | ---: | --- | --- |']
    for i in top:lines+=['| '+i['priority']['level']+' | '+cell(i['name'])+f" | {i['count']} | {i['thread_count']} | "+cell(i['priority']['severity'])+' | '+cell('；'.join(i['priority']['reasons']))+' |']
    lines+=['','这些优先级用于核查与研究，不代表必须立即开发。业务价值与开发成本均未知。','']
    for i in top:
        lines += ['- **'+i['name']+'**：'+claim_text(i['direction'],'先核查具体问题与场景，暂不指定功能改造。'),
            '  验证：'+claim_text(i['validation'],'补充任务观察/访谈，确认问题是否发生及适用条件。'),
            '  可观察指标：'+('、'.join(i['metrics']) or '待问题核实后确定，不编造目标值')]
    lines += ['','## 五、研究限制与后续行动','']+['- '+x for x in r['limitations']]+['']+['- '+x for x in d['counting_rules']]+['',d['comparison_limit'],'']
    if view=='full':
        lines+=['## 六、附录：完整分类与具体议题','']
        for domain in d['taxonomy']:
            lines+=['### '+domain['name'],'',domain['definition']]+['- '+c['name']+'：'+c['definition'] for c in domain['children']]+['']
        lines+=['| 二级议题 | 一级维度 | 评论 | 观点提及 | 可解析串 | 原始细分表现 |','| --- | --- | ---: | ---: | ---: | --- |']
        for t in r['themes']:lines+=['| '+cell(t['name'])+' | '+cell(t['aspect'])+f" | {t['count']} | {t['point_count']} | {t['thread_count']} | "+cell('；'.join(t['feedback']))+' |']
        lines+=['','### 研究优先级规则','']+['- '+x for x in d['priority_rules']]
        lines+=['','### 质量核查与未解决项','',f"程序证据/统计检查{len(d['quality_checks'])}项；规则检查不能代替人工语义评估。",f"待映射观点{len(d['unmapped_unit_ids'])}条；近似标签待复核{len(d['label_review'])}组；记录保守修正{len(d['adjustments'])}项。",'']
        for x in d['label_review']:lines+=['- 待核查标签：'+' / '.join(x['labels'])+'；'+x['reason']]
        for x in d.get('generated_review',[]):lines+=['- 未发布表述（待复核）：'+x['text'],'  原因：'+x['reason'],'  原始引用ID（不代表支持）：'+'、'.join(x.get('evidence_ids',[]))]
        for x in d['adjustments']:lines+=['- 保守修正：'+x['evidence_id']+' · '+x['rule']+' · '+x['action']]
        lines+=['','### 原文证据索引','']
        for eid,e in r['evidence'].items():lines += ['#### 证据 '+eid.replace(':','-'),'', '> '+(e.get('text') or '').replace('\n','\n> '),
            '平台：'+e['platform']+'；来源：'+(e.get('note_title') or '未获取'),'父评论（上下文）：'+(e.get('parent_text') or '未获取/无'),
            '原文链接：'+(e.get('source_url') or '未获取'),'']
    else:
        lines+=['完整分类、低频议题和核查记录请查看完整版；CSV包含全部去重评论。','','### 本文引用原话节选','']
        cited=brief_evidence_ids(r)
        for eid in cited:
            e=r['evidence'][eid];text=e.get('text') or ''
            lines+=['#### 证据 '+eid.replace(':','-'),'', '> '+text[:180].replace('\n','\n> ')+('（节选）' if len(text)>180 else ''),
                '原文链接：'+(e.get('source_url') or '未获取'),'']
    return '\n'.join(lines)+'\n'

def print_html(r,view='brief',version=None):
    """Use exactly the React component/styles shipped by the frontend build."""
    from .report_renderer import render_html
    return render_html(r,view,version)

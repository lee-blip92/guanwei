"""Pure data/eligibility contract shared by workbench, reports, CSV and review."""
import copy
import hashlib
import json

INTENTS={'positive_experience':'正面体验','negative_experience':'负面体验','feature_request':'功能需求','usage_question':'使用疑问',
 'fault_report':'故障反馈','improvement_suggestion':'改进建议','use_scenario':'使用场景','factual_statement':'事实说明','irrelevant':'无关内容','uncertain':'无法判断'}
OBJECTS={'product':'产品本身','feature':'产品功能','tutorial':'教程内容','creator':'博主','platform_content':'平台内容','third_party':'第三方服务','other':'其他对象','uncertain':'对象待确认'}
RESEARCH_FIELDS=('product_scope','intents','evaluation_object','sentiment','stance','epistemic','severity','severity_quote','group_quote')
CORE_FIELDS=('product_scope','aspect','topic','kind','sentiment','basis','scenario','pain_point','impact','expected_outcome','keywords','quote','context_quote')

def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def original_points(item):
    points=copy.deepcopy(item.get('overrides',{}).get('points',(item.get('analysis') or {}).get('points',[])))
    o=item.get('overrides',{})
    if 'points' not in o:
        for p in points:
            if o.get('relevance') in ('target','other_product','uncertain'):p['product_scope']=o['relevance']
            if o.get('sentiment'):p['sentiment']=o['sentiment']
    return points

def source_hash(item):
    return digest({'text':item['text'],'parent':item.get('parent_text'),'note':item.get('note_body'),'title':item.get('note_title'),
        'points':original_points(item),'parent_blocked':item.get('parent_blocked',False)})

def eligible(p):
    return p.get('semantic_state') not in ('non_product','uncertain') and p.get('product_scope')=='target' and p.get('evaluation_object') in ('product','feature') and \
        bool(set(p.get('intents',[])) & (set(INTENTS)-{'irrelevant','uncertain','factual_statement'}))

def satisfaction(p):
    return p.get('semantic_state')!='overall' and eligible(p) and p.get('epistemic')=='experience' and 'positive_experience' in p.get('intents',[]) and p['sentiment'] in ('positive','mixed')

def problem(p):
    return p.get('semantic_state')!='overall' and eligible(p) and p.get('epistemic')=='experience' and bool(set(p.get('intents',[])) & {'negative_experience','fault_report'}) and p['sentiment'] in ('negative','mixed')

def reason_code(item):
    e=item['effective']
    if e.get('excluded'):return 'manual_excluded','人工排除'
    if e.get('suspected_ad'):return 'suspected_ad','疑似广告'
    if not item.get('analysis'):return 'pending','尚未分析'
    if (item['analysis'] or {}).get('opinion_audit',{}).get('state') in ('pending','needs_review'):return 'review_required','结构/观点复核待确认'
    if e.get('research_status')=='needs_review':return 'research_review','研究标注未通过，待复核'
    if e.get('relevance')=='uncertain':return 'uncertain','产品或评价对象无法确定'
    points=e.get('points',[])
    if points and all(p.get('semantic_state')=='non_product' for p in points):return 'non_product','教程/博主/第三方等非产品评价'
    if points and any(p.get('semantic_state')=='uncertain' for p in points) and not any(eligible(p) for p in points):return 'semantic_uncertain','任务、对象或问题依据待确认'
    if points and all(p.get('evaluation_object') not in ('product','feature','uncertain') for p in points):return 'non_product','教程/博主/第三方等非产品评价'
    if e.get('relevance')=='other_product':return 'other_product','其他产品'
    if points and all(p.get('epistemic') in ('information','speculation','uncertain') for p in points):return 'information','纯资讯、猜测或无明确体验'
    return 'no_feedback','无实质产品反馈'

def apply_research(item,record):
    """Does not mutate raw analysis. Research annotations are an auditable derived view."""
    points=original_points(item);e=item['effective'];o=item.get('overrides',{})
    valid=record and record.get('source_hash')==source_hash(item)
    manual=bool('points' in o and points and all(p.get('intents') and p.get('evaluation_object')!='uncertain' for p in points))
    if not valid and not manual:
        if record:
            e['research_status']='needs_review';e['has_feedback']=False;e['relevance']='uncertain'
            e['reason']='原始观点或上下文已修改，独立研究标注待更新；暂不支撑决策报告。'
        return
    changes={p['point_index']:p for p in record.get('points',[])} if valid else {}
    merged=[]
    for i,p in enumerate(points):
        a=changes.get(i,{})
        if not manual:p.update({k:a[k] for k in RESEARCH_FIELDS if k in a})
        semantic=(record or {}).get('semantic_points',{}).get(str(i)) if valid else None
        if semantic:
            p.update(semantic_state='specific' if manual else semantic['state'],semantic_revision=record.get('semantic_revision'),meaning=semantic)
            if not manual:
                p.update(scenario=semantic.get('scenario_quote'),scenario_label=semantic.get('scenario_label'),
                    impact=semantic.get('impact_quote'),expected_outcome=semantic.get('expectation_quote'),
                    manifestation=semantic.get('manifestation_quote'),severity=semantic.get('impact_kind','unknown'),severity_quote=semantic.get('impact_quote'))
        if 'points' in o:p['product_scope']=o['points'][i]['product_scope']
        if item.get('parent_blocked') and p['basis']=='agreement':p['product_scope']='uncertain'
        merged.append(p)
    status='checked' if manual else record.get('status','needs_review')
    if status!='checked':
        for p in merged:p['product_scope']='uncertain'
    e['points']=merged;e['research_status']=status
    good=[p for p in merged if eligible(p)]
    e['has_feedback']=bool(good)
    if good:
        e['relevance']='target';e['themes']=list(dict.fromkeys(p['aspect'] for p in good))
        s={p['sentiment'] for p in good};pos=bool(s & {'positive','mixed'});neg=bool(s & {'negative','mixed'})
        e['sentiment']='mixed' if pos and neg else 'positive' if pos else 'negative' if neg else 'neutral' if 'neutral' in s else 'unknown'
    else:
        e['relevance']='uncertain' if status!='checked' or any(p.get('semantic_state')=='uncertain' or p.get('product_scope')=='uncertain' or p.get('evaluation_object')=='uncertain' for p in merged) else \
            'other_product' if merged and all(p['product_scope']=='other_product' for p in merged) else 'irrelevant'
        e['themes']=[];e['sentiment']='unknown'
    semantic_reasons=list(dict.fromkeys(p['meaning']['reason'] for p in merged if p.get('meaning') and p.get('semantic_state') in ('non_product','uncertain')))
    e['reason']=('人工研究修正' if manual else '；'.join(semantic_reasons) or record.get('reason') or '按独立意图、评价对象与体验依据重新筛选。')

def thread_keys(items):
    by={(x['platform'],x.get('comment_id')):x for x in items if x.get('comment_id')}
    result={}
    for item in items:
        current=item;seen=set();root=None
        while current and current.get('comment_id'):
            key=(current['platform'],current['comment_id'])
            if key in seen:break
            seen.add(key);parent=current.get('parent_comment_id')
            if not parent:
                if current.get('parent_relation_known'):root=current['platform']+':thread:'+current['comment_id']
                break
            current=by.get((current['platform'],parent))
        result[item['evidence_id']]=root
    return result

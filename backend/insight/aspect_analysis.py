"""V2: evidence-bound opinions, semantic issues, programmatic statistics, grounded advice."""
import asyncio
import copy
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pydantic import ValidationError
from . import db
from .analysis import ModelClient, ModelFailure, ModelOutputFailure, validation_issues, SYSTEM, payload_comment, rule_extract, validate_narrative, verify_support, provider_options
from .schemas import AspectExtraction, CommentUnderstanding, CommentOpinionAudit, OpinionGrouping, OpinionPoint, TopicAdviceBatch, Narrative, OpinionAuditBatch

VERSION='absa-2.1'
EXTRACT_CACHE_VERSION='absa-2.0'
UNDERSTAND='''逐条理解软件产品评论，按关注点拆成独立观点，输出 items。
每个输入 evidence_id 恰好出现一次。先判断 relevance 和 suspected_ad，再提取 points；没有具体体验/问题/需求/实质提问时 points=[]，不要把“蹲、哈哈、求关注”等算产品体验。
转载公告、机器人播报、单纯求提醒不等于亲身使用反馈，仅有这类内容时 points=[]；suspected_ad 需要营销迹象，不能把所有转述都标广告。
单纯表情不代表已发生某个故障。无法判断短回复具体附和哪一点时标记不确定，不补出痛点。
一评多观点：例如“文档好用但手机同步慢”，分别提取文档正面体验、多端同步负面问题；不能把整体负面情绪复制给所有维度。
每个观点标记 product_scope=target/other_product/uncertain，比较评论可同时包含不同产品的观点，无法确定归属时 uncertain，不强行丢掉明确属于目标产品的另一观点。
aspect 是产品维度，例如多端同步、权限管理、文档编辑；topic 是具体反馈，例如“手机编辑后电脑端迟迟未同步”，不能只用“体验问题”等宽泛词。表达相同事件可使用相同 topic。
kind=experience/problem/request/question/other；sentiment=positive/negative/mixed/neutral/unknown。反问、否定、讽刺需结合语义，不作词典匹配。
pain_point 只在 kind=problem 且用户确实表达问题时填写；不能把担忧、提问当作已发生问题。impact 只填用户明确表达的后果，expected_outcome 只填明确诉求，不推演损失或用户意图。scenario 只填已知场景。缺失一律 null。
keywords 提取最多6个有区分度的中文短语，可作轻微语义规范化，避免产品名、好用、体验等空泛词。
quote 必须复制当前 text 中一个连续原文片段，建议不超过100字，不改标点或错字。
“我也是、更新后更严重”等附和可结合父评论提取，但 basis=agreement，context_quote 必须复制 parent_text 中支持该观点的连续原文；quote 仍为当前回复原话。没有可引用父文本就不能给短附和补造问题。
其他观点 basis=direct，context_quote 可为 null。不要把上级评论的细节当成当前用户直接表述。'''
AUDIT='''独立复核每条评论及其候选观点，不默认初提取正确。只用当前评论和显式父评论，输出 items。
每个 evidence_id 恰好一次。content_class=feedback/information_only/non_feedback/uncertain。
转载公告、机器人播报、预测发布日期/额度变化等纯资讯没有表达自己的体验、评价、诉求或实质产品提问时，information_only；点赞/蹲/求提醒/单纯表情为 non_feedback。相关资讯不等于使用体验。
如果整条不是具体反馈，checks 可为空，所有候选观点均会被程序排除。不能将公告中“额度翻倍、月费不变”自动判作满意体验。
向博主、评论区机器人或其他网友说“重置时告诉我/@点点/蹲/求提醒”是资讯订阅或社交请求，不是明确要求目标软件增加功能，判 non_feedback；直接希望产品提供提醒/倒计时才是产品诉求。请他人讲解工作流可保留为 question，但不能改写成用户已要求官方新增教程。
逐观点核对对象和责任边界：同公司另一个产品、第三方中转、代理商或运营商的问题不能自动归因给目标产品。无法确认发生在目标产品本身的观点判 uncertain；明确属于另一产品或外部服务的观点判 unsupported。某个外部服务观点被排除，不影响当前评论中另一条明确属于目标产品的体验。
不能仅因“额度增加、价格不变、已经重置”等事件看似有利，就推断用户满意。positive 需要用户明确的赞赏或正面感受；仅报告状态、数字变化或表示困惑时用 neutral/unknown，不替用户表达态度。
feedback 的每个候选点从0开始 point_index 恰好返回一次。verdict=supported/unsupported/uncertain，并说明 reason。
检查具体话题、产品归属和痛点是否确实得到原文支持；给出具体后果、根因或用户态度但原文没有时不能通过。用户在说自己做事慢，不等于产品运行慢或产品有重置故障。自我纠正不能当成新产品痛点。
可以校正 kind/sentiment/basis。纯提问和担忧不能标作已发生 problem，带问号的真实感受要结合原意判断，用户认为和已核实事实必须区分。
当前评论独立表达的具体状态为 direct；“是的、对啊、我也是”等依赖父评论才有具体含义时为 agreement，此时 context_quote 复制父评论中支持该点的连续原文。
scenario_quote/impact_quote/expected_outcome_quote 只复制用户明示的场景/实际后果/明确诉求的连续片段，不改写，不推断；没有则 null。direct 的这些片段只能来自当前评论；agreement 也可来自该父评论。
不提出新观点，不重新编写痛点或补造影响。不能把情绪反应直接升级为故障经历。'''
AUDIT_VERSION=hashlib.sha256(AUDIT.encode()).hexdigest()[:16]
# Protocol clarification only; existing semantically validated audit caches remain valid.
AUDIT_PROTOCOL='''输入的每条 candidate_points 已显式给出 point_index，直接复制，不自行重新编号。
expected_point_indices 是本评论允许的完整序号集合。content_class=feedback 时 checks 必须恰好覆盖该集合，不漏、不重、不新增；非 feedback 的 checks 返回 []。
candidate_points=[] 时 checks 必须为 []，即使你认为原文仍值得研究，也不能在复核阶段新增观点。
例如一个候选点 point_index=0，只能返回一个 point_index=0；不能返回1。两个点0、1需各返回一次。'''
MERGE='''将观点候选归并为具体话题，输出 groups，每组 aspect/name/member_ids。
每个候选 id 必须恰好出现一次，不产生额外成员。候选附有具体问题、原文与情绪；不能只比较标签字面。
只合并同一产品维度、同一问题或同一正面体验的同义表达。比如“手机修改电脑不更新”和“跨端同步延迟”可合并；“同步失败”和“权限入口难找”不可合并。
同一宽泛维度内不同事件仍分开；不为了减少分组而合并，不要求最小组大小，不预设固定主题数。
aspect 使用统一具体的维度名，name 描述具体话题。不虚构事件、数量、严重度或业务优先级。'''
ADVISE='''逐个话题给出一条具体改进建议和一个可执行验证方案，输出 topics。
每个 topic_id 恰好出现一次。recommendation/validation 为 {text,evidence_ids} 或 null。
建议说明针对哪个实际反馈、可尝试什么产品改动；验证说明如何复现/访谈/观察，并给出适合核查的指标名称，不能编造目标数值。
可以提出新解决方案，但它是待验证建议，不能声称根因已经确定、修改一定有效或知道开发成本。前提、使用场景须由该话题给定的证据支持；只引用本话题证据。
正面体验可建议保留已有价值并验证适用边界。不足以提出具体建议时返回 null；不输出每个话题都相同的“建议优化体验”。不得自由填写样本数量或占比。'''
SYNTHESIZE='''基于程序已计算的关注点、具体问题、正面体验、需求和引用原文，归纳产品样本的核心发现与满意点。
输出 findings/positives/recommendations/questions，每项为 text/evidence_ids。recommendations/questions 必须为空数组，由逐话题建议补充。
findings 应指出具体的场景、关注点与体验，不写“用户关心产品体验”这种泛化文案。positives 只总结原文明示的正面价值。
所有观察限定为“本次样本中/有评论表达”，不写“用户普遍/大多数用户”等泛化判断；用户认为的原因不能升级成已证实的产品根因。
统计用于了解样本结构，不在 text 中自由填写数量、比例或评分；正式数字由界面模板直接读取统计字段。
引用只取本次给定证据，不能把少量原文之外的具体细节补进结论。用户担忧/传闻不等于已经证实的产品故障。'''
SUPPORT_V2='''检查每条生成文本，只使用该条 cited_comments，逐个 claim_id 返回 supported/unsupported/uncertain 及 reason。
findings/positives：所有事实必须得到引用支持。用户主观感受或担忧不升级为已核实产品事实；少量引用不能证明“用户普遍/全部用户”。
recommendations：只检查触发建议的事实与产品归属是否有依据。允许提出评论未写过的新功能或改动，只要明确为建议/可尝试且未保证效果，不要求解决方案本身已出现在原文。
questions：验证方案和待验证假设不应已经有结论；允许提出新的实验步骤、观察指标和访谈问题。只核查问题前提来自引用、没有把未验证结果当事实，不能仅因用户没提实验或实验尚未做就判 unsupported。
任何段落都不能把第三方中转或同公司其他产品问题归因给目标产品。不要将对博主/平台机器人的求提醒直接包装成已经确认的软件功能需求。只支持部分事实也要标 unsupported。'''

async def semantic_call(client,prompt,payload,schema,validate,checkpoint,sanitize=None):
    original=prompt
    for attempt in range(2):
        await checkpoint();result=await client.call(prompt,payload,schema)
        try:validate(result);return result
        except ModelFailure as exc:
            if attempt:
                if sanitize:return sanitize(result)
                raise ModelOutputFailure(schema.__name__,[str(exc)],result.model_dump()) from exc
            prompt=original+'\n上次输出被程序拒绝：'+str(exc)+'。请完整修正本次输出，保留原输入 ID；结论使用定性表达，不抄写数字统计或百分比。'

def normal(value):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(value or ''))).strip()

def unique(values):return list(dict.fromkeys(v for v in values if v))

def overall(points):
    sentiments={p['sentiment'] for p in points}
    pos=bool(sentiments & {'positive','mixed'});neg=bool(sentiments & {'negative','mixed'})
    return 'mixed' if pos and neg else 'negative' if neg else 'positive' if pos else 'neutral' if 'neutral' in sentiments else 'unknown'

def validate_points(points,item,strict_literals=False):
    for point in points:
        p=point.model_dump() if hasattr(point,'model_dump') else point
        if not p['quote'].strip() or p['quote'] not in item['text']:
            raise ModelFailure(f'观点引用不是当前评论的连续原文（{item["evidence_id"]}）')
        if p.get('context_quote') and p['context_quote'] not in (item.get('parent_text') or ''):
            raise ModelFailure(f'观点的父评论引用不匹配（{item["evidence_id"]}）')
        if p['basis']=='agreement' and not p.get('context_quote'):
            raise ModelFailure(f'附和观点缺少可核查父评论原文（{item["evidence_id"]}）')
        if p.get('pain_point') and p['kind']!='problem':
            raise ModelFailure('只有明确的问题观点可以填写痛点，提问与担忧不得作为已发生问题')
        if strict_literals:
            sources=[item['text']]+([item.get('parent_text') or ''] if p['basis']=='agreement' else [])
            for field in ('scenario','impact','expected_outcome','severity_quote','group_quote'):
                if p.get(field) and not any(p[field] in source for source in sources):
                    raise ModelFailure(f'{field} 必须为允许来源中的连续原文，未明确表达则留空')

def labels_from_points(points,relevance='target'):
    owned=[p for p in points if p['product_scope']=='target']
    if owned:relevance='target'
    elif points:relevance='uncertain' if any(p['product_scope']=='uncertain' for p in points) else 'other_product'
    return {'relevance':relevance,'sentiment':overall(owned),'themes':unique(p['aspect'] for p in owned),
            'has_feedback':bool(owned),'scenario':'；'.join(unique(p.get('scenario') for p in owned)) or '未知',
            'feedback':'；'.join(unique(p['topic'] for p in owned))[:800]}

def validate_extraction(result,batch,cfg):
    expected={x['evidence_id']:x for x in batch};ids=[x.evidence_id for x in result.items]
    if len(ids)!=len(set(ids)) or set(ids)!=set(expected):raise ModelFailure('观点提取返回缺失、重复或未知评论 ID')
    values=[]
    for row in result.items:
        item=expected[row.evidence_id];value=row.model_dump();points=value['points'];adjustments=[]
        for index,p in enumerate(points):
            if p['kind']!='problem' and p.get('pain_point'):
                adjustments.append({'point_index':index,'field':'pain_point','value':p['pain_point'],'reason':'非问题类型不能支撑已发生痛点，已清空并交独立复核'})
                p['pain_point']=None
        validate_points(points,item)
        value['extraction_adjustments']=adjustments
        stop={normal(v).casefold() for v in [cfg['product'],*cfg.get('aliases',[]),'产品','用户','功能','体验','好用','不好用','问题','反馈']}
        for p in points:
            p['aspect']=normal(p['aspect']);p['topic']=normal(p['topic'])
            if not p['aspect'] or not p['topic']:raise ModelFailure('观点维度或话题不能为空')
            p['keywords']=unique(normal(k)[:30] for k in p['keywords'] if normal(k).casefold() not in stop and len(normal(k))>=2)
        value.update(labels_from_points(points,value['relevance']))
        value['quote']=points[0]['quote'] if points else item['text'][:80]
        value['analysis_version']=2
        values.append(value)
    return values

def rule_understanding(item,cfg):
    base=rule_extract(item,cfg);points=[]
    inherited=bool(item.get('parent_text') and re.match(r'^(我也是|同感|\+1|一样|更新后更严重|确实|同样)',item['text'].strip()))
    if base['relevance']=='target' and item['text'].strip() and (base['themes'] or base['sentiment'] in ('positive','negative','mixed')):
        for aspect in base['themes'] or ['整体使用感受']:
            points.append({'product_scope':'target','aspect':aspect,'topic':aspect+'相关反馈（规则预览）',
                'kind':'problem' if base['sentiment'] in ('negative','mixed') else 'experience',
                'sentiment':base['sentiment'],'basis':'agreement' if inherited else 'direct','scenario':None,
                'pain_point':None,'impact':None,'expected_outcome':None,'keywords':[aspect],
                'quote':item['text'][:800],'context_quote':item['parent_text'][:800] if inherited else None})
    base.update(points=points,analysis_version=2,**labels_from_points(points,base['relevance']))
    return base

def quarantine(value,evidence_id,reason,stage='audit'):
    saved=copy.deepcopy(value or {'evidence_id':evidence_id,'analysis_version':2,'suspected_ad':None,'quote':''})
    saved['candidate_points']=copy.deepcopy(saved.get('points',[]));saved['points']=[]
    saved.update(labels_from_points([],'uncertain'))
    saved['reason']='模型输出未通过，已暂不纳入报告；请人工核查。'+reason
    saved['opinion_audit']={'state':'needs_review','failed_stage':stage,'content_class':'uncertain','reason':reason,
        'candidate_count':len(saved['candidate_points']),'retained_count':0,'removed_count':len(saved['candidate_points']),
        'checks':[],'discarded_fields':[]}
    return saved

def recover_extraction(error,batch,cfg):
    raw=error.parsed.get('items',[]) if isinstance(error.parsed,dict) else []
    raw=raw if isinstance(raw,list) else []
    output=[]
    for item in batch:
        eid=item['evidence_id'];matches=[x for x in raw if isinstance(x,dict) and x.get('evidence_id')==eid]
        try:
            if len(matches)!=1:raise ModelFailure(f'{eid} 初提取应返回1项，实际{len(matches)}项')
            row=CommentUnderstanding.model_validate(matches[0])
            output.extend(validate_extraction(AspectExtraction(items=[row]),[item],cfg))
        except (ValidationError,ModelFailure) as exc:
            reason='；'.join(validation_issues(exc)) if isinstance(exc,ValidationError) else str(exc)
            output.append(quarantine(None,eid,reason+'；'+str(error),'extraction'))
    return output

def recover_audit(error,batch,values):
    raw=error.parsed.get('items',[]) if isinstance(error.parsed,dict) else []
    valid=[];issues={}
    for x in raw if isinstance(raw,list) else []:
        try:valid.append(CommentOpinionAudit.model_validate(x))
        except ValidationError as exc:
            if isinstance(x,dict) and isinstance(x.get('evidence_id'),str):issues[x['evidence_id']]='；'.join(validation_issues(exc))
    output=apply_audit(OpinionAuditBatch(items=valid),batch,values,allow_partial=True)
    for value in output:
        if value.get('opinion_audit',{}).get('state')=='needs_review':
            reason=issues.get(value['evidence_id'],str(error))
            value['opinion_audit']['reason']=reason;value['reason']='模型复核未通过，已隔离为待核查；'+reason
    return output

def apply_audit(result,batch,values,allow_partial=False):
    if allow_partial:
        output=[]
        for item in batch:
            value=next(v for v in values if v['evidence_id']==item['evidence_id'])
            matches=[r for r in result.items if r.evidence_id==item['evidence_id']]
            try:
                if len(matches)!=1:raise ModelFailure(f'该评论复核返回 {len(matches)} 项，应为 1 项')
                output.extend(apply_audit(OpinionAuditBatch(items=matches),[item],[value]))
            except ModelFailure as exc:
                output.append(quarantine(value,item['evidence_id'],str(exc)))
        return output
    expected={x['evidence_id']:x for x in batch};lookup={v['evidence_id']:v for v in values}
    ids=[x.evidence_id for x in result.items]
    if len(ids)!=len(set(ids)) or set(ids)!=set(expected):raise ModelFailure('观点复核缺失、重复或引用未知评论')
    output=[]
    for checked in result.items:
        item=expected[checked.evidence_id];value=copy.deepcopy(lookup[checked.evidence_id]);candidates=value['points'];kept=[];discarded=[]
        indices=[c.point_index for c in checked.checks]
        if checked.content_class=='feedback' and (len(indices)!=len(set(indices)) or set(indices)!=set(range(len(candidates)))):
            raise ModelFailure(f'观点复核序号不匹配（{checked.evidence_id}）：期望 {list(range(len(candidates)))}，实际 {indices}；不得漏项、重复或新增观点')
        if checked.content_class=='feedback':
            for c in checked.checks:
                p=copy.deepcopy(candidates[c.point_index])
                if c.verdict=='unsupported':continue
                if c.verdict=='uncertain':p['product_scope']='uncertain'
                p.update(basis=c.basis,kind=c.kind,sentiment=c.sentiment,context_quote=c.context_quote if c.basis=='agreement' else None)
                sources=[item['text']]+([item.get('parent_text') or ''] if c.basis=='agreement' else [])
                for field,quote in [('scenario',c.scenario_quote),('impact',c.impact_quote),('expected_outcome',c.expected_outcome_quote)]:
                    if quote and (not quote.strip() or not any(quote in source for source in sources)):
                        discarded.append({'point_index':c.point_index,'field':field,'value':quote,'reason':'无法匹配允许来源的连续原文，已置为未知'})
                        quote=None
                    p[field]=quote
                if p['kind']!='problem':p['pain_point']=None
                validate_points([p],item,strict_literals=True);kept.append(p)
        value['candidate_points']=candidates;value['points']=kept
        relevance='uncertain' if checked.content_class=='uncertain' else 'irrelevant' if checked.content_class=='non_feedback' else value['relevance']
        value.update(labels_from_points(kept,relevance))
        value['reason']=(checked.reason+(f'；程序将 {len(discarded)} 个未能引用的可空字段置为未知' if discarded else '')+'；初提取：'+value['reason'])[:800]
        value['opinion_audit']={'state':'checked','content_class':checked.content_class,'reason':checked.reason,
            'candidate_count':len(candidates),'retained_count':len(kept),'removed_count':len(candidates)-len(kept),
            'checks':[c.model_dump() for c in checked.checks],'discarded_fields':discarded}
        output.append(value)
    return output

async def extract_all(task_id,cfg,checkpoint,progress,target_ids=None):
    items=db.comments(task_id);blocked={x['evidence_id'] for x in items if x['effective'].get('excluded')}
    client=ModelClient();todo=[];cached=0;ai=cfg['analysis_mode']=='ai';audit_version=AUDIT_VERSION if ai else 'rules'
    cfg_data={k:cfg.get(k) for k in ('product','aliases','description','focus','analysis_mode')}
    for item in items:
        if target_ids is not None and item['evidence_id'] not in target_ids:continue
        if item['evidence_id'] in blocked:continue
        if item.get('parent_evidence_id') in blocked:item['parent_text']=None
        fingerprint=hashlib.sha256(db.dumps({'v':EXTRACT_CACHE_VERSION,'system':SYSTEM,'prompt':UNDERSTAND,'config':cfg_data,'input':payload_comment(item),
            'parent_id':item.get('parent_evidence_id'),'provider':{k:client.settings[k] for k in ('base_url','model')},'provider_options':provider_options(client.settings)}).encode()).hexdigest()
        meta=(item['analysis'] or {}).get('_meta',{})
        same=meta.get('fingerprint')==fingerprint and meta.get('extraction_valid',True)
        if same and item['analysis']['_meta'].get('audit_version')==audit_version:cached+=1
        else:todo.append((item,fingerprint,same))
    total=cached+len(todo);progress(cached,total)
    if cached:db.event(task_id,'analysis_cache',f'复用 {cached} 条提取与复核指纹一致的结果；仍应用最新人工修正。')
    reused=sum(same for _,_,same in todo)
    if reused:db.event(task_id,'analysis_cache',f'复用 {reused} 条已校验初提取，只补充本版观点复核，不重复提取。')
    size=min(client.settings['batch_size'],10)
    def persist(values,hashes,audited):
        with db.connect() as c:
            for value in values:
                value['_meta']={'engine':VERSION,'fingerprint':hashes[value['evidence_id']],
                    'extraction_valid':value.get('opinion_audit',{}).get('failed_stage')!='extraction',
                    'audit_version':audit_version if audited and value.get('opinion_audit',{}).get('state')!='needs_review' else None}
                c.execute('UPDATE comments SET analysis=? WHERE task_id=? AND evidence_id=?',(db.dumps(value),task_id,value['evidence_id']))
    for start in range(0,len(todo),size):
        await checkpoint();pairs=todo[start:start+size];batch=[x for x,_,_ in pairs];values=[]
        hashes={x['evidence_id']:h for x,h,_ in pairs}
        fresh=[x for x,_,same in pairs if not same]
        for x,_,same in pairs:
            if same:
                value=copy.deepcopy(x['analysis']);value['points']=copy.deepcopy(value.get('candidate_points',value.get('points',[])));values.append(value)
        nonempty=[x for x in fresh if x['text'].strip()]
        if ai and nonempty:
            prompt=UNDERSTAND;payload={**cfg_data,'comments':[payload_comment(x) for x in nonempty]}
            for attempt in range(2):
                try:result=await client.call(prompt,payload,AspectExtraction)
                except ModelOutputFailure as exc:
                    db.event(task_id,'model_output',str(exc))
                    values.extend(recover_extraction(exc,nonempty,cfg));break
                try:values.extend(validate_extraction(result,nonempty,cfg));break
                except ModelFailure as exc:
                    if attempt:
                        db.event(task_id,'model_output',str(exc))
                        values.extend(recover_extraction(ModelOutputFailure('AspectExtraction',[str(exc)],result.model_dump()),nonempty,cfg));break
                    db.event(task_id,'validation_retry',str(exc)+'；正在有界重试')
                    prompt=UNDERSTAND+'\n上次结果被拒绝：'+str(exc)+'。请完整重做本批，引用必须逐字复制原文。'
            values += [rule_understanding(x,cfg) for x in fresh if not x['text'].strip()]
        else:
            values += [rule_understanding(x,cfg) for x in fresh];await asyncio.sleep(0)
        if ai:
            lookup={v['evidence_id']:v for v in values}
            # Persist the independently valid first stage before a second request can fail.
            preliminary=[]
            for value in values:
                if value.get('opinion_audit',{}).get('failed_stage')=='extraction':
                    preliminary.append(copy.deepcopy(value));continue
                saved=copy.deepcopy(value);saved['candidate_points']=saved['points'];saved['has_feedback']=False
                saved['opinion_audit']={'state':'pending','reason':'初提取已保存，尚待观点复核'};preliminary.append(saved)
            persist(preliminary,hashes,False)
            failed=[v for v in values if v.get('opinion_audit',{}).get('failed_stage')=='extraction']
            audit_batch=[x for x in batch if x['evidence_id'] not in {v['evidence_id'] for v in failed}]
            audit_values=[v for v in values if v['evidence_id'] in {x['evidence_id'] for x in audit_batch}]
            prompt=AUDIT+'\n'+AUDIT_PROTOCOL
            payload={'product':cfg['product'],'description':cfg.get('description',''),'comments':[
                {**payload_comment(x),'expected_point_indices':list(range(len(lookup[x['evidence_id']]['points']))),
                 'candidate_points':[{'point_index':i,**p} for i,p in enumerate(lookup[x['evidence_id']]['points'])]} for x in audit_batch]}
            for attempt in range(2):
                if not audit_batch:values=failed;break
                try:result=await client.call(prompt,payload,OpinionAuditBatch)
                except ModelOutputFailure as exc:
                    db.event(task_id,'model_output',str(exc))
                    values=failed+recover_audit(exc,audit_batch,audit_values);break
                try:values=failed+apply_audit(result,audit_batch,audit_values);break
                except ModelFailure as exc:
                    if attempt:
                        values=failed+apply_audit(result,audit_batch,audit_values,allow_partial=True)
                        count=sum(v.get('opinion_audit',{}).get('state')=='needs_review' for v in values)
                        db.event(task_id,'audit_partial',f'复核有界重试后仍有 {count} 条未通过，已隔离为待核查；其余结果保留，不阻断后续批次。')
                        break
                    db.event(task_id,'validation_retry',str(exc)+'；观点复核正在有界重试')
                    prompt=AUDIT+'\n'+AUDIT_PROTOCOL+'\n上次复核被拒绝：'+str(exc)+'。请复制 expected_point_indices 完整复核本批，空候选列表返回空 checks；证据片段须逐字存在。'
            persist(values,hashes,True)  # Preserve successful batch rows before a recovery request can fail.
            for i,value in enumerate(values):
                audit=value.get('opinion_audit',{})
                if audit.get('state')!='needs_review' or audit.get('failed_stage')!='audit':continue
                await checkpoint();eid=value['evidence_id']
                single={**payload,'comments':[x for x in payload['comments'] if x['evidence_id']==eid]}
                one_batch=[x for x in audit_batch if x['evidence_id']==eid]
                one_values=[x for x in audit_values if x['evidence_id']==eid]
                db.event(task_id,'validation_recovery',f'{eid}：观点复核未通过，仅重试当前评论；合格记录保留。')
                try:
                    retry=await client.call(AUDIT+'\n'+AUDIT_PROTOCOL+'\n仅修复当前评论。上次失败字段：'+audit.get('reason',''),single,OpinionAuditBatch)
                except ModelOutputFailure as exc:
                    values[i]=recover_audit(exc,one_batch,one_values)[0]
                else:
                    values[i]=apply_audit(retry,one_batch,one_values,allow_partial=True)[0]
                persist([values[i]],hashes,True)
                if values[i].get('opinion_audit',{}).get('state')=='checked':
                    db.event(task_id,'validation_recovered',f'{eid}：观点复核已恢复，通过结构及原文校验。')
        persist(values,hashes,True)
        progress(cached+start+len(batch),total)

def effective_points(item):
    e=item['effective'];overrides=item['overrides'];points=copy.deepcopy(e.get('points') or [])
    if 'points' in overrides:return points
    for p in points:
        if overrides.get('relevance') in ('target','other_product','uncertain'):p['product_scope']=overrides['relevance']
        if overrides.get('sentiment'):p['sentiment']=overrides['sentiment']
    return points

def build_units(items):
    units=[]
    for item in items:
        if not db.included(item):continue
        for idx,p in enumerate(effective_points(item)):
            if p['product_scope']!='target':continue
            if p['basis']=='agreement' and (not p.get('context_quote') or p['context_quote'] not in (item.get('parent_text') or '')):continue
            labels=item['overrides'].get('themes') if 'points' not in item['overrides'] else None
            for aspect in unique(labels) if labels is not None else [p['aspect']]:
                units.append({**p,'aspect':aspect,'unit_id':f'{item["evidence_id"]}#p{idx+1}',
                    'evidence_id':item['evidence_id'],'platform':item['platform'],'note_id':item.get('note_id'),
                    'manual_aspect':'points' in item['overrides'] or 'themes' in item['overrides']})
    return units

def examples(units,limit=4):
    result=[];seen=set()
    for sentiment in ('negative','positive','mixed','neutral','unknown'):
        candidates=sorted((u for u in units if u['sentiment']==sentiment),key=lambda u:u['basis']!='direct')
        for u in candidates:
            if u['evidence_id'] in seen:continue
            result.append(u);seen.add(u['evidence_id']);break
    for u in sorted(units,key=lambda u:u['basis']!='direct'):
        if len(result)>=limit:break
        if u['evidence_id'] not in seen:result.append(u);seen.add(u['evidence_id'])
    return result[:limit]

async def group_units(units,client,checkpoint,ai):
    buckets=defaultdict(list)
    for u in units:buckets[(normal(u['aspect']),normal(u['topic']))].append(u)
    current=[{'id':f'candidate-{i}','aspect':a,'name':name,'units':rows} for i,((a,name),rows) in enumerate(sorted(buckets.items()),1)]
    if not ai:return current
    for level in range(3):
        if not current:break
        merged=[]
        for start in range(0,len(current),24):
            await checkpoint();chunk=current[start:start+24];lookup={x['id']:x for x in chunk}
            payload={'candidates':[{'id':x['id'],'aspect':x['aspect'],'name':x['name'],
                'examples':[{k:u.get(k) for k in ('topic','sentiment','kind','pain_point','quote','context_quote')} for u in examples(x['units'],3)]} for x in chunk]}
            def valid_group(result):
                members=[m for g in result.groups for m in g.member_ids]
                if len(members)!=len(set(members)) or set(members)!=set(lookup):raise ModelFailure('观点归并返回缺失、重复或未知候选 ID')
            result=await semantic_call(client,MERGE,payload,OpinionGrouping,valid_group,checkpoint)
            for g in result.groups:
                partitions=defaultdict(list)
                for member in g.member_ids:
                    for u in lookup[member]['units']:partitions[u['aspect'] if u.get('manual_aspect') else g.aspect].append(u)
                for aspect,rows in partitions.items():
                    merged.append({'id':f'level-{level}-{len(merged)+1}','aspect':aspect,'name':g.name,'units':rows})
        # Identically named outputs across batches share one group; keep every opinion.
        combined={}
        for x in merged:
            key=(normal(x['aspect']),normal(x['name']))
            if key in combined:combined[key]['units'].extend(x['units'])
            else:combined[key]=x
        after=sorted(combined.values(),key=lambda x:(x['aspect'],x['name']))
        if len(current)<=24:return after
        current=after
    return current

def summarize_units(units,total):
    ids=unique(u['evidence_id'] for u in units)
    pos={u['evidence_id'] for u in units if u['sentiment'] in ('positive','mixed')}
    neg={u['evidence_id'] for u in units if u['sentiment'] in ('negative','mixed')}
    direct={u['evidence_id'] for u in units if u['basis']=='direct'}
    agreement={u['evidence_id'] for u in units if u['basis']=='agreement'}
    return {'count':len(ids),'share':round(len(ids)/total*100,1) if total else 0,'evidence_ids':ids,
        'positive_count':len(pos),'negative_count':len(neg),'negative_share':round(len(neg)/len(ids)*100,1) if ids else 0,
        'direct_count':len(direct),'agreement_count':len(agreement),'point_count':len({u['unit_id'] for u in units})}

def fact_evidence(by_id,ids):
    return [payload_comment(by_id[eid]) for eid in ids]

def compact_stats(rows):
    fields=('name','aspect','count','share','positive_count','negative_count','direct_count','agreement_count')
    return [{k:r[k] for k in fields if k in r} for r in rows]

def reuse_groups(units, previous):
    """Only reuse a prior semantic label when its complete membership is unchanged."""
    old_units={u['unit_id']:u for u in previous['opinion_points']}
    fields=tuple(OpinionPoint.model_fields)
    current={u['unit_id']:u for u in units}
    unchanged={uid for uid,u in current.items() if uid in old_units and
               all(u.get(k)==old_units[uid].get(k) for k in fields)}
    old_by_comment=defaultdict(list);memberships=defaultdict(list)
    for uid,u in old_units.items():old_by_comment[u['evidence_id']].append(uid)
    for t in previous['themes']:
        for eid in t['evidence_ids']:memberships[eid].append(t['id'])
    groups=[];assigned=set()
    for t in previous['themes']:
        ids=t.get('unit_ids')
        if ids is None:
            # Legacy snapshots have no member opinion IDs. Never guess a many-to-many mapping.
            if not all(len(old_by_comment[eid])==1 and len(memberships[eid])==1 for eid in t['evidence_ids']):continue
            ids=[old_by_comment[eid][0] for eid in t['evidence_ids']]
        if ids and set(ids)<=unchanged and not assigned.intersection(ids):
            groups.append({'aspect':t['aspect'],'name':t['name'],'units':[current[uid] for uid in ids]})
            assigned.update(ids)
    fallback=defaultdict(list)
    for u in units:
        if u['unit_id'] not in assigned:fallback[(u['aspect'],u['topic'])].append(u)
    groups.extend({'aspect':a,'name':name,'units':rows} for (a,name),rows in fallback.items())
    # Reuse prose only if every opinion of each cited comment and its source context is unchanged.
    stable=set()
    for eid in previous['evidence']:
        old_ids=set(old_by_comment[eid]);new_ids={u['unit_id'] for u in units if u['evidence_id']==eid}
        if old_ids and old_ids==new_ids and old_ids<=unchanged:stable.add(eid)
    return groups,stable

async def make_report(task_id,cfg,checkpoint,previous_report=None,provider_issue=None):
    items=db.comments(task_id);blocked={x['evidence_id'] for x in items if x['effective'].get('excluded')}
    for x in items:
        if x.get('parent_evidence_id') in blocked:x['parent_text']=None
    usable=[x for x in items if db.included(x)];by_id={x['evidence_id']:x for x in usable}
    units=build_units(items);total=len(usable);ai=cfg['analysis_mode']=='ai'
    offline=previous_report is not None
    client=None if offline else ModelClient();protocol_warnings=[]
    def protocol_warning(stage,exc):
        message=stage+'未通过结构/证据契约，已保守处理：'+str(exc)
        protocol_warnings.append(message);db.event(task_id,'model_output',message)
    if offline:
        groups,stable=reuse_groups(units,previous_report)
        stable={eid for eid in stable if eid in by_id and all(
            (previous_report['evidence'][eid].get(k) or '')==(by_id[eid].get(k) or '')
            for k in ('text','parent_text','note_title','note_body'))}
    else:
        try:groups=await group_units(units,client,checkpoint,ai)
        except ModelOutputFailure as exc:
            protocol_warning('语义归并',exc)
            groups=await group_units(units,client,checkpoint,False)
    themes=[];topic_units={};aspect_units=defaultdict(list);keyword_units=defaultdict(list)
    for group in sorted(groups,key=lambda g:-len({u['evidence_id'] for u in g['units']})):
        rows=group['units'];tid=f'topic-{len(themes)+1}';topic_units[tid]=rows
        summary=summarize_units(rows,total);representatives=examples(rows)
        themes.append({'id':tid,'name':group['name'],'aspect':group['aspect'],**summary,
            'unit_ids':[u['unit_id'] for u in rows],
            'representative_ids':unique(u['evidence_id'] for u in representatives),
            'representative_quotes':[{k:u.get(k) for k in ('unit_id','evidence_id','quote','context_quote','basis','sentiment')} for u in representatives],
            'scenarios':unique(u.get('scenario') for u in rows)[:12] or ['未知'],
            'feedback':unique(u['topic'] for u in rows)[:6],
            'pain_points':unique(u.get('pain_point') for u in rows if u['kind']=='problem')[:12],
            'impacts':unique(u.get('impact') for u in rows)[:10],'expectations':unique(u.get('expected_outcome') for u in rows)[:10],
            'impact':'；'.join(unique(u.get('impact') for u in rows)[:5]) or '未知；评论未明确说明后果',
            'recommendation':'暂无通过证据检查的具体建议','hypothesis':'暂无生成的验证方案',
            'recommendation_evidence_ids':[],'validation_evidence_ids':[]})
        for u in rows:aspect_units[group['aspect']].append(u)
    for u in units:
        for word in unique(u.get('keywords',[])):keyword_units[word].append(u)
    aspects=[{'name':a,**summarize_units(rows,total)} for a,rows in aspect_units.items()]
    aspects.sort(key=lambda x:(-x['count'],x['name']))
    keywords=[{'word':word,**summarize_units(rows,total)} for word,rows in keyword_units.items()]
    keywords.sort(key=lambda x:(-x['count'],x['word']))
    pain_points=[];needs=[];strengths=[]
    for theme in themes:
        rows=topic_units[theme['id']]
        for output,selected,description in (
            (pain_points,[u for u in rows if u['kind']=='problem' and u.get('pain_point') and u['sentiment'] in ('negative','mixed')],'pain_point'),
            (needs,[u for u in rows if u['kind']=='request' or u.get('expected_outcome')],'expected_outcome'),
            (strengths,[u for u in rows if u['sentiment'] in ('positive','mixed')],'topic')):
            if selected:output.append({'topic_id':theme['id'],'name':theme['name'],'aspect':theme['aspect'],
                **summarize_units(selected,total),'descriptions':unique(u.get(description) for u in selected)[:5],
                'impacts':unique(u.get('impact') for u in selected),'expectations':unique(u.get('expected_outcome') for u in selected)})
    for output in (pain_points,needs,strengths):output.sort(key=lambda x:-x['count'])
    advice_ids=unique([x['topic_id'] for x in pain_points[:8]]+[x['topic_id'] for x in needs[:4]]+[x['topic_id'] for x in strengths[:4]])[:12]
    lookup={t['id']:t for t in themes};advice={};omitted=[];narrative={'findings':[],'positives':[],'recommendations':[],'questions':[]}
    def safe_claim(claim,allowed,section):
        if claim is None:return None
        try:validate_narrative(Narrative(findings=[claim],positives=[],recommendations=[],questions=[]),set(allowed));return claim
        except ModelFailure as exc:
            omitted.append({'section':section,'text':claim.text,'evidence_ids':claim.evidence_ids,'reason':str(exc)})
            return None
    if offline:
        def retain(claim):
            if not claim or not claim.get('evidence_ids') or not set(claim['evidence_ids'])<=stable:return False
            try:validate_narrative(Narrative(findings=[claim],positives=[],recommendations=[],questions=[]),stable)
            except ModelFailure:return False
            return True
        for section in narrative:
            narrative[section]=[copy.deepcopy(c) for c in previous_report.get(section,[]) if retain(c)]
        for t in themes:
            old=next((x for x in previous_report['themes'] if x['aspect']==t['aspect'] and x['name']==t['name']
                      and set(x['evidence_ids'])==set(t['evidence_ids'])),None)
            if not old:continue
            for field,ref,section in (('recommendation','recommendation_evidence_ids','recommendations'),('hypothesis','validation_evidence_ids','questions')):
                claim={'text':old[field],'evidence_ids':old.get(ref,[])}
                if retain(claim) and claim in narrative[section]:t[field]=claim['text'];t[ref]=claim['evidence_ids']
        support_check={'method':'cached_claim_dependency_filter','new_model_check':False,
            'previous_generated_at':previous_report['generated_at'],'stable_evidence_count':len(stable),
            'removed_claim_count':sum(len(previous_report.get(s,[]))-len(narrative[s]) for s in narrative)}
    elif ai and units:
        for start in range(0,len(advice_ids),4):
            await checkpoint();chunk=[lookup[tid] for tid in advice_ids[start:start+4]]
            advice_payload={'product':cfg['product'],'focus':cfg.get('focus',''),
                'topics':[{'topic_id':t['id'],'aspect':t['aspect'],'topic':t['name'],
                           'evidence':fact_evidence(by_id,t['representative_ids'])} for t in chunk]}
            def valid_advice(result):
                ids=[x.topic_id for x in result.topics]
                if len(ids)!=len(set(ids)) or set(ids)!={t['id'] for t in chunk}:raise ModelFailure('话题建议缺失、重复或引用未知话题')
                for entry in result.topics:
                    for claim in (entry.recommendation,entry.validation):
                        if claim:validate_narrative(Narrative(findings=[],positives=[],recommendations=[claim],questions=[]),set(lookup[entry.topic_id]['representative_ids']))
            def sanitize_advice(result):
                cleaned=[]
                for t in chunk:
                    matches=[x for x in result.topics if x.topic_id==t['id']]
                    if len(matches)!=1:
                        omitted.append({'section':t['id'],'reason':'话题建议缺失或重复，未展示','text':'','evidence_ids':[]})
                        cleaned.append({'topic_id':t['id'],'recommendation':None,'validation':None});continue
                    entry=matches[0];allowed=t['representative_ids']
                    cleaned.append({'topic_id':t['id'],'recommendation':safe_claim(entry.recommendation,allowed,t['id']+':recommendation'),
                                    'validation':safe_claim(entry.validation,allowed,t['id']+':validation')})
                return TopicAdviceBatch(topics=cleaned)
            try:result=await semantic_call(client,ADVISE,advice_payload,TopicAdviceBatch,valid_advice,checkpoint,sanitize_advice)
            except ModelOutputFailure as exc:
                protocol_warning('本批话题建议',exc);continue
            for entry in result.topics:
                for key,section in (('recommendation','recommendations'),('validation','questions')):
                    claim=getattr(entry,key)
                    if claim:
                        validate_narrative(Narrative(findings=[],positives=[],recommendations=[claim],questions=[]),set(lookup[entry.topic_id]['representative_ids']))
                        advice[(entry.topic_id,key)]=claim.model_dump();narrative[section].append(claim.model_dump())
        core_ids=unique([t['id'] for t in themes[:6]]+[p['topic_id'] for p in pain_points[:3]]+[s['topic_id'] for s in strengths[:3]])[:10]
        evidence_ids=unique(eid for tid in core_ids for eid in lookup[tid]['representative_ids'])[:20]
        core_payload={'product':cfg['product'],'focus':cfg.get('focus',''),
            'statistics':{'included_comments':total,'opinion_points':len({u['unit_id'] for u in units}),'aspects':compact_stats(aspects[:12]),
                          'pain_points':compact_stats(pain_points[:8]),'strengths':compact_stats(strengths[:6]),'needs':compact_stats(needs[:6])},
            'evidence':fact_evidence(by_id,evidence_ids)}
        def valid_core(result):
            validate_narrative(Narrative(findings=result.findings,positives=result.positives,recommendations=[],questions=[]),set(evidence_ids))
        def sanitize_core(result):
            return Narrative(findings=[c for c in (safe_claim(x,evidence_ids,'findings') for x in result.findings) if c],
                positives=[c for c in (safe_claim(x,evidence_ids,'positives') for x in result.positives) if c],recommendations=[],questions=[])
        try:result=await semantic_call(client,SYNTHESIZE,core_payload,Narrative,valid_core,checkpoint,sanitize_core)
        except ModelOutputFailure as exc:
            protocol_warning('整体发现生成',exc);result=Narrative()
        narrative['findings']=result.model_dump()['findings'];narrative['positives']=result.model_dump()['positives']
        # The checker can see explicitly labelled parent context, never an excluded parent.
        check_by_id={eid:{**x,'text':'当前评论：'+x['text']+('\n父评论上下文（仅用于附和释义）：'+x['parent_text'] if x.get('parent_text') else '')} for eid,x in by_id.items()}
        try:narrative,support_check=await verify_support(client,narrative,check_by_id,checkpoint,prompt=SUPPORT_V2)
        except ModelOutputFailure as exc:
            protocol_warning('生成结论证据复核',exc)
            removed=sum(len(v) for v in narrative.values())
            narrative={k:[] for k in narrative}
            support_check={'method':'output_invalid_claims_omitted','checked':0,'omitted':removed,'reason':str(exc)}
        retained={(c['text'],tuple(c['evidence_ids'])) for section in ('recommendations','questions') for c in narrative[section]}
        for (tid,key),claim in advice.items():
            if (claim['text'],tuple(claim['evidence_ids'])) not in retained:continue
            field='recommendation' if key=='recommendation' else 'hypothesis'
            lookup[tid][field]=claim['text'];lookup[tid]['recommendation_evidence_ids' if key=='recommendation' else 'validation_evidence_ids']=claim['evidence_ids']
    else:
        support_check=None
        narrative['findings']=[{'text':f'本地规则识别到「{t["name"]}」，尚未进行逐维度语义核查。','evidence_ids':t['representative_ids']} for t in themes[:5]]
        narrative['positives']=[{'text':'原文表达：'+u['quote'],'evidence_ids':[u['evidence_id']]} for u in examples([u for u in units if u['sentiment']=='positive'],5)]
    text_groups=defaultdict(list)
    for x in usable:
        if normal(x['text']):text_groups[normal(x['text'])].append(x['evidence_id'])
    repeated=[{'text':text,'evidence_ids':ids,'count':len(ids)} for text,ids in text_groups.items() if len(ids)>1]
    evidence={eid:{k:x.get(k) for k in ('evidence_id','platform','comment_id','text','note_id','note_title','note_body','parent_text','parent_evidence_id','source_url','source_keywords','published_at','like_count')} for eid,x in by_id.items()}
    needs_review=[x['evidence_id'] for x in items if (x['analysis'] or {}).get('opinion_audit',{}).get('state')=='needs_review'
                  and not x['effective'].get('excluded') and 'points' not in x['overrides']]
    warnings=(['本报告仅离线重算当前标签统计；文案保守复用上一轮结果，涉及已修改/排除证据的结论已删除，未新调用模型。']+
        ([provider_issue] if provider_issue else [])) if offline else [f'{len(omitted)} 条生成文本未通过数字/证据契约检查，已省略；统计与原文保留。'] if omitted else []
    if needs_review:warnings.append(f'{len(needs_review)} 条评论的模型观点复核未通过，已隔离为待核查，不支撑报告；可人工核查或重新分析重试这些评论。')
    warnings.extend(protocol_warnings)
    return {'analysis_version':2,'engine':VERSION,'product':cfg['product'],'generated_at':db.now(),'source':cfg['source'],
        'source_name':db.task(task_id)['source_name'],'analysis_mode':cfg['analysis_mode'],
        'model':previous_report['model'] if offline else client.settings['model'] if ai else '本地规则预览（未调用 AI）',
        'config':cfg,'stats':db.stats(task_id,items),'themes':themes,'evidence':evidence,**narrative,
        'aspects':aspects,'keywords':keywords[:40],'pain_points':pain_points,'needs':needs,'strengths':strengths,
        'opinion_points':units,'opinion_count':len({u['unit_id'] for u in units}),'support_check':support_check,
        'generation_warnings':warnings,
        'generation_mode':'cached_narrative_recount' if offline else 'model_generated' if ai else 'rules',
        'provider_issue':provider_issue if offline else None,
        'previous_generated_at':previous_report['generated_at'] if offline else None,
        'omitted_generated_claims':omitted,
        'advice_scope':{'topic_ids':advice_ids,'topic_limit':12},
        'quality':{'repeated_text_group_count':len(repeated),'repeated_text_comment_count':sum(x['count'] for x in repeated),'repeated_text_examples':repeated[:20],
                   'audit_needs_review_count':len(needs_review),'audit_needs_review_ids':needs_review,
                   'audit_removed_point_count':sum((x['analysis'] or {}).get('opinion_audit',{}).get('removed_count',0) for x in items),
                   'audit_method':'model_self_check_not_human_gold'},
        'sentiments':{s:sum(x['effective'].get('sentiment')==s for x in usable) for s in ('positive','negative','mixed','neutral','unknown')},
        'unthemed':len(set(by_id)-{u['evidence_id'] for u in units}),
        'limitations':['结论仅来自本次样本，不代表全部用户；社交平台样本不是随机抽样。',
            '身份去重按平台＋评论 ID；不同 ID 的同文和相似观点均保留，只提示同文并归并话题，评论数不等于独立用户数。',
            '关注点、话题、关键词与痛点数量按各自覆盖的唯一评论计算；同一评论可包含多个观点，维度占比合计可超过 100%。',
            '正面/负面提及包含混合情绪中的相应表达，可同时出现；附和数量另列，不等于独立、详细的故障描述。',
            '关键词是模型归纳短语的评论提及数，不是原文字面词频；疑似广告、其他产品观点、待确认和人工排除项不支撑报告。',
            '影响与期待只展示用户明确表达的信息；根因、商业损失、开发成本与业务优先级未知。频次仅是研究核查线索。',
            '模型逐维度判断、话题归并与证据自检仍需人工抽样核查；ID/原文匹配不等于语义正确，未据此声称准确率。',
            '建议仅覆盖最多12个有问题/需求/正面证据的话题；其余话题保留统计和原文。分批归并可能仍存在近似话题。',
            '各平台样本范围和排序不同，不直接比较平台口碑；超长评论和上下文会截断分析输入，原文仍完整保留。',
            '规则预览只做基础归类，无法完成可靠的观点拆分、真实痛点与具体建议；正式洞察请使用 AI 模式。' if not ai else '当前为 AI 观点分析，结构化结果是模型判断，原始评论本身也不是已经核实的产品事实。']}

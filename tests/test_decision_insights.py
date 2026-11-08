"""Decision quality contracts; protocol substitutes do not imply model accuracy."""
import copy
import csv
import io
import json
from collections import defaultdict
import pytest
from insight import db
from insight.analysis import ModelClient,ModelOutputFailure,ModelFailure
from insight.schemas import SupportCheck
from insight.decision_schemas import ResearchBatch,TaxonomyPlan,TaxonomyMapping,DecisionNarrative
from insight.decision_state import source_hash,thread_keys,eligible,satisfaction,problem
from insight.decision_analysis import valid_annotation,priority,summarize
from test_pipeline import create,upload,wait
from test_aspect_analysis import point

def sample():
    return {'notes':[{'platform':'xhs','note_id':'n','title':'飞书使用反馈'},{'platform':'dy','note_id':'n','title':'飞书教程'}], 'comments':[
        {'platform':'xhs','comment_id':'1','note_id':'n','parent_comment_id':None,'user_id':'u1','text':'飞书入口找不到，耽误了半小时。'},
        {'platform':'xhs','comment_id':'2','note_id':'n','parent_comment_id':'1','user_id':'u2','text':'我也是'},
        {'platform':'xhs','comment_id':'3','note_id':'n','parent_comment_id':'1','user_id':'u2','text':'我也是'},
        {'platform':'xhs','comment_id':'4','note_id':'n','parent_comment_id':None,'user_id':'u1','text':'飞书功能入口难找，会议开不了。'},
        {'platform':'dy','comment_id':'1','note_id':'n','parent_comment_id':None,'user_id':'u1','text':'飞书新手上手很容易，文档操作顺手。'},
        {'platform':'xhs','comment_id':'5','note_id':'n','parent_comment_id':None,'text':'希望飞书能够自动发送日报。'},
        {'platform':'dy','comment_id':'6','note_id':'n','parent_comment_id':None,'text':'这个教程根本没讲清楚飞书怎么用。'},
        {'platform':'xhs','comment_id':'7','note_id':'n','text':'飞书会不会把文件弄丢？'},
        {'platform':'xhs','comment_id':'8','note_id':'n','parent_comment_id':None,'text':'飞书昨天丢失了文件，无法继续工作。'},
        {'platform':'xhs','comment_id':'9','note_id':'n','parent_comment_id':None,'text':'代理商收费离谱，和飞书无关。'},
        {'platform':'xhs','comment_id':'1','note_id':'n','parent_comment_id':None,'user_id':'u1','text':'飞书入口找不到，耽误了半小时。'},
    ]}

def seed_analysis(tid):
    for item in db.comments(tid):
        eid=item['evidence_id'];text=item['text'];cid=eid.split(':')[-1]
        if eid=='xhs:1':points=[point('学习成本','功能入口找不到','入口找不到',impact='耽误了半小时')]
        elif cid in ('2','3'):points=[point('新手友好度','不知道在哪里开启','我也是',basis='agreement',context_quote='入口找不到')]
        elif cid=='4':points=[point('产品易用性','功能开启困难','功能入口难找',impact='会议开不了')]
        elif eid=='dy:1':points=[point('上手成本','上手很容易','新手上手很容易','positive'),point('文档编辑','编辑操作顺手','文档操作顺手','positive')]
        elif cid=='5':points=[point('日报','自动发送日报','希望飞书能够自动发送日报','positive',kind='request',expected_outcome='自动发送日报')]
        elif cid=='6':points=[point('教程内容','教程讲解不清','这个教程根本没讲清楚',kind='problem')]
        elif cid=='7':points=[point('文件安全','担忧文件丢失','会不会把文件弄丢','negative',kind='question',pain_point=None)]
        elif cid=='8':points=[point('数据安全','文件丢失','丢失了文件',impact='无法继续工作')]
        else:points=[point('价格','代理商收费贵','代理商收费离谱')]
        a={'analysis_version':2,'relevance':'target','has_feedback':True,'suspected_ad':False,'sentiment':'negative','themes':[p['aspect'] for p in points],
            'points':points,'opinion_audit':{'state':'checked','content_class':'feedback','removed_count':0},'reason':'人工构造的协议验收候选观点'}
        with db.connect() as c:c.execute('UPDATE comments SET analysis=? WHERE task_id=? AND evidence_id=?',(db.dumps(a),tid,eid))

@pytest.fixture
def decision_model(monkeypatch):
    calls=[]
    async def fake(self,prompt,payload,schema):
        calls.append((schema,payload))
        if schema is ResearchBatch:
            rows=[]
            for x in payload['comments']:
                cid=x['evidence_id'].split(':')[-1];points=[]
                for p in x['points']:
                    kind=p['kind'];sent=p['sentiment'];obj='feature';ep='experience';scope=p['product_scope'];severity='unknown';quote=None
                    intent='positive_experience' if sent=='positive' else 'negative_experience'
                    if cid=='5':intent='feature_request';ep='request'
                    if cid=='6':obj='tutorial'
                    if cid=='7':intent='usage_question';ep='question'
                    if cid=='9':obj='third_party'
                    if x['evidence_id']=='xhs:1':severity='time_cost';quote='耽误了半小时'
                    if cid=='4':severity='task_blocked';quote='会议开不了'
                    if cid=='8':intent='fault_report';severity='data_loss';quote='丢失了文件'
                    points.append({'point_index':p['point_index'],'product_scope':scope,'intents':[intent],'evaluation_object':obj,
                        'sentiment':sent,'stance':'support' if sent=='positive' else 'oppose','epistemic':ep,'severity':severity,
                        'severity_quote':quote,'reason':'协议替身，不代表真实模型质量'})
                rows.append({'evidence_id':x['evidence_id'],'points':points})
            return schema(items=rows)
        if schema is TaxonomyPlan:
            return schema(domains=[{'name':'易用性与上手','definition':'发现功能与学习产品','children':[{'name':'功能发现与上手路径','definition':'同一能力的正反体验保留各自观点'}]},
                {'name':'文档与自动化','definition':'依据当前产品能力','children':[{'name':'文档编辑体验','definition':'编辑操作价值'},{'name':'日报自动化需求','definition':'明确需求，不计满意'}]},
                {'name':'数据可靠性','definition':'实际风险与疑问分开','children':[{'name':'文件可靠性与恢复','definition':'保留疑问与实际经历的区别'}]}])
        if schema is TaxonomyMapping:
            entries={c['name']:c for d in payload['taxonomy'] for c in d['children']}
            rows=[]
            for p in payload['points']:
                target='文件可靠性与恢复' if p['aspect'] in ('文件安全','数据安全') else '日报自动化需求' if p['aspect']=='日报' else '文档编辑体验' if p['aspect']=='文档编辑' else '功能发现与上手路径'
                c=entries[target];rows.append({'unit_id':p['unit_id'],'domain_id':c['domain_id'],'category_id':c['id'],'reason':'协议替身归并'})
            return schema(items=rows)
        if schema is DecisionNarrative:
            return schema(findings=[{'text':'本次样本包含功能发现与任务完成方面的反馈。','evidence_ids':[payload['issues'][0]['evidence'][0]['evidence_id']]}],issues=[
                {'issue_id':i['id'],'observation':{'text':'有评论提到「'+i['name']+'」相关的具体困难。','evidence_ids':[i['evidence'][0]['evidence_id']]},
                 'direction':{'text':'建议先核查「'+i['name']+'」的任务完成路径，再设计改进。','evidence_ids':[i['evidence'][0]['evidence_id']]},
                 'validation':{'text':'邀请反馈者复现「'+i['name']+'」的任务并观察阻断位置。','evidence_ids':[i['evidence'][0]['evidence_id']]},'metrics':['任务完成率','求助次数']} for i in payload['issues']])
        if schema is SupportCheck:return schema(items=[{'claim_id':c['claim_id'],'verdict':'supported','reason':'只验证结构流程的协议替身'} for c in payload['claims']])
        raise AssertionError(schema)
    monkeypatch.setattr(ModelClient,'call',fake)
    return calls

def decision_task(client,decision_model,product='飞书'):
    tid=create(client,product=product,mode='ai',analysis_version=2,insight_version=3,platforms=['xhs','dy'])
    upload(client,tid,sample());seed_analysis(tid)
    assert client.post(f'/api/tasks/{tid}/report/upgrade').status_code==200
    t=wait(client,tid);assert t['status']=='completed',t.get('error') or t.get('report',{}).get('generation_warnings')
    return tid,t

def test_hierarchy_coverage_and_source_statistics(client,decision_model):
    tid,t=decision_task(client,decision_model);r=t['report'];d=r['decision']
    assert r['decision_version']==3 and r['stats']['raw']==11 and r['stats']['unique']==10
    assert r['stats']['included']==8 and r['opinion_count']==9
    assert len(r['aspects'])==3 and len(r['themes'])==4
    a=next(a for a in r['aspects'] if a['name']=='易用性与上手')
    assert a['count']==5 and a['point_count']==5 and a['thread_count']==3
    assert a['known_user_count']==3  # Same platform u1 reused; dy:u1 is distinct.
    assert a['agreement_count']==2 and a['repeated_agreement_count']==1
    assert 'xhs:1' in r['evidence'] and 'dy:1' in r['evidence']
    assert d['sample_evidence']['thread_unknown_comments']==1
    assert sum(x['count'] for x in d['reason_summary'])==2
    assert all(x['status']=='pass' for x in d['quality_checks'])

def test_intents_objects_questions_and_divergence_are_independent(client,decision_model):
    tid,t=decision_task(client,decision_model);r=t['report']
    assert {'dy:6','xhs:9'}.isdisjoint(r['evidence'])
    assert not any('xhs:5' in s['evidence_ids'] for s in r['strengths'])
    assert any('xhs:5' in s['evidence_ids'] for s in r['needs'])
    assert not any('xhs:7' in s['evidence_ids'] for s in r['pain_points'])
    issue=next(i for i in r['decision']['issues'] if i['name']=='功能发现与上手路径')
    assert issue['disputed'] and 'dy:1' in issue['opposing_positive_ids'] and 'xhs:1' in issue['opposing_negative_ids']
    risk=next(i for i in r['decision']['issues'] if '文件' in i['name'])
    assert risk['priority']['level']=='P0' and risk['priority']['risk_watch']
    assert risk['priority']['business_value']=='未知'
    assert all(x['denominator']==r['stats']['included'] for x in r['decision']['intent_distribution'])

def test_brief_export_includes_contrary_evidence_anchors(client,decision_model):
    from html.parser import HTMLParser
    from insight.decision_exports import print_html,markdown
    _,task=decision_task(client,decision_model)
    report=task['report']
    # Positive counter-evidence is outside the negative issue representatives.
    issue=next(i for i in report['decision']['issues'] if i['disputed'])
    contrary=set(issue['opposing_positive_ids'])-set(issue['representative_ids'])
    assert contrary
    class Anchors(HTMLParser):
        def __init__(self):super().__init__();self.targets=set();self.links=set()
        def handle_starttag(self,tag,attrs):
            values=dict(attrs)
            if values.get('id'):self.targets.add(values['id'])
            if values.get('href','').startswith('#'):self.links.add(values['href'][1:])
    parser=Anchors();parser.feed(print_html(report,'brief'))
    assert parser.links<=parser.targets
    md=markdown(report,'brief')
    for eid in contrary:assert '#### 证据 '+eid.replace(':','-') in md

def test_export_modes_and_csv_match_one_data_source(client,decision_model):
    tid,t=decision_task(client,decision_model);r=t['report']
    brief=client.get(f'/api/tasks/{tid}/export/md?view=brief').text
    full=client.get(f'/api/tasks/{tid}/export/md?view=full').text
    assert len(brief)<len(full) and '执行摘要' in brief and '完整分类与具体议题' not in brief and '完整分类与具体议题' in full
    assert '研究优先级' in brief and '分母' in brief and 'P0' in brief
    for mode in ('brief','full'):
        text=client.get(f'/api/tasks/{tid}/export/html?view={mode}').text
        assert '决策洞察' in text and 'window.print()' in text and '<script' not in text
        assert 'dr-donut' in text and 'dr-matrix' in text and 'dr-dimension-row' in text
        assert 'print-color-adjust:exact' in text and 'report-print-page' in text
        assert f'报告 v{t["version"]}' in text
        attachment=client.get(f'/api/tasks/{tid}/export/html?view={mode}&download=true')
        assert attachment.status_code==200 and attachment.text==text
        assert attachment.headers['content-type'].startswith('text/html')
        assert attachment.headers['content-disposition'].startswith("attachment; filename*=UTF-8''")
        from urllib.parse import unquote
        assert ('精简版.html' if mode=='brief' else '完整版.html') in unquote(attachment.headers['content-disposition'])
        assert 'content-disposition' not in client.get(f'/api/tasks/{tid}/export/html?view={mode}').headers

    rows=list(csv.DictReader(io.StringIO(client.get(f'/api/tasks/{tid}/export/csv').text.lstrip('\ufeff'))))
    assert len(rows)==r['stats']['unique'] and sum(x['included']=='True' for x in rows)==r['stats']['included']
    assert all('original_opinion_points_json' in x and 'decision_domains' in x for x in rows)
    assert client.get(f'/api/tasks/{tid}/export/md?view=nope').status_code==422

def test_shared_renderer_escapes_input_and_explains_missing_build(client,decision_model,monkeypatch,tmp_path):
    from insight.decision_exports import print_html
    from insight.report_renderer import ReportRenderFailure
    from insight import config
    _,t=decision_task(client,decision_model)
    report=copy.deepcopy(t['report']);report['product']='</style><script>alert(1)</script>'
    text=print_html(report)
    assert '<script>' not in text and '&lt;script&gt;' in text
    monkeypatch.setattr(config,'ROOT',tmp_path)
    with pytest.raises(ReportRenderFailure,match='尚未构建'):print_html(report)

def test_cached_reanalysis_and_offline_exclusion_do_not_reuse_removed_claims(client,decision_model,monkeypatch):
    tid,t=decision_task(client,decision_model)
    before=len(decision_model)
    client.post(f'/api/tasks/{tid}/report/upgrade');new=wait(client,tid)
    # Existing taxonomy and changed mapping payload may need one call; initial research is cached.
    assert not any(s is ResearchBatch for s,_ in decision_model[before:])
    client.patch(f'/api/tasks/{tid}/comments/xhs:8',json={'excluded':True})
    async def forbidden(*args,**kwargs):raise AssertionError('offline path must not call a model')
    monkeypatch.setattr(ModelClient,'call',forbidden)
    client.post(f'/api/tasks/{tid}/report/recount');t=wait(client,tid);r=t['report']
    assert t['status']=='partial' and r['generation_mode']=='cached_decision_recount'
    assert r['stats']['included']==7 and 'xhs:8' not in r['evidence']
    assert all('xhs:8' not in c['evidence_ids'] for s in ('findings','recommendations','questions') for c in r[s])

def test_missing_identifiers_and_cycles_never_become_independent_sources():
    items=[{'evidence_id':'xhs:a','platform':'xhs','comment_id':'a','parent_comment_id':'b','parent_relation_known':True},
           {'evidence_id':'xhs:b','platform':'xhs','comment_id':'b','parent_comment_id':'a','parent_relation_known':True},
           {'evidence_id':'xhs:c','platform':'xhs','comment_id':'c','parent_comment_id':None},
           {'evidence_id':'xhs:d','platform':'xhs','comment_id':None,'parent_relation_known':True}]
    assert all(v is None for v in thread_keys(items).values())

def test_speculation_and_missing_impact_quote_cannot_create_severity():
    p=point('安全','文件可靠性','会不会丢文件','negative',kind='question',pain_point=None)
    x={'evidence_id':'xhs:1','text':'飞书会不会丢文件？','parent_text':None,'analysis':{'points':[p]},'overrides':{}}
    row=ResearchBatch(items=[{'evidence_id':'xhs:1','points':[{'point_index':0,'product_scope':'target','intents':['fault_report','positive_experience'],
        'evaluation_object':'feature','sentiment':'negative','stance':'uncertain','epistemic':'speculation','severity':'data_loss','severity_quote':'编造后果','group_quote':'个人用户','reason':'协议错标'}]}]).items[0]
    result=valid_annotation(row,x,[p]);a=result['points'][0]
    assert a['severity']=='unknown' and a['severity_quote'] is None and a['group_quote'] is None
    assert not set(a['intents']) & {'fault_report','positive_experience'} and result['checks']

def test_schema_failure_is_quarantined_and_report_still_has_verified_statistics(client,decision_model,monkeypatch):
    original=ModelClient.call
    async def fail_one(self,prompt,payload,schema):
        result=await original(self,prompt,payload,schema)
        if schema is ResearchBatch:
            raw=result.model_dump();raw['items'][0]['points'][0]['evaluation_object']='unknown-invalid'
            raise ModelOutputFailure('ResearchBatch',['items.0.points.0.evaluation_object [literal_error]'],raw)
        return result
    monkeypatch.setattr(ModelClient,'call',fail_one)
    tid=create(client,mode='ai',analysis_version=2,insight_version=3,platforms=['xhs','dy']);upload(client,tid,sample());seed_analysis(tid)
    client.post(f'/api/tasks/{tid}/report/upgrade');t=wait(client,tid)
    assert t['status']=='partial' and t['report'] and 'xhs:1' not in t['report']['evidence']
    assert t['report']['generation_warnings']

def test_research_invalid_enum_recovers_only_failed_row(client,decision_model,monkeypatch):
    original=ModelClient.call
    async def invalid_batch(self,prompt,payload,schema):
        result=await original(self,prompt,payload,schema)
        if schema is ResearchBatch and len(payload['comments'])>1 and any(x['evidence_id']=='xhs:1' for x in payload['comments']):
            raw=result.model_dump();raw['items'][0]['points'][0]['intents']=['experience']
            raise ModelOutputFailure(schema.__name__,['items.0.points.0.intents.0 [literal_error]'],raw)
        return result
    monkeypatch.setattr(ModelClient,'call',invalid_batch)
    tid,t=decision_task(client,decision_model)
    assert t['validation_summary']['comment_failures']==0
    assert not any('ResearchBatch' in w for w in t['report']['generation_warnings'])
    batches=[p for s,p in decision_model if s is ResearchBatch]
    assert len(batches)==3 and len(batches[1]['comments'])==1  # 8+2 comments, only first failed row retried.
    assert batches[1]['comments'][0]['evidence_id']=='xhs:1'

def test_repair_endpoint_reuses_good_analysis_and_mappings(client,decision_model):
    tid,t=decision_task(client,decision_model)
    before={x['evidence_id']:(x['analysis'],x['overrides']) for x in db.comments(tid)}
    with db.connect() as c:
        rows=c.execute("SELECT input_hash,result FROM research_cache WHERE task_id=? AND cache_key='comment:xhs:1'",(tid,)).fetchall()
        for row in rows:
            value=json.loads(row['result']);value['status']='needs_review'
            c.execute("UPDATE research_cache SET result=? WHERE task_id=? AND cache_key='comment:xhs:1' AND input_hash=?",(db.dumps(value),tid,row['input_hash']))
    current=client.get('/api/tasks/'+tid).json();assert current['validation_summary']['comment_ids']==['xhs:1']
    start=len(decision_model)
    assert client.post(f'/api/tasks/{tid}/analysis/repair').status_code==200
    repaired=wait(client,tid)
    assert repaired['status']=='completed' and repaired['validation_summary']['comment_failures']==0
    assert repaired['stats']==t['stats']
    assert {x['evidence_id']:(x['analysis'],x['overrides']) for x in db.comments(tid)}==before
    assert [s for s,_ in decision_model[start:]]==[ResearchBatch]
    assert client.post(f'/api/tasks/{tid}/analysis/repair').status_code==409

def test_research_transport_failure_preserves_other_batch_annotations(client,decision_model,monkeypatch):
    original=ModelClient.call
    async def broken(self,prompt,payload,schema):
        if schema is ResearchBatch and len(payload['comments'])==1:raise ModelFailure('模型连接失败或超时')
        result=await original(self,prompt,payload,schema)
        if schema is ResearchBatch:
            raw=result.model_dump();raw['items'][0]['points'][0]['intents']=['invalid']
            raise ModelOutputFailure(schema.__name__,['items.0.points.0.intents.0 [literal_error]'],raw)
        return result
    monkeypatch.setattr(ModelClient,'call',broken)
    tid=create(client,mode='ai',analysis_version=2,insight_version=3,platforms=['xhs','dy']);upload(client,tid,sample());seed_analysis(tid)
    client.post(f'/api/tasks/{tid}/report/upgrade');t=wait(client,tid)
    assert t['status']=='failed' and '连接' in t['error']
    rows={x['evidence_id']:x for x in db.comments(tid)}
    assert rows['xhs:1']['effective']['research_status']=='needs_review'
    assert all(rows[eid]['effective']['research_status']=='checked' for eid in ('xhs:2','xhs:3','xhs:4','dy:1'))

def test_taxonomy_is_product_specific_and_existing_categories_are_reused(client,decision_model,monkeypatch):
    original=ModelClient.call
    async def video(self,prompt,payload,schema):
        if schema is TaxonomyPlan and payload['product']=='剪映':
            return schema(domains=[{'name':'视频创作与导出','definition':'当前视频工具的实际能力','children':[{'name':'导出性能','definition':'渲染导出过程'}]}])
        if schema is TaxonomyMapping and payload['product']=='剪映':
            c=payload['taxonomy'][0]['children'][0]
            return schema(items=[{'unit_id':p['unit_id'],'domain_id':c['domain_id'],'category_id':c['id'],'reason':'当前产品导出场景'} for p in payload['points']])
        return await original(self,prompt,payload,schema)
    monkeypatch.setattr(ModelClient,'call',video)
    tid=create(client,product='剪映',mode='ai',analysis_version=2,insight_version=3)
    upload(client,tid,[{'comment_id':'100','text':'剪映导出太慢，等了半天','parent_comment_id':None}])
    a={'analysis_version':2,'points':[point('导出','导出等待','导出太慢')],'relevance':'target','has_feedback':True,'suspected_ad':False,'opinion_audit':{'state':'checked'}}
    with db.connect() as c:c.execute('UPDATE comments SET analysis=? WHERE task_id=?',(db.dumps(a),tid))
    client.post(f'/api/tasks/{tid}/report/upgrade');t=wait(client,tid)
    assert [a['name'] for a in t['report']['aspects']]==['视频创作与导出']
    assert '导出性能'==t['report']['themes'][0]['name']

def test_new_secondary_category_does_not_rebuild_primary_taxonomy(client,decision_model,monkeypatch):
    tid,t=decision_task(client,decision_model);taxonomy=copy.deepcopy(t['report']['decision']['taxonomy'])
    before=len(decision_model);upload(client,tid,[{'platform':'xhs','comment_id':'new','text':'飞书分享文档审批费劲','parent_comment_id':None}])
    a={'analysis_version':2,'points':[point('文档权限','分享审批困难','分享文档审批费劲')],'relevance':'target','has_feedback':True,'suspected_ad':False,'opinion_audit':{'state':'checked'}}
    with db.connect() as c:c.execute("UPDATE comments SET analysis=? WHERE task_id=? AND evidence_id='xhs:new'",(db.dumps(a),tid))
    original=ModelClient.call
    async def add(self,prompt,payload,schema):
        if schema is TaxonomyMapping:
            known=[p for p in payload['points'] if p['unit_id']!='xhs:new#p1'];result=await original(self,prompt,{**payload,'points':known},schema)
            if len(known)!=len(payload['points']):
                from insight.decision_schemas import MappingItem
                result.items.append(MappingItem(unit_id='xhs:new#p1',domain_id=payload['taxonomy'][1]['id'],category_id=None,new_category='文档分享审批',reason='已有文档维度下的新问题'))
            return result
        return await original(self,prompt,payload,schema)
    monkeypatch.setattr(ModelClient,'call',add)
    client.post(f'/api/tasks/{tid}/report/upgrade');new=wait(client,tid)['report']
    assert not any(s is TaxonomyPlan for s,_ in decision_model[before:])
    assert [x['id'] for x in new['decision']['taxonomy']]==[x['id'] for x in taxonomy]
    assert any(c['name']=='文档分享审批' for d in new['decision']['taxonomy'] for c in d['children'])
    batches=[p for s,p in decision_model[before:] if s is ResearchBatch]
    assert [x['evidence_id'] for p in batches for x in p['comments']]==['xhs:new']

def test_excluded_parent_is_not_sent_to_research_model(client,decision_model):
    tid,t=decision_task(client,decision_model);before=len(decision_model)
    client.patch(f'/api/tasks/{tid}/comments/xhs:1',json={'excluded':True})
    client.post(f'/api/tasks/{tid}/report/upgrade');t=wait(client,tid)
    batches=[p for s,p in decision_model[before:] if s is ResearchBatch]
    children=[x for p in batches for x in p['comments'] if x['evidence_id'] in ('xhs:2','xhs:3')]
    assert children and all(not x.get('parent_text') and not x['points'][0].get('context_quote') for x in children)
    assert {'xhs:1','xhs:2','xhs:3'}.isdisjoint(t['report']['evidence'])

def test_manual_object_correction_updates_eligibility_and_invalidates_report(client,decision_model):
    tid,t=decision_task(client,decision_model)
    item=client.get(f'/api/tasks/{tid}/comments/dy:1').json();points=copy.deepcopy(item['effective']['points'])
    for p in points:p['evaluation_object']='tutorial'
    response=client.patch(f'/api/tasks/{tid}/comments/dy:1',json={'points':points,'note':'人工确认评价的是教程'})
    assert response.status_code==200 and not response.json()['effective']['has_feedback']
    assert client.get(f'/api/tasks/{tid}/export/md').status_code==409
    client.post(f'/api/tasks/{tid}/report/recount');r=wait(client,tid)['report']
    assert 'dy:1' not in r['evidence'] and r['stats']['included']==7

def test_same_comment_two_opinions_in_one_dimension_counts_coverage_once():
    p={**point('易用性','入口困难','入口找不到'), 'unit_id':'xhs:a#p1','evidence_id':'xhs:a','evaluation_object':'feature',
       'intents':['negative_experience'],'epistemic':'experience','severity_quote':None}
    q={**p,'unit_id':'xhs:a#p2','topic':'功能学习困难'}
    x={'evidence_id':'xhs:a','platform':'xhs','comment_id':'a','parent_relation_known':True,'user_id':None,'note_id':None}
    summary=summarize([p,q],10,{'xhs:a':x},thread_keys([x]))
    assert summary['count']==1 and summary['point_count']==2 and summary['share']==10
    assert summary['known_user_count'] is None and summary['thread_count']==1

def test_difficult_is_not_a_claim_of_task_blockage_and_raw_judgment_is_retained():
    p=point('易用性','上手困难','飞书太难用')
    item={'evidence_id':'xhs:1','text':'飞书太难用','parent_text':None,'analysis':{'points':[p]},'overrides':{}}
    row=ResearchBatch(items=[{'evidence_id':'xhs:1','points':[{'point_index':0,'product_scope':'target','intents':['negative_experience'],
        'evaluation_object':'feature','sentiment':'negative','stance':'oppose','epistemic':'experience','severity':'task_blocked','severity_quote':'太难用','reason':'错把难用升级为阻断'}]}]).items[0]
    value=valid_annotation(row,item,[p])
    assert value['points'][0]['severity']=='unknown'
    assert value['raw_points'][0]['severity']=='task_blocked'
    assert any(c['rule']=='severity_semantic_cue' for c in value['checks'])

def test_taxonomy_schema_cannot_expand_into_unbounded_secondary_labels():
    from pydantic import ValidationError
    row={'name':'维度','definition':'范围','children':[{'name':str(i),'definition':'细分'} for i in range(5)]}
    with pytest.raises(ValidationError):TaxonomyPlan(domains=[row])

def test_onboarding_and_usability_aliases_share_a_primary_without_merging_machine_learning():
    from insight.decision_analysis import coalesce_domains
    domains=[{'id':'d1','name':'上手与学习路径','definition':'上手','children':[{'id':'c1','name':'入口发现','domain_id':'d1'}]},
        {'id':'d2','name':'易用性与操作负担','definition':'使用负担','children':[{'id':'c2','name':'操作步骤','domain_id':'d2'}]},
        {'id':'d3','name':'机器学习能力','definition':'产品能力','children':[{'id':'c3','name':'训练功能','domain_id':'d3'}]}]
    mapping={'u1':{'domain_id':'d1','category_id':'c1'},'u2':{'domain_id':'d2','category_id':'c2'}}
    merged,mapping=coalesce_domains(domains,mapping)
    assert [d['name'] for d in merged]==['易用性与上手','机器学习能力']
    assert mapping['u1']['domain_id']==mapping['u2']['domain_id'] and len(merged[0]['children'])==2
    assert merged[0]['aliases']==['上手与学习路径','易用性与操作负担']

def test_rejected_generalization_remains_in_quality_review_during_offline_recount(client,decision_model,monkeypatch):
    import asyncio
    from insight.decision_analysis import make_report
    tid,t=decision_task(client,decision_model);old=copy.deepcopy(t['report'])
    old['findings'].append({'text':'用户反馈集中在学习成本。','evidence_ids':['xhs:1']})
    async def forbidden(*args,**kwargs):raise AssertionError('No model in offline audit')
    async def checkpoint():pass
    monkeypatch.setattr(ModelClient,'call',forbidden)
    r=asyncio.run(make_report(tid,t['config'],checkpoint,statistics_only=True,previous_report=old))
    assert all('集中在' not in c['text'] for c in r['findings'])
    assert any('集中在' in c['text'] for c in r['decision']['generated_review'])

def test_many_threads_from_one_known_account_do_not_raise_frequency_priority():
    rows=[];by={};threads={}
    for i in range(3):
        eid='xhs:'+str(i);u={**point('体验','耗时','花了半小时'),'evaluation_object':'feature','intents':['negative_experience'],
            'epistemic':'experience','severity':'time_cost','severity_quote':'半小时','unit_id':eid+'#p1','evidence_id':eid}
        rows.append(u);by[eid]={'platform':'xhs','user_id':'same','note_id':'n','parent_comment_id':None};threads[eid]='thread'+str(i)
    s=summarize(rows,10,by,threads)
    assert s['thread_count']==3 and s['known_user_count']==1 and priority(rows,s,10)['level']=='P2'
    by['xhs:1']['user_id']='another';s=summarize(rows,10,by,threads)
    assert priority(rows,s,10)['level']=='P1'

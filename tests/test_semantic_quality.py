"""Protocol regressions. Constructed expectations are not human accuracy estimates."""
import asyncio
import copy
from collections import defaultdict
import pytest
from insight import db
from insight.analysis import ModelClient,ModelOutputFailure
from insight.semantic_quality import clean_meaning,research_priority,group,comparable,quality_metrics,executive
from insight.semantic_schemas import MeaningBatch,AssignmentBatch,MembershipBatch,ComparisonBatch,ConsolidationPlan,ScopeBatch
from insight.schemas import Grouping,SupportCheck
from insight.decision_analysis import stage_call
from test_pipeline import create,upload,wait
from test_decision_insights import seed_analysis,sample,point,decision_model

def test_role_fields_never_repeat_or_invent_consequences():
    text='创建表格时无法审批权限'
    row={'unit_id':'xhs:1#p1','state':'specific','object_name':'权限审批','user_task':'编辑表格','obstacle':'无法审批',
         'manifestation_quote':'无法审批权限','scenario_quote':'无法审批权限','scenario_label':'权限故障',
         'impact_quote':'无法审批权限','expectation_quote':'希望管理员代为审批','impact_kind':'task_blocked'}
    value=clean_meaning(row,{'text':text},{'basis':'direct','epistemic':'experience','quote':'无法审批权限'})
    assert value['manifestation_quote']=='无法审批权限'
    assert value['scenario_quote'] is None and value['impact_quote'] is None and value['expectation_quote'] is None
    assert value['impact_kind']=='unknown' and value['scenario_label'] is None

def test_speculation_does_not_become_a_severe_incident():
    value=clean_meaning({'state':'specific','object_name':'存储','user_task':'保存','obstacle':'担忧丢失','impact_quote':'万一丢数据','impact_kind':'data_loss'},
        {'text':'万一丢数据怎么办'},{'basis':'direct','epistemic':'question','quote':'万一丢数据'})
    assert value['impact_quote'] is None and value['impact_kind']=='unknown'

@pytest.mark.parametrize('task,obstacle',[(None,'卡顿'),('了解是否需要会员',None),(None,None)])
def test_optional_task_or_obstacle_does_not_discard_valid_specific_feedback(task,obstacle):
    value=clean_meaning({'state':'specific','object_name':'目标产品的明确能力','user_task':task,'obstacle':obstacle,'reason':'有效提问或体验，不强填未知字段'},
        {'text':'需要会员吗'},{'basis':'direct','epistemic':'question','quote':'需要会员吗'})
    assert value['state']=='specific' and value['user_task']==task and value['obstacle']==obstacle

def test_same_name_is_not_an_issue_identity():
    from insight.semantic_quality import definition_id
    a={'name':'表格使用问题','domain':'表格','object_name':'编辑权限','user_task':'申请审批','obstacle':'无法审批'}
    b={**a,'object_name':'附件','user_task':'下载视频','obstacle':'下载失败'}
    assert definition_id(a)!=definition_id(b)

def test_low_frequency_risk_is_not_a_confirmed_p0():
    rows=[{'epistemic':'experience','evaluation_object':'feature','intents':['fault_report'],'product_scope':'target','semantic_state':'specific',
           'basis':'direct','sentiment':'negative','severity':'data_loss','severity_quote':'文件丢失','evidence_id':'xhs:1'}]
    counts={'count':1,'thread_count':1,'known_user_count':1,'detailed_direct_count':1,'share':1}
    p=research_priority(rows,counts,100)
    assert p['level']=='P1' and p['risk_note']=='高潜在风险、低证据量、待验证'
    assert len(p['factors'])==4 and p['development_priority']=='未知'

def test_repeated_multithread_problem_can_be_prioritized_without_fabricated_impact():
    rows=[{'epistemic':'experience','evaluation_object':'feature','intents':['negative_experience'],'product_scope':'target','semantic_state':'specific',
           'basis':'direct','sentiment':'negative','severity':'unknown','evidence_id':'xhs:1'}]
    p=research_priority(rows,{'count':12,'thread_count':5,'known_user_count':10,'detailed_direct_count':1,'share':12},100)
    assert p['level']=='P1' and p['severity']=='影响信息不足'

def test_executive_reuses_actual_observation_citations_not_display_representatives():
    issue={'name':'任务中断','count':4,'thread_count':3,'known_user_count':3,'representative_ids':['xhs:display'],
        'observation':{'text':'有依据的具体观察','evidence_ids':['xhs:actual-support']},'validation':None,'priority':{}}
    findings=executive({'decision':{'issues':[issue]},'strengths':[]})
    assert findings[0]['evidence_ids']==['xhs:actual-support']

def test_executive_risk_slot_prefers_severe_low_frequency_signal_over_task_blockage():
    base={'name':'核查','count':1,'thread_count':1,'known_user_count':1,'representative_ids':['xhs:1'],'observation':None,'validation':None}
    blocked={**base,'priority':{'level':'P1','severity':'明确任务阻断','risk_watch':True}}
    risk={**base,'name':'待核查严重风险','priority':{'level':'P1','severity':'严重风险线索','risk_watch':True}}
    findings=executive({'decision':{'issues':[blocked,risk]},'strengths':[]})
    assert findings[0]['title']=='待核查严重风险' and findings[0]['kind']=='risk'

def test_partial_typed_semantic_batch_preserves_valid_neighbours(client,monkeypatch):
    tid=create(client)
    row={'unit_id':'xhs:1#p1','state':'overall','reason':'无具体指向'}
    async def wrong(self,prompt,payload,schema):raise ModelOutputFailure('MeaningBatch',['items.1.state [literal_error]'],{'items':[row,{**row,'unit_id':'xhs:2#p1','state':'invalid'}]})
    monkeypatch.setattr(ModelClient,'call',wrong)
    async def checkpoint():pass
    value=asyncio.run(stage_call(tid,'test','test',{},MeaningBatch,ModelClient(),checkpoint,[]))
    assert len(value.items)==1 and value.items[0].unit_id=='xhs:1#p1'

@pytest.mark.parametrize('audit_stage',['member','scope'])
def test_membership_audit_splits_module_based_false_merge(client,audit_stage):
    tid=create(client);units=[];by={}
    for i,task in enumerate(('审批编辑权限','下载附件'),1):
        eid='xhs:'+str(i);by[eid]={'text':task+'失败'}
        units.append({'unit_id':eid+'#p1','evidence_id':eid,'basis':'direct','sentiment':'negative','intents':['negative_experience'],
            'meaning':{'user_task':task},'semantic_state':'specific','aspect':'表格','topic':task+'失败','quote':task+'失败'})
    definition={'name':'表格问题','domain':'数据工具','object_name':'表格','user_task':'使用表格','obstacle':'操作失败','definition':'不正确的宽泛分类'}
    async def fake(tid,key,prompt,payload,schema,*args):
        if schema is AssignmentBatch:return schema(items=[{'unit_id':u['unit_id'],'new_issue':definition,'reason':'测试故意错并'} for u in units])
        if schema is MembershipBatch:
            return schema(items=[{'unit_id':x['unit_id'],'issue_id':x['issue']['id'],'verdict':'split' if audit_stage=='member' else 'supported',
                'split_issue':{**definition,'name':x['meaning']['user_task']+'障碍','user_task':x['meaning']['user_task'],'object_name':x['meaning']['user_task']},'reason':'实际任务不同'} for x in payload['items']])
        if schema is ScopeBatch:return schema(items=[{'issue_id':x['issue_id'],'verdict':'split','name':'错误宽泛议题','definition':'需按具体任务拆分','reason':'逐成员虽通过，但共享定义包含不同任务'} for x in payload['issues']])
        raise AssertionError(schema)
    async def checkpoint():pass
    domains,mapping,missing,meta=asyncio.run(group(tid,{'product':'任意产品'},units,by,ModelClient(),checkpoint,[],fake))
    assert not missing and mapping[units[0]['unit_id']]['category_id']!=mapping[units[1]['unit_id']]['category_id']
    assert all(x['verdict']=='split' for x in meta['membership_checks' if audit_stage=='member' else 'scope_checks'])

def test_different_features_do_not_create_opposing_evidence(client):
    positive={'unit_id':'xhs:1#p1','evidence_id':'xhs:1','semantic_state':'specific','evaluation_object':'feature','product_scope':'target','epistemic':'experience','intents':['positive_experience'],'sentiment':'positive'}
    negative={**positive,'unit_id':'xhs:2#p1','evidence_id':'xhs:2','intents':['negative_experience'],'sentiment':'negative'}
    async def fake(tid,key,prompt,payload,schema,*args):return schema(items=[{'pair_id':x['pair_id'],'verdict':'different','reason':'画板与保存入口不同'} for x in payload['pairs']])
    async def checkpoint():pass
    verified,checks=asyncio.run(comparable(create(client),{'bad-group':[positive,negative]},
        {'xhs:1':{'text':'画板流畅'},'xhs:2':{'text':'保存入口找不到'}},ModelClient(),checkpoint,[],fake))
    assert not verified and checks[0]['verdict']=='different'

def test_wording_variants_reconcile_then_every_member_is_rechecked(client):
    tid=create(client);by={'xhs:1':{'text':'界面操作很卡'},'xhs:2':{'text':'员工端操作卡顿'}}
    units=[{'unit_id':eid+'#p1','evidence_id':eid,'basis':'direct','sentiment':'negative','intents':['negative_experience'],
        'meaning':{'user_task':'日常界面操作'},'semantic_state':'specific'} for eid in by]
    definition={'name':'界面卡顿','domain':'运行体验','object_name':'操作界面','user_task':'日常操作','obstacle':'响应卡顿','definition':'相同界面操作的响应问题'}
    verified=[]
    async def fake(tid,key,prompt,payload,schema,*args):
        if schema is AssignmentBatch:return schema(items=[{'unit_id':x['unit_id'],'new_issue':{**definition,'name':by[x['unit_id'].rsplit('#p',1)[0]]['text']},'reason':'故意过分拆分'} for x in payload['items']])
        if schema is ScopeBatch:return schema(items=[{'issue_id':x['issue_id'],'verdict':'supported','name':x['definition']['name'],'definition':x['definition']['definition'],'reason':'共同范围协议替身'} for x in payload['issues']])
        if schema is ConsolidationPlan:return schema(groups=[{'name':'界面操作卡顿','definition':'用户日常操作相同界面的响应卡顿，保留设备和场景差异，不推断同根因','member_issue_ids':[x['id'] for x in payload['issues']]}])
        if schema is MembershipBatch:
            assert {m['unit_id'] for scope in payload['issue_scopes'] for m in scope['members']}=={u['unit_id'] for u in units}
            verified.extend(x['unit_id'] for x in payload['items'])
            return schema(items=[{'unit_id':x['unit_id'],'issue_id':x['issue']['id'],'verdict':'supported','reason':'同一任务与障碍'} for x in payload['items']])
        raise AssertionError(schema)
    async def checkpoint():pass
    _,mapping,missing,meta=asyncio.run(group(tid,{'product':'任意工具'},units,by,ModelClient(),checkpoint,[],fake))
    assert not missing and len({m['category_id'] for m in mapping.values()})==1
    assert set(verified)=={u['unit_id'] for u in units}

def test_generic_attitude_can_be_corrected_to_overall_without_discarding_it(client):
    tid=create(client);uid='xhs:1#p1'
    unit={'unit_id':uid,'evidence_id':'xhs:1','basis':'direct','sentiment':'negative','intents':['negative_experience'],
        'meaning':{},'semantic_state':'specific'}
    async def fake(tid,key,prompt,payload,schema,*args):
        if schema is AssignmentBatch:return schema(items=[{'unit_id':uid,'new_issue':{'name':'不好用','domain':'操作','object_name':'产品','definition':'初次误标的具体问题'},'reason':'初次标注'}])
        if schema is MembershipBatch:return schema(items=[{'unit_id':uid,'issue_id':payload['items'][0]['issue']['id'],'verdict':'overall','reason':'未说明具体任务或功能，只表达整体态度'}])
        raise AssertionError(schema)
    async def checkpoint():pass
    domains,mapping,missing,meta=asyncio.run(group(tid,{'product':'任意产品'},[unit],{'xhs:1':{'text':'不好用'}},ModelClient(),checkpoint,[],fake))
    assert not missing and uid in mapping and domains[0]['name']=='整体评价'
    assert meta['membership_checks'][0]['verdict']=='overall'

def test_consolidation_id_failure_repairs_once_and_preserves_unambiguous_members(client):
    tid=create(client);by={'xhs:'+str(i):{'text':'原文'+str(i)} for i in range(4)}
    units=[{'unit_id':eid+'#p1','evidence_id':eid,'basis':'direct','sentiment':'negative','intents':['negative_experience'],
        'meaning':{},'semantic_state':'specific'} for eid in by]
    definition={'name':'问题','domain':'任务','object_name':'操作','definition':'原有具体问题'}
    repairs=[];audited=[];warnings=[]
    async def fake(tid,key,prompt,payload,schema,*args):
        if schema is AssignmentBatch:return schema(items=[{'unit_id':x['unit_id'],'new_issue':{**definition,'name':x['unit_id']},'reason':'初始独立问题'} for x in payload['items']])
        if schema is ScopeBatch:return schema(items=[{'issue_id':x['issue_id'],'verdict':'supported','name':x['definition']['name'],'definition':x['definition']['definition'],'reason':'共同范围协议替身'} for x in payload['issues']])
        if schema is ConsolidationPlan:
            if key.endswith('_repair'):
                repairs.append(payload['validation_errors']);return schema.model_validate(payload['previous_plan'])
            ids=[x['id'] for x in payload['issues']]
            return schema(groups=[{'name':'同一障碍','definition':'确实相同的问题','member_issue_ids':ids[:2]},
                {'name':'歧义','definition':'包含冲突成员','member_issue_ids':[ids[2],ids[2]]}])
        if schema is MembershipBatch:
            audited.extend(x['unit_id'] for x in payload['items'])
            return schema(items=[{'unit_id':x['unit_id'],'issue_id':x['issue']['id'],'verdict':'supported','reason':'逐条原文通过'} for x in payload['items']])
        raise AssertionError(schema)
    async def checkpoint():pass
    _,mapping,missing,meta=asyncio.run(group(tid,{'product':'任意产品'},units,by,ModelClient(),checkpoint,warnings,fake))
    assert len(repairs)==1 and repairs[0]['duplicate'] and repairs[0]['missing']
    assert not missing and set(audited)=={u['unit_id'] for u in units}
    assert mapping[units[0]['unit_id']]==mapping[units[1]['unit_id']]
    assert mapping[units[2]['unit_id']]!=mapping[units[3]['unit_id']]
    assert warnings  # Conflicting/omitted IDs remain explicit, never silently accepted.

def test_strict_pipeline_uses_same_statistics_sources_and_keeps_raw_analysis(client,decision_model,monkeypatch):
    original=ModelClient.call
    async def semantic_model(self,prompt,payload,schema):
        if schema is SupportCheck:assert len(payload['claims'])==1  # No cross-claim evidence leakage.
        if schema.__name__=='DecisionNarrative':assert all('note_body' not in e for issue in payload['issues'] for e in issue['evidence'])
        if schema is MeaningBatch:
            return schema(items=[{'unit_id':x['unit_id'],'state':'non_product' if '教程根本' in x['text'] else 'specific',
                'object_name':x['original_point']['aspect'],'user_task':x['original_point']['topic'],'obstacle':x['original_point']['topic'],
                'manifestation_quote':x['original_point']['quote'],'reason':'协议替身，不代表真实准确率'} for x in payload['items']])
        if schema is AssignmentBatch:
            return schema(items=[{'unit_id':x['unit_id'],'new_issue':{'name':x['meaning']['user_task'],'domain':x['meaning']['object_name'],
                'object_name':x['meaning']['object_name'],'user_task':x['meaning']['user_task'],'obstacle':x['meaning']['obstacle'],'definition':'同一明确对象和任务'},'reason':'具体任务'} for x in payload['items']])
        if schema is MembershipBatch:return schema(items=[{'unit_id':x['unit_id'],'issue_id':x['issue']['id'],'verdict':'supported','reason':'协议成员检查'} for x in payload['items']])
        if schema is ScopeBatch:return schema(items=[{'issue_id':x['issue_id'],'verdict':'supported','name':x['definition']['name'],'definition':x['definition']['definition'],'reason':'共同范围协议替身'} for x in payload['issues']])
        if schema is ConsolidationPlan:return schema(groups=[{'name':x['name'],'definition':x['definition'],'member_issue_ids':[x['id']]} for x in payload['issues']])
        if schema is ComparisonBatch:return schema(items=[{'pair_id':x['pair_id'],'verdict':'different','reason':'测试保守处理'} for x in payload['pairs']])
        return await original(self,prompt,payload,schema)
    monkeypatch.setattr(ModelClient,'call',semantic_model)
    tid=create(client,mode='ai',analysis_version=2,insight_version=3,semantic_version=1,platforms=['xhs','dy']);upload(client,tid,sample());seed_analysis(tid)
    before={x['evidence_id']:copy.deepcopy(x['analysis']) for x in db.comments(tid)}
    client.post(f'/api/tasks/{tid}/report/upgrade');t=wait(client,tid)
    assert t['status'] in ('completed','partial'),t.get('error')
    report=t['report'];d=report['decision'];assert d['semantic_revision']=='task-issue-1'
    assert d['quality_metrics']['human_accuracy'] is None
    assert d['quality_metrics']['opinion_mapping_coverage']['numerator']==report['opinion_count']
    assert 'dy:6' not in report['evidence']  # Tutorial is not product evidence.
    assert {x['evidence_id']:x['analysis'] for x in db.comments(tid)}==before
    assert all(not i['disputed'] for i in d['issues'])
    assert sum(x['count'] for x in d['reason_summary'])==report['stats']['unique']-report['stats']['included']
    assert all(x['status']=='pass' for x in d['quality_checks'])
    for eid,links in d['evidence_links'].items():
        assert links['unit_ids'] and client.get(f'/api/tasks/{tid}/comments/{eid}').json()['decision_trace']==links
    for issue in d['issues']:
        for field in ('observation','deeper_need','direction','validation','cause_hypotheses'):
            claims=issue[field] if isinstance(issue[field],list) else [issue[field]] if issue[field] else []
            for index,claim in enumerate(claims):
                assert all('issues:'+issue['id']+':'+field+':'+str(index) in d['evidence_links'][eid]['claims'] for eid in claim['evidence_ids'])
    # Recount is entirely offline and retains the audited atomic memberships.
    async def forbidden(*args,**kwargs):raise AssertionError('Offline recount called model')
    monkeypatch.setattr(ModelClient,'call',forbidden)
    assert client.post(f'/api/tasks/{tid}/report/recount').status_code==200
    offline=wait(client,tid)
    assert offline['report']['stats']==report['stats']
    assert [(x['name'],x['count']) for x in offline['report']['themes']]==[(x['name'],x['count']) for x in report['themes']]

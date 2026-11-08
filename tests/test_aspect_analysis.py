"""V2 contracts use deterministic model substitutes; no model-quality accuracy claims."""
import copy
import csv
import io
import json
from collections import defaultdict
import pytest
from insight import db
from insight.analysis import ModelClient,ModelFailure,ModelOutputFailure
from insight.aspect_analysis import validate_points,summarize_units,apply_audit,validate_extraction
from insight.schemas import AspectExtraction,OpinionGrouping,TopicAdviceBatch,Narrative,SupportCheck,OpinionAuditBatch
from test_pipeline import create,upload,wait

def point(aspect,topic,quote,sentiment='negative',**extra):
    return {'product_scope':'target','aspect':aspect,'topic':topic,'kind':'problem' if sentiment=='negative' else 'experience',
        'sentiment':sentiment,'basis':'direct','scenario':None,'pain_point':topic if sentiment=='negative' else None,
        'impact':None,'expected_outcome':None,'keywords':[aspect],'quote':quote,'context_quote':None,**extra}

def dataset():
    return {'notes':[{'note_id':'n','title':'飞书使用体验'}],'comments':[
        {'comment_id':'1','note_id':'n','text':'飞书文档编辑很顺手，但手机修改以后电脑一直不同步，开会前只好手动复制。'},
        {'comment_id':'2','note_id':'n','text':'我也是','parent_comment_id':'3'},
        {'comment_id':'3','note_id':'n','text':'飞书手机改完电脑看不到更新，最后把内容发微信重新粘贴。'},
        {'comment_id':'4','note_id':'n','text':'飞书手机改完电脑看不到更新，最后把内容发微信重新粘贴。'},
        {'comment_id':'5','note_id':'n','text':'希望飞书能按项目给通知分组，现在提醒太多找重点很费劲。'},
        {'comment_id':'6','note_id':'n','text':'飞书文档顺手，钉钉消息推送比较及时。'},
        {'comment_id':'7','note_id':'n','text':'飞书会不会把文件弄丢？'},
        {'comment_id':'8','note_id':'n','text':'蹲'},
        {'comment_id':'1','note_id':'n','text':'飞书文档编辑很顺手，但手机修改以后电脑一直不同步，开会前只好手动复制。'},
    ]}

def opinions(x):
    cid=x['evidence_id'].split(':')[-1]
    if cid=='1':return [point('文档编辑','编辑体验顺手','文档编辑很顺手','positive'),
        point('多端同步','手机编辑后桌面未同步','手机修改以后电脑一直不同步',impact='开会前只好手动复制',scenario='开会前')]
    if cid in ('3','4'):return [point('多端同步','手机修改未更新到电脑','手机改完电脑看不到更新',impact='把内容发微信重新粘贴')]
    if cid=='2':return [point('多端同步','跨端同步延迟','我也是',basis='agreement',context_quote='手机改完电脑看不到更新')] if x.get('parent_text') else []
    if cid=='5':return [point('通知管理','提醒多难找重点','提醒太多找重点很费劲'),
        point('通知管理','按项目分组通知','希望飞书能按项目给通知分组','neutral',kind='request',expected_outcome='按项目给通知分组')]
    if cid=='6':return [point('文档编辑','编辑操作顺手','飞书文档顺手','positive'),
        point('消息推送','推送及时','钉钉消息推送比较及时','positive',product_scope='other_product')]
    if cid=='7':return [point('数据安全','文件安全疑问','会不会把文件弄丢','neutral',kind='question')]
    return []

@pytest.fixture
def model(monkeypatch):
    calls=[]
    async def fake(self,prompt,payload,schema):
        calls.append((schema,payload))
        if schema is AspectExtraction:
            return schema(items=[{'evidence_id':x['evidence_id'],'relevance':'uncertain' if x['text']=='蹲' else 'target',
                'reason':'结构化协议替身','suspected_ad':False,'points':opinions(x)} for x in payload['comments']])
        if schema is OpinionAuditBatch:
            return schema(items=[{'evidence_id':x['evidence_id'],'content_class':'feedback' if x['candidate_points'] else 'non_feedback',
                'reason':'协议替身，仅验证逐观点复核与原文契约','checks':[{'point_index':i,'verdict':'supported','reason':'协议替身',
                    'kind':p['kind'],'sentiment':p['sentiment'],'basis':p['basis'],'context_quote':p['context_quote'],
                    'scenario_quote':p['scenario'],'impact_quote':p['impact'],'expected_outcome_quote':p['expected_outcome']} for i,p in enumerate(x['candidate_points'])]} for x in payload['comments']])
        if schema is OpinionGrouping:
            buckets=defaultdict(list)
            for x in payload['candidates']:
                name='手机编辑后电脑未同步' if x['aspect']=='多端同步' else '文档编辑顺手' if x['aspect']=='文档编辑' else x['name']
                buckets[(x['aspect'],name)].append(x['id'])
            return schema(groups=[{'aspect':a,'name':n,'member_ids':ids} for (a,n),ids in buckets.items()])
        if schema is TopicAdviceBatch:
            return schema(topics=[{'topic_id':x['topic_id'],
                'recommendation':{'text':'建议围绕「'+x['topic']+'」设计可验证的改进方案。','evidence_ids':[x['evidence'][0]['evidence_id']]},
                'validation':{'text':'待验证：复现「'+x['topic']+'」对应场景，观察任务完成情况。','evidence_ids':[x['evidence'][0]['evidence_id']]}} for x in payload['topics']])
        if schema is Narrative:
            positive=next((x for x in payload['evidence'] if '文档' in x['text']),None)
            return schema(findings=[{'text':'评论中存在不同产品维度的具体反馈。','evidence_ids':[payload['evidence'][0]['evidence_id']]}],
                positives=[{'text':'评论直接表达文档操作顺手。','evidence_ids':[positive['evidence_id']]}] if positive else [],recommendations=[],questions=[])
        if schema is SupportCheck:return schema(items=[{'claim_id':x['claim_id'],'verdict':'supported','reason':'协议替身，仅验证管道'} for x in payload['claims']])
        raise AssertionError(schema)
    monkeypatch.setattr(ModelClient,'call',fake)
    return calls

def analyzed_v2(client,model):
    tid=create(client,mode='ai',analysis_version=2);upload(client,tid,dataset())
    assert client.post(f'/api/tasks/{tid}/analyze').status_code==200
    t=wait(client,tid);assert t['status']=='completed',t
    return tid,t

def test_new_tasks_default_to_aspect_engine(client):
    r=client.post('/api/tasks',json={'product':'飞书','source':'import'})
    assert r.status_code==201 and r.json()['config']['analysis_version']==2
    assert 'aspect_extraction_v2' in client.get('/api/schemas').json()

def test_mixed_comment_has_opposite_aspect_sentiments_and_product_scope(client,model):
    tid,t=analyzed_v2(client,model)
    first=client.get(f'/api/tasks/{tid}/comments/xhs:1').json()['effective']
    assert first['sentiment']=='mixed' and [p['sentiment'] for p in first['points']]==['positive','negative']
    compare=client.get(f'/api/tasks/{tid}/comments/xhs:6').json()['effective']
    assert compare['relevance']=='target' and compare['sentiment']=='positive'
    assert any(p['product_scope']=='other_product' for p in compare['points'])
    assert not any(p['aspect']=='消息推送' for p in t['report']['opinion_points'])

def test_specific_issue_aggregation_and_short_reply_counts(client,model):
    tid,t=analyzed_v2(client,model);r=t['report']
    assert r['analysis_version']==2 and r['stats']['raw']==9 and r['stats']['unique']==8 and r['stats']['included']==7
    sync=next(x for x in r['themes'] if x['aspect']=='多端同步')
    assert sync['count']==4 and sync['negative_count']==4 and sync['direct_count']==3 and sync['agreement_count']==1
    assert r['opinion_count']==9  # two-dimensional comments retain separate viewpoints.
    assert len(r['pain_points'])==2 and next(x for x in r['pain_points'] if x['aspect']=='多端同步')['count']==4
    assert not any(x['aspect']=='数据安全' for x in r['pain_points'])  # a question is not a confirmed pain point.
    assert next(x for x in r['needs'] if x['aspect']=='通知管理')['count']==1
    assert next(x for x in r['keywords'] if x['word']=='通知管理')['count']==1  # two opinions, one comment.
    assert r['quality']['repeated_text_group_count']==1 and r['quality']['repeated_text_comment_count']==2
    assert {'xhs:3','xhs:4'}<=set(r['evidence'])

def test_cache_reuse_and_excluded_parent_invalidation(client,model):
    tid,t=analyzed_v2(client,model)
    count=lambda:sum(schema is AspectExtraction for schema,_ in model)
    before=count();client.post(f'/api/tasks/{tid}/analyze');wait(client,tid)
    assert count()==before
    client.patch(f'/api/tasks/{tid}/comments/xhs:3',json={'excluded':True})
    client.post(f'/api/tasks/{tid}/analyze');updated=wait(client,tid)
    assert count()==before+1
    new_batch=next(p for s,p in reversed(model) if s is AspectExtraction)
    assert [x['evidence_id'] for x in new_batch['comments']]==['xhs:2'] and new_batch['comments'][0]['parent_text']==''
    assert 'xhs:3' not in updated['report']['evidence'] and 'xhs:2' not in updated['report']['evidence']
    assert 'xhs:4' in updated['report']['evidence']

def test_manual_opinion_correction_updates_counts_without_model_reextract(client,model,monkeypatch):
    tid,t=analyzed_v2(client,model)
    old=next(x for x in db.comments(tid) if x['evidence_id']=='xhs:1')['analysis']['points']
    changes=copy.deepcopy(old);changes[0]['aspect']='人工修正的文档维度'
    before=sum(s is AspectExtraction for s,_ in model)
    response=client.patch(f'/api/tasks/{tid}/comments/xhs:1',json={'points':changes,'note':'人工核查'})
    assert response.status_code==200 and response.json()['audit']
    assert client.get(f'/api/tasks/{tid}/export/md').status_code==409
    original=ModelClient.call
    async def rename(self,prompt,payload,schema):
        result=await original(self,prompt,payload,schema)
        if schema is OpinionGrouping:
            for group in result.groups:group.aspect='模型尝试覆盖人工归类'
        return result
    monkeypatch.setattr(ModelClient,'call',rename)
    client.post(f'/api/tasks/{tid}/analyze');new=wait(client,tid)['report']
    assert any(x['name']=='人工修正的文档维度' for x in new['aspects'])
    assert sum(s is AspectExtraction for s,_ in model)==before
    changes[0]['quote']='原文没有的句子'
    assert client.patch(f'/api/tasks/{tid}/comments/xhs:1',json={'points':changes}).status_code==422

def test_v2_exports_include_computed_dimensions_and_full_opinions(client,model):
    tid,t=analyzed_v2(client,model)
    for fmt in ('md','html'):
        text=client.get(f'/api/tasks/{tid}/export/{fmt}').text
        assert '用户在关注什么' in text or '观点与关注点' in text
        assert '用户明确表达的痛点' in text and '评论关键词' in text and '4 条' in text
    rows=list(csv.DictReader(io.StringIO(client.get(f'/api/tasks/{tid}/export/csv').text.lstrip('\ufeff'))))
    assert len(rows)==t['stats']['unique'] and sum(r['included']=='True' for r in rows)==t['stats']['included']
    assert all(r['analysis_version']=='2' for r in rows)
    first=next(r for r in rows if r['comment_id']=='1')
    assert '手机编辑后桌面未同步' in first['pain_points'] and '文档编辑' in first['aspects']
    assert 'positive' in first['opinion_points_json'] and 'negative' in first['opinion_points_json']

@pytest.mark.parametrize('field,value',[('quote','伪造引用'),('context_quote','伪造父评论'),('kind','question')])
def test_opinion_evidence_and_question_pain_invariants(field,value):
    p=point('同步','同步延迟','我也是',basis='agreement',context_quote='手机没有同步')
    p[field]=value
    with pytest.raises(ModelFailure):validate_points([p],{'evidence_id':'xhs:1','text':'我也是','parent_text':'手机没有同步'})

def test_duplicate_model_ids_quarantined_and_raw_data_preserved(client,monkeypatch):
    async def fake(self,prompt,payload,schema):
        x=payload['comments'][0]
        row={'evidence_id':x['evidence_id'],'relevance':'target','reason':'测试错误','suspected_ad':False,'points':opinions(x)}
        return AspectExtraction(items=[row,row])
    monkeypatch.setattr(ModelClient,'call',fake)
    tid=create(client,mode='ai',analysis_version=2);upload(client,tid,dataset())
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid)
    assert t['status']=='partial' and t['stats']['raw']==9 and t['report']['stats']['included']==0
    assert all(x['analysis']['opinion_audit']['state']=='needs_review' for x in db.comments(tid))

def test_v2_zero_feedback_is_not_a_product_finding(client,model):
    tid=create(client,mode='ai',analysis_version=2);upload(client,tid,[{'comment_id':'8','text':'蹲'}])
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid)
    assert t['status']=='completed' and t['stats']['included']==0
    assert t['report']['findings']==[] and t['report']['pain_points']==[]
    assert not any(schema is Narrative for schema,_ in model)

def test_topic_counts_are_unique_comments_not_opinion_count():
    rows=[{'unit_id':'1','evidence_id':'c','sentiment':'positive','basis':'direct'},
          {'unit_id':'2','evidence_id':'c','sentiment':'negative','basis':'direct'}]
    stats=summarize_units(rows,10)
    assert stats['count']==1 and stats['point_count']==2 and stats['share']==10
    assert stats['positive_count']==stats['negative_count']==1

def test_successful_batches_survive_later_model_failure(client,model,monkeypatch):
    monkeypatch.setenv('MODEL_BATCH_SIZE','2')
    original=ModelClient.call;attempts=0
    async def fail_second(self,prompt,payload,schema):
        nonlocal attempts
        if schema is AspectExtraction:
            attempts+=1
            if attempts==2:raise ModelFailure('协议替身：第二批连接失败')
        return await original(self,prompt,payload,schema)
    monkeypatch.setattr(ModelClient,'call',fail_second)
    tid=create(client,mode='ai',analysis_version=2);upload(client,tid,dataset())
    client.post(f'/api/tasks/{tid}/analyze');failed=wait(client,tid)
    assert failed['status']=='failed' and failed['analyzed_count']==2
    assert sum(x['analysis'] is not None for x in db.comments(tid))==2
    monkeypatch.setattr(ModelClient,'call',original)
    before=sum(s is AspectExtraction for s,_ in model)
    client.post(f'/api/tasks/{tid}/analyze');done=wait(client,tid)
    assert done['status']=='completed'
    new_batches=[p for s,p in model if s is AspectExtraction][before:]
    assert not any(x['evidence_id'] in ('xhs:1','xhs:2') for batch in new_batches for x in batch['comments'])

def test_unsupported_advice_is_removed_from_topic_and_report(client,model,monkeypatch):
    original=ModelClient.call
    async def reject_advice(self,prompt,payload,schema):
        result=await original(self,prompt,payload,schema)
        if schema is SupportCheck:
            for item in result.items:
                if item.claim_id.startswith('recommendations:'):item.verdict='unsupported'
        return result
    monkeypatch.setattr(ModelClient,'call',reject_advice)
    tid,t=analyzed_v2(client,model);report=t['report']
    assert report['recommendations']==[] and report['pain_points']
    assert all(not t['recommendation_evidence_ids'] for t in report['themes'])

def test_announcement_points_are_not_counted_as_user_satisfaction(client,model,monkeypatch):
    original=ModelClient.call
    async def fake(self,prompt,payload,schema):
        if schema is AspectExtraction:
            return schema(items=[{'evidence_id':x['evidence_id'],'relevance':'target','reason':'信息播报却产生候选观点','suspected_ad':False,
                'points':[point('额度','额度翻倍','额度翻倍','positive')]} for x in payload['comments']])
        if schema is OpinionAuditBatch:
            return schema(items=[{'evidence_id':x['evidence_id'],'content_class':'information_only','reason':'转述公告，没有表达自己的体验或评价','checks':[]} for x in payload['comments']])
        return await original(self,prompt,payload,schema)
    monkeypatch.setattr(ModelClient,'call',fake)
    tid=create(client,product='Codex',mode='ai',analysis_version=2)
    upload(client,tid,[{'comment_id':'news','text':'公告：Codex额度翻倍，月费不变。'}])
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid)
    assert t['status']=='completed' and t['stats']['included']==0 and t['report']['strengths']==[]
    record=db.comments(tid)[0]['analysis']
    assert len(record['candidate_points'])==1 and record['points']==[]
    assert record['opinion_audit']['content_class']=='information_only'

def test_old_initial_extraction_cache_only_needs_new_audit(client,model):
    tid,t=analyzed_v2(client,model)
    before=sum(s is AspectExtraction for s,_ in model);audits=sum(s is OpinionAuditBatch for s,_ in model)
    with db.connect() as c:
        for row in c.execute('SELECT evidence_id,analysis FROM comments WHERE task_id=?',(tid,)).fetchall():
            value=json.loads(row['analysis']);value['_meta'].pop('audit_version');value.pop('candidate_points',None)
            c.execute('UPDATE comments SET analysis=? WHERE task_id=? AND evidence_id=?',(db.dumps(value),tid,row['evidence_id']))
    client.post(f'/api/tasks/{tid}/analyze');done=wait(client,tid)
    assert done['status']=='completed' and done['report']['engine']=='absa-2.1'
    assert sum(s is AspectExtraction for s,_ in model)==before
    assert sum(s is OpinionAuditBatch for s,_ in model)==audits+1

def test_unquoted_business_consequences_are_cleared_and_logged():
    p=point('同步','同步卡顿','同步卡顿')
    item={'evidence_id':'xhs:1','text':'同步卡顿','parent_text':None}
    audit=OpinionAuditBatch(items=[{'evidence_id':'xhs:1','content_class':'feedback','reason':'测试',
        'checks':[{'point_index':0,'verdict':'supported','reason':'测试','basis':'direct','kind':'problem','sentiment':'negative',
            'context_quote':None,'scenario_quote':None,'impact_quote':'造成商业损失','expected_outcome_quote':None}]}])
    result=apply_audit(audit,[item],[{'evidence_id':'xhs:1','relevance':'target','reason':'test','points':[p]}])[0]
    assert result['points'][0]['impact'] is None
    assert result['opinion_audit']['discarded_fields'][0]['field']=='impact'

def test_audit_failure_keeps_initial_extraction_for_retry(client,model,monkeypatch):
    original=ModelClient.call
    async def fail_audit(self,prompt,payload,schema):
        if schema is OpinionAuditBatch:raise ModelFailure('协议替身：复核连接失败')
        return await original(self,prompt,payload,schema)
    monkeypatch.setattr(ModelClient,'call',fail_audit)
    tid=create(client,mode='ai',analysis_version=2);upload(client,tid,dataset())
    client.post(f'/api/tasks/{tid}/analyze');assert wait(client,tid)['status']=='failed'
    assert all(x['analysis']['opinion_audit']['state']=='pending' for x in db.comments(tid))
    before=sum(s is AspectExtraction for s,_ in model)
    monkeypatch.setattr(ModelClient,'call',original)
    client.post(f'/api/tasks/{tid}/analyze');assert wait(client,tid)['status']=='completed'
    assert sum(s is AspectExtraction for s,_ in model)==before

def test_preliminary_question_does_not_keep_a_fabricated_pain():
    p=point('数据安全','文件安全问题','会不会丢数据','neutral',kind='question',pain_point='数据丢失')
    row={'evidence_id':'xhs:1','relevance':'target','reason':'测试','suspected_ad':False,'points':[p]}
    value=validate_extraction(AspectExtraction(items=[row]),[{'evidence_id':'xhs:1','text':'飞书会不会丢数据？','parent_text':None}],{'product':'飞书'})[0]
    assert value['points'][0]['pain_point'] is None and value['extraction_adjustments']

@pytest.mark.parametrize('invalid',['counts','unknown_id'])
def test_bad_generated_text_is_omitted_after_bounded_retry(client,model,monkeypatch,invalid):
    original=ModelClient.call
    async def bad(self,prompt,payload,schema):
        result=await original(self,prompt,payload,schema)
        if schema is Narrative:
            if invalid=='counts':result.findings[0].text='90% 的用户遇到问题'
            else:result.findings[0].evidence_ids=['xhs:invented']
        return result
    monkeypatch.setattr(ModelClient,'call',bad)
    tid=create(client,mode='ai',analysis_version=2);upload(client,tid,dataset())
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid)
    assert t['status']=='partial' and t['failure_count']==0 and t['report']['generation_warnings']
    assert t['report']['stats']['included']==7 and t['report']['findings']==[]
    assert len([s for s,_ in model if s is Narrative])==2
    assert t['report']['pain_points'] and 'xhs:invented' not in t['report']['evidence']

def test_unused_core_advice_does_not_block_verified_report(client,model,monkeypatch):
    original=ModelClient.call
    async def extra(self,prompt,payload,schema):
        result=await original(self,prompt,payload,schema)
        if schema is Narrative:return Narrative(findings=result.findings,positives=result.positives,recommendations=[{'text':'90% 的用户会满意','evidence_ids':['xhs:fake']}],questions=[])
        return result
    # Return a typed value while simulating extra model sections that V2 never uses.
    async def typed(self,prompt,payload,schema):
        result=await extra(self,prompt,payload,schema)
        return Narrative.model_validate(result.model_dump()) if schema is Narrative else result
    monkeypatch.setattr(ModelClient,'call',typed)
    tid,t=analyzed_v2(client,model)
    assert not t['report']['generation_warnings']
    assert all('90%' not in x['text'] for x in t['report']['recommendations'])

def test_offline_recount_filters_changed_claims_and_never_calls_model(client,model,monkeypatch):
    tid,t=analyzed_v2(client,model);old=copy.deepcopy(t['report'])
    assert client.get(f'/api/tasks/{tid}').json()['can_recount_report']
    changes=copy.deepcopy(db.comments(tid)[0]['effective']['points'])
    changes[0]['sentiment']='neutral'
    client.patch(f'/api/tasks/{tid}/comments/xhs:1',json={'points':changes,'note':'测试逐观点修正'})
    client.patch(f'/api/tasks/{tid}/comments/xhs:4',json={'excluded':True})
    assert db.latest_report_snapshot(tid)['opinion_count']==old['opinion_count']
    async def forbidden(*args,**kwargs):raise AssertionError('Offline recount called model')
    monkeypatch.setattr(ModelClient,'call',forbidden)
    db.update(tid,error='模型服务返回 HTTP 402')
    response=client.post(f'/api/tasks/{tid}/report/recount');assert response.status_code==200
    final=wait(client,tid);r=final['report']
    assert final['status']=='partial' and final['error']=='模型服务返回 HTTP 402'
    assert r['generation_mode']=='cached_narrative_recount' and not r['support_check']['new_model_check']
    assert r['stats']['included']==6 and 'xhs:4' not in r['evidence']
    for section in ('findings','positives','recommendations','questions'):
        assert all(not {'xhs:1','xhs:4'}.intersection(c['evidence_ids']) for c in r[section])
    assert any(c for section in ('recommendations','questions') for c in r[section])
    assert r['support_check']['removed_claim_count']>0
    assert next(u for u in r['opinion_points'] if u['unit_id']=='xhs:1#p1')['sentiment']=='neutral'
    rows=list(csv.DictReader(io.StringIO(client.get(f'/api/tasks/{tid}/export/csv').text.lstrip('\ufeff'))))
    assert len(rows)==r['stats']['unique'] and sum(x['included']=='True' for x in rows)==6
    assert db.latest_report_snapshot(tid)['generation_mode']=='cached_narrative_recount'

def test_offline_recount_drops_dependent_agreement_and_keeps_parent_raw(client,model,monkeypatch):
    tid,t=analyzed_v2(client,model)
    client.patch(f'/api/tasks/{tid}/comments/xhs:3',json={'excluded':True})
    async def forbidden(*args,**kwargs):raise AssertionError('must be offline')
    monkeypatch.setattr(ModelClient,'call',forbidden)
    client.post(f'/api/tasks/{tid}/report/recount');r=wait(client,tid)['report']
    assert r['stats']['included']==5 and {'xhs:2','xhs:3'}.isdisjoint(r['evidence'])
    child=client.get(f'/api/tasks/{tid}/comments/xhs:2').json()
    assert child['effective']['relevance']=='uncertain' and child['analysis']['points'][0]['product_scope']=='target'
    assert child['parent_text']=='飞书手机改完电脑看不到更新，最后把内容发微信重新粘贴。'
    assert child['effective']['points'][0]['product_scope']=='uncertain'

def test_recount_requires_snapshot_and_completed_opinions(client,model):
    tid=create(client,mode='ai',analysis_version=2);upload(client,tid,dataset())
    assert client.post(f'/api/tasks/{tid}/report/recount').status_code==422
    client.post(f'/api/tasks/{tid}/analyze');wait(client,tid)
    upload(client,tid,[{'comment_id':'new','text':'飞书不错'}])
    assert db.latest_report_snapshot(tid)
    assert client.post(f'/api/tasks/{tid}/report/recount').status_code==422

@pytest.mark.parametrize('invalid',['missing','duplicate','one_based','empty_invented','unknown_comment','bad_context'])
def test_audit_protocol_failure_isolated_without_stopping_later_comments(client,model,monkeypatch,invalid):
    original=ModelClient.call
    async def wrong(self,prompt,payload,schema):
        result=await original(self,prompt,payload,schema)
        if schema is OpinionAuditBatch:
            for item in payload['comments']:
                assert item['expected_point_indices']==list(range(len(item['candidate_points'])))
                assert [p['point_index'] for p in item['candidate_points']]==item['expected_point_indices']
            row=next(x for x in result.items if x.evidence_id==('xhs:8' if invalid=='empty_invented' else 'xhs:1'))
            if invalid=='missing':row.checks.pop()
            elif invalid=='duplicate':row.checks.append(copy.deepcopy(row.checks[0]))
            elif invalid=='one_based':
                for check in row.checks:check.point_index+=1
            elif invalid=='empty_invented':
                from insight.schemas import OpinionCheck
                row.content_class='feedback';row.checks=[OpinionCheck(point_index=0,verdict='supported',reason='编造不存在观点',kind='problem',sentiment='negative',basis='direct')]
            elif invalid=='unknown_comment':row.evidence_id='xhs:invented'
            elif invalid=='bad_context':row.checks[0].basis='agreement';row.checks[0].context_quote='不存在的上下文'
        return result
    monkeypatch.setattr(ModelClient,'call',wrong)
    tid=create(client,mode='ai',analysis_version=2);upload(client,tid,dataset())
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid);r=t['report']
    assert t['status']=='partial' and t['analyzed_count']==8 and r['quality']['audit_needs_review_count']==1
    bad='xhs:8' if invalid=='empty_invented' else 'xhs:1'
    assert bad not in r['evidence'] and {'xhs:3','xhs:4'}<=set(r['evidence'])
    item=client.get(f'/api/tasks/{tid}/comments/{bad}').json()
    assert item['analysis']['opinion_audit']['state']=='needs_review' and item['analysis']['_meta']['audit_version'] is None
    assert item['effective']['relevance']=='uncertain' and not item['effective']['has_feedback']
    assert len([s for s,_ in model if s is OpinionAuditBatch])==3  # Two batch attempts, then one failed-record recovery.
    assert any('待核查' in x for x in r['generation_warnings'])
    # Retry only the isolated comment, preserving all complete extraction/audit caches.
    before=sum(s is AspectExtraction for s,_ in model);audit_before=sum(s is OpinionAuditBatch for s,_ in model)
    monkeypatch.setattr(ModelClient,'call',original)
    client.post(f'/api/tasks/{tid}/analyze');done=wait(client,tid)
    assert done['status']=='completed' and done['stats']['included']==7
    assert sum(s is AspectExtraction for s,_ in model)==before
    batches=[p for s,p in model if s is OpinionAuditBatch][audit_before:]
    assert len(batches)==1 and [x['evidence_id'] for x in batches[0]['comments']]==[bad]

def test_bad_audit_in_first_batch_does_not_skip_remaining_batches(client,model,monkeypatch):
    monkeypatch.setenv('MODEL_BATCH_SIZE','2')
    original=ModelClient.call
    async def wrong_first(self,prompt,payload,schema):
        result=await original(self,prompt,payload,schema)
        if schema is OpinionAuditBatch:
            row=next((x for x in result.items if x.evidence_id=='xhs:1'),None)
            if row:row.checks=[]
        return result
    monkeypatch.setattr(ModelClient,'call',wrong_first)
    tid=create(client,mode='ai',analysis_version=2);upload(client,tid,dataset())
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid)
    assert t['status']=='partial' and t['analyzed_count']==8
    assert t['report']['quality']['audit_needs_review_ids']==['xhs:1']
    assert {'xhs:3','xhs:4','xhs:5','xhs:6','xhs:7'}<=set(t['report']['evidence'])
    assert all(x['analysis']['opinion_audit']['state'] in ('checked','needs_review') for x in db.comments(tid))

@pytest.mark.parametrize('stage',[AspectExtraction,OpinionAuditBatch,OpinionGrouping,TopicAdviceBatch,Narrative,SupportCheck])
def test_schema_failure_from_model_client_does_not_destroy_report(client,model,monkeypatch,stage):
    original=ModelClient.call
    async def invalid(self,prompt,payload,schema):
        result=await original(self,prompt,payload,schema)
        if schema is stage:
            raw=result.model_dump()
            if schema is AspectExtraction:raw['items'][0]['points'][0]['sentiment']='invalid-enum'
            elif schema is OpinionAuditBatch:raw['items'][0]['checks'][0]['kind']='invalid-enum'
            raise ModelOutputFailure(schema.__name__,['items.0 [literal_error]'],raw)
        return result
    monkeypatch.setattr(ModelClient,'call',invalid)
    tid=create(client,mode='ai',analysis_version=2);upload(client,tid,dataset())
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid);r=t['report']
    assert t['status']=='partial' and t['analyzed_count']==8 and r and r['generation_warnings']
    if stage in (AspectExtraction,OpinionAuditBatch):
        assert r['quality']['audit_needs_review_ids']==['xhs:1']
        assert 'xhs:1' not in r['evidence'] and r['stats']['included']==6
        assert {'xhs:3','xhs:4'}<=set(r['evidence'])
    else:assert r['stats']['included']==7 and r['pain_points']
    if stage is SupportCheck:assert not any(r[s] for s in ('findings','positives','recommendations','questions'))
    for fmt in ('md','html','csv'):assert client.get(f'/api/tasks/{tid}/export/{fmt}').status_code==200

def test_unparseable_batch_is_quarantined_and_later_batches_continue(client,model,monkeypatch):
    original=ModelClient.call;monkeypatch.setenv('MODEL_BATCH_SIZE','2')
    async def invalid(self,prompt,payload,schema):
        if schema is AspectExtraction and any(x['evidence_id']=='xhs:1' for x in payload['comments']):
            raise ModelOutputFailure(schema.__name__,['response [invalid_json]'])
        return await original(self,prompt,payload,schema)
    monkeypatch.setattr(ModelClient,'call',invalid)
    tid=create(client,mode='ai',analysis_version=2);upload(client,tid,dataset())
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid)
    assert t['status']=='partial' and t['analyzed_count']==8
    assert t['report']['quality']['audit_needs_review_ids']==['xhs:1','xhs:2']
    assert {'xhs:3','xhs:4','xhs:5'}<=set(t['report']['evidence'])
    assert all(not x['analysis']['_meta']['extraction_valid'] for x in db.comments(tid) if x['evidence_id'] in ('xhs:1','xhs:2'))

def test_failed_audit_enum_recovers_individually_without_rechecking_good_rows(client,model,monkeypatch):
    original=ModelClient.call
    async def invalid_batch(self,prompt,payload,schema):
        result=await original(self,prompt,payload,schema)
        if schema is OpinionAuditBatch and len(payload['comments'])>1:
            raw=result.model_dump();raw['items'][0]['content_class']='negative_experience'
            raise ModelOutputFailure(schema.__name__,['items.0.content_class [literal_error]'],raw)
        return result
    monkeypatch.setattr(ModelClient,'call',invalid_batch)
    tid=create(client,mode='ai',analysis_version=2);upload(client,tid,dataset())
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid)
    assert t['status']=='completed' and t['report']['quality']['audit_needs_review_count']==0
    batches=[p for s,p in model if s is OpinionAuditBatch]
    assert len(batches)==2 and len(batches[1]['comments'])==1
    assert batches[1]['comments'][0]['evidence_id']=='xhs:1'
    assert 'xhs:1' in t['report']['evidence']

def test_transport_failure_during_single_recovery_keeps_successful_audits(client,model,monkeypatch):
    original=ModelClient.call
    async def broken(self,prompt,payload,schema):
        if schema is OpinionAuditBatch and len(payload['comments'])==1:raise ModelFailure('模型连接失败或超时')
        result=await original(self,prompt,payload,schema)
        if schema is OpinionAuditBatch:
            raw=result.model_dump();raw['items'][0]['content_class']='invalid'
            raise ModelOutputFailure(schema.__name__,['items.0.content_class [literal_error]'],raw)
        return result
    monkeypatch.setattr(ModelClient,'call',broken)
    tid=create(client,mode='ai',analysis_version=2);upload(client,tid,dataset())
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid)
    assert t['status']=='failed' and '连接' in t['error']
    rows={x['evidence_id']:x for x in db.comments(tid)}
    assert rows['xhs:1']['analysis']['opinion_audit']['state']=='needs_review'
    assert all(x['analysis']['opinion_audit']['state']=='checked' for eid,x in rows.items() if eid!='xhs:1')

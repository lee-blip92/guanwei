import asyncio
import csv
import io
import json
import time
from pathlib import Path
import pytest
from insight import db, config
from insight.ingest import ingest, read_payload, ImportErrorDetail
from insight.analysis import rule_extract, validate_extraction, validate_narrative, ModelFailure, ModelClient
from insight.schemas import Extraction, Narrative

def create(client, source='import', mode='rules', **extra):
    # Preserve the previous pipeline's regression contract; V2 has dedicated tests.
    response = client.post('/api/tasks', json={'product':'飞书','source':source,'analysis_mode':mode,'analysis_version':1,'insight_version':2,'semantic_version':0,**extra})
    assert response.status_code == 201, response.text
    return response.json()['id']

def upload(client, tid, value, name='sample.json'):
    r = client.post(f'/api/tasks/{tid}/import', content=json.dumps(value,ensure_ascii=False).encode(),headers={'X-Filename':name})
    assert r.status_code == 200, r.text
    return r.json()

def wait(client, tid):
    deadline = time.monotonic()+10
    while time.monotonic()<deadline:
        t = client.get('/api/tasks/'+tid).json()
        if t['status'] not in db.ACTIVE:
            return t
        time.sleep(.01)
    raise AssertionError('background job did not finish')

def simple_payload():
    return {'notes':[{'note_id':'n1','title':'飞书使用体验','desc':'讨论飞书文档'}], 'comments':[
        {'comment_id':'c1','note_id':'n1','content':'飞书文档打开经常卡顿','note_url':'https://www.xiaohongshu.com/explore/n1'},
        {'comment_id':'c2','note_id':'n1','content':'我也是','parent_comment_id':'c1','note_url':'https://www.xiaohongshu.com/explore/n1'},
        {'comment_id':'c3','note_id':'n1','content':'飞书协作很方便'},
        {'comment_id':'c1','note_id':'n1','content':'飞书文档打开经常卡顿','source_keyword':'飞书问题'}]}

def analyzed(client):
    tid = create(client)
    upload(client,tid,simple_payload())
    assert client.post(f'/api/tasks/{tid}/analyze').status_code==200
    t=wait(client,tid)
    assert t['status']=='completed',t
    return tid,t

def test_comment_id_dedup_same_url_and_short_reply(client):
    tid,t = analyzed(client)
    assert t['stats']['raw']==4 and t['stats']['unique']==3 and t['stats']['duplicates']==1
    comments=client.get(f'/api/tasks/{tid}/comments').json()['items']
    short=next(x for x in comments if x['comment_id']=='c2')
    assert short['effective']['relevance']=='target'
    assert short['effective']['sentiment']=='negative'
    assert '稳定性与性能' in short['effective']['themes']
    assert short['parent_text']=='飞书文档打开经常卡顿'

def test_missing_ids_do_not_collapse(client):
    tid=create(client)
    t=upload(client,tid,[{'note_id':'n','text':'一样'},{'note_id':'n','text':'一样'}])
    assert t['stats']['unique']==2
    assert all(x['missing_id'] for x in db.comments(tid))

def test_image_only_null_text_retained(client):
    tid=create(client)
    t=upload(client,tid,[{'comment_id':'image','content':None,'pictures':'image.jpg'}])
    assert t['stats']['raw']==1 and db.comments(tid)[0]['text']==''

def test_manual_exclusion_invalidates_and_removes_evidence(client):
    tid,t=analyzed(client)
    response=client.patch(f'/api/tasks/{tid}/comments/xhs:c1',json={'excluded':True,'note':'不是实际体验'})
    assert response.status_code==200
    assert client.get(f'/api/tasks/{tid}/export/md').status_code==409
    client.post(f'/api/tasks/{tid}/analyze')
    updated=wait(client,tid)
    assert updated['version']==2
    assert 'xhs:c1' not in updated['report']['evidence']
    assert updated['report']['evidence']['xhs:c2']['parent_text'] is None
    assert all('xhs:c1' not in t['evidence_ids'] for t in updated['report']['themes'])
    assert updated['stats']['raw']==4  # reanalysis must not recrawl or reimport
    assert db.comments(tid)[0]['overrides']['excluded'] is True

def test_restore_ad_and_classification_survive_reanalysis(client):
    tid=create(client)
    upload(client,tid,[{'comment_id':'ad','text':'飞书会员代充，加微优惠'}])
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid)
    assert t['stats']['included']==0
    client.patch(f'/api/tasks/{tid}/comments/xhs:ad',json={'suspected_ad':False,'relevance':'target','themes':['人工确认主题'],'sentiment':'neutral','excluded':False})
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid)
    assert t['stats']['included']==1
    assert t['report']['themes'][0]['name']=='人工确认主题'

def test_comparison_attribution_and_uncertainty(client):
    tid=create(client)
    upload(client,tid,{'notes':[{'note_id':'n','title':'飞书和钉钉对比'}],'comments':[{'comment_id':'a','note_id':'n','text':'钉钉的通知太吵了'},{'comment_id':'b','note_id':'n','text':'确实不太好用'},{'comment_id':'c','note_id':'n','text':'我也是','parent_comment_id':'a'}]})
    client.post(f'/api/tasks/{tid}/analyze');wait(client,tid)
    values={x['comment_id']:x for x in db.comments(tid)}
    assert values['a']['effective']['relevance']=='other_product'
    assert values['b']['effective']['relevance']=='uncertain'
    assert values['c']['effective']['relevance']!='target'

def test_evidence_and_csv_counts_match_page(client):
    tid,t=analyzed(client)
    r=t['report'];allowed=set(r['evidence'])
    for category in ('findings','positives','recommendations','questions'):
        assert all(set(c['evidence_ids'])<=allowed for c in r[category])
    csv_response=client.get(f'/api/tasks/{tid}/export/csv')
    rows=list(csv.DictReader(io.StringIO(csv_response.text.lstrip('\ufeff'))))
    assert len(rows)==t['stats']['unique']
    assert sum(x['included']=='True' for x in rows)==t['stats']['included']
    md=client.get(f'/api/tasks/{tid}/export/md').text
    assert f'最终纳入 {t["stats"]["included"]}' in md
    html=client.get(f'/api/tasks/{tid}/export/html').text
    assert 'window.print()' in html and '最终纳入' in html

def test_zero_effective_feedback_not_fake_findings(client):
    tid=create(client);upload(client,tid,[{'comment_id':'c','text':'钉钉通知太吵'}])
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid)
    assert t['status']=='completed' and t['stats']['included']==0
    assert t['report']['findings']==[] and t['report']['themes']==[]

def test_import_transaction_rolls_back(client):
    tid=create(client)
    r=client.post(f'/api/tasks/{tid}/import',json=[{'comment_id':'ok','text':'飞书好用'},{'comment_id':'bad','text':12}])
    assert r.status_code==422
    assert db.stats(tid)['raw']==0 and db.stats(tid)['unique']==0

def test_jsonl_import_bad_line_and_utf8(client):
    tid=create(client)
    r=client.post(f'/api/tasks/{tid}/import',content='{"comment_id":"1","text":"我也是"}\n{"text":"飞书好用"}'.encode(),headers={'X-Filename':'test.jsonl'})
    assert r.status_code==200 and r.json()['stats']['unique']==2
    bad=client.post(f'/api/tasks/{tid}/import',content=b'{}\nBAD',headers={'X-Filename':'test.jsonl'})
    assert bad.status_code==422 and '2' in bad.json()['detail']

def test_demo_never_mixed_with_real_import(client):
    tid=create(client,source='demo');t=wait(client,tid)
    assert t['config']['source']=='demo' and '演示' in t['source_name']
    assert '人工虚构' in client.get(f'/api/tasks/{tid}/export/md').text
    assert client.post(f'/api/tasks/{tid}/import',json=[]).status_code==409

def test_demo_file_cannot_masquerade_as_real_import(client):
    tid=create(client)
    payload=json.loads((config.ROOT/'samples/demo-feishu.json').read_text(encoding='utf-8'))
    response=client.post(f'/api/tasks/{tid}/import',json=payload)
    assert response.status_code==422 and '演示数据' in response.json()['detail']
    assert db.stats(tid)['raw']==0

def test_unknown_and_duplicate_evidence_rejected():
    base={'evidence_id':'xhs:1','relevance':'target','reason':'明确提及','suspected_ad':False,'sentiment':'neutral','themes':[],'scenario':'未知','feedback':'原文','quote':'原文'}
    batch=[{'evidence_id':'xhs:1','text':'原文'}]
    assert validate_extraction(Extraction(items=[base]),batch)
    for bad in ([{**base,'evidence_id':'fake'}],[base,base],[{**base,'quote':'编造原文'}]):
        with pytest.raises(ModelFailure):validate_extraction(Extraction(items=bad),batch)
    n=Narrative(findings=[{'text':'观察','evidence_ids':['fake']}],positives=[],recommendations=[],questions=[])
    with pytest.raises(ModelFailure):validate_narrative(n,{'xhs:1'})

def test_restart_ends_all_lingering_states(client):
    ids=[]
    for state in db.ACTIVE:
        tid=create(client);db.update(tid,status=state);ids.append(tid)
    db.recover()
    assert all(db.task(tid)['status']=='failed' and '重启' in db.task(tid)['error'] for tid in ids)

def test_model_failure_preserves_data_and_fails_task(client,monkeypatch):
    async def fail(*args,**kwargs):raise ModelFailure('模型调用失败（HTTP 401）')
    monkeypatch.setattr(ModelClient,'call',fail)
    tid=create(client,mode='ai');upload(client,tid,simple_payload())
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid)
    assert t['status']=='failed' and '401' in t['error']
    assert t['stats']['unique']==3 and t['report'] is None

def test_fake_model_ai_pipeline_structured_and_evidence(client,monkeypatch):
    # Protocol/validation test only; not an actual model quality evaluation.
    async def fake(self,prompt,payload,schema):
        if schema is Extraction:
            values=[{'evidence_id':x['evidence_id'],'relevance':'target','reason':'测试响应','suspected_ad':False,'sentiment':'neutral','themes':['稳定性'],'scenario':'未知','feedback':x['text'],'quote':x['text']} for x in payload['comments']]
            return Extraction(items=values)
        from insight.schemas import Grouping
        if schema is Grouping:return Grouping(groups=[{'name':'稳定性','member_labels':payload['labels']}])
        from insight.schemas import SupportCheck
        if schema is SupportCheck:return SupportCheck(items=[{'claim_id':x['claim_id'],'verdict':'supported','reason':'测试支持'} for x in payload['claims']])
        return Narrative(findings=[{'text':'测试观察','evidence_ids':[payload['evidence'][0]['evidence_id']]}],positives=[],recommendations=[],questions=[])
    monkeypatch.setattr(ModelClient,'call',fake)
    tid=create(client,mode='ai');upload(client,tid,simple_payload())
    client.post(f'/api/tasks/{tid}/analyze');t=wait(client,tid)
    assert t['status']=='completed' and len(t['report']['themes'])==1
    assert t['report']['themes'][0]['count']==3

def test_wait_login_resume_cancel_and_partial_failure(client,monkeypatch):
    import insight.jobs as jobs
    async def collector(tid,cfg,manager):
        db.update(tid,status='waiting_login',stage='等待人工登录',current_platform='xhs')
        folder=config.DATA/'tasks'/tid/'xhs';folder.mkdir(parents=True,exist_ok=True)
        manager.processes[tid]=object()
        try:
            while not (folder/'resume').exists():await asyncio.sleep(.01)
            db.update(tid,status='collecting')
            ingest(tid,[{'comment_id':'live','text':'飞书很好用'}])
            return 1
        finally:manager.processes.pop(tid,None)
    monkeypatch.setattr(jobs,'collect',collector)
    tid=create(client,source='crawl')
    deadline=time.monotonic()+2
    while client.get('/api/tasks/'+tid).json()['status']!='waiting_login' and time.monotonic()<deadline:time.sleep(.01)
    assert client.post(f'/api/tasks/{tid}/resume').status_code==200
    t=wait(client,tid)
    assert t['status']=='partial' and t['failure_count']==1 and t['stats']['included']==1
    second=create(client,source='crawl')
    assert client.post(f'/api/tasks/{second}/cancel').status_code==200
    assert wait(client,second)['status']=='cancelled'

def test_excluded_record_never_sent_to_model(client,monkeypatch):
    tid,t=analyzed(client)
    client.patch(f'/api/tasks/{tid}/comments/xhs:c1',json={'excluded':True})
    seen=[]
    async def fake(self,prompt,payload,schema):
        if schema is Extraction:
            seen.extend(payload['comments'])
            return Extraction(items=[{'evidence_id':x['evidence_id'],'relevance':'uncertain','reason':'测试','suspected_ad':False,'sentiment':'unknown','themes':[],'scenario':'未知','feedback':'','quote':x['text']} for x in payload['comments']])
        raise AssertionError('empty sample should not synthesize')
    monkeypatch.setattr(ModelClient,'call',fake)
    client.post(f'/api/tasks/{tid}/analyze',json={'analysis_mode':'ai'});t=wait(client,tid)
    assert t['status']=='completed'
    assert all(x['evidence_id']!='xhs:c1' for x in seen)
    assert next(x for x in seen if x['evidence_id']=='xhs:c2')['parent_text']==''

def test_model_key_never_returned_and_origin_guard(client,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','SENSITIVE_TEST_KEY')
    r=client.get('/api/settings')
    assert r.json()['key_configured'] and 'SENSITIVE_TEST_KEY' not in r.text
    assert client.post('/api/tasks',json={'product':'飞书'},headers={'Origin':'https://evil.example'}).status_code==403
    assert client.get('/api/settings',headers={'Host':'evil.example'}).status_code==403

def test_export_escapes_xss_and_csv_formula(client):
    tid=create(client);upload(client,tid,[{'comment_id':'x','text':'=飞书<script>alert(1)</script>'}])
    client.post(f'/api/tasks/{tid}/analyze');wait(client,tid)
    text=client.get(f'/api/tasks/{tid}/export/html').text
    assert '<script>alert(1)</script>' not in text and '&lt;script&gt;' in text
    rows=list(csv.DictReader(io.StringIO(client.get(f'/api/tasks/{tid}/export/csv').text.lstrip('\ufeff'))))
    assert rows[0]['text'].startswith("'=")

def test_ambiguous_product_and_no_fake_date_filter(client):
    r=client.post('/api/tasks',json={'product':'豆包'})
    assert r.status_code==422
    assert client.post('/api/tasks',json={'product':'豆包','description':'字节跳动 AI 助手'}).status_code==201
    assert client.post('/api/tasks',json={'product':'飞书','start_date':'2026-01-01'}).status_code==422

def test_keyword_generation_balanced(client):
    value=client.post('/api/keywords',json={'product':'飞书'}).json()
    assert '飞书 好用' in value['keywords'] and '飞书 使用体验' in value['keywords'] and '飞书 问题反馈' in value['keywords']

def test_filter_and_review_audit(client):
    tid,t=analyzed(client)
    assert client.get(f'/api/tasks/{tid}/comments?q=我也是').json()['total']==1
    client.patch(f'/api/tasks/{tid}/comments/xhs:c2',json={'sentiment':'positive','note':'已核查'})
    rows=client.get(f'/api/tasks/{tid}/comments?status=reviewed&sentiment=positive').json()
    assert rows['total']==1 and rows['items'][0]['audit'][0]['change']['note']=='已核查'

def test_report_only_reuses_labels_and_requires_unmodified_result(client,monkeypatch):
    tid,t=analyzed(client)
    import insight.jobs as jobs
    async def must_not_extract(*args,**kwargs):raise AssertionError('report-only must not call extraction')
    monkeypatch.setattr(jobs,'extract_all',must_not_extract)
    assert client.post(f'/api/tasks/{tid}/report/regenerate').status_code==200
    updated=wait(client,tid)
    assert updated['status']=='completed' and updated['version']==t['version']+1
    assert updated['stats']==t['stats']
    client.patch(f'/api/tasks/{tid}/comments/xhs:c1',json={'excluded':True})
    assert client.post(f'/api/tasks/{tid}/report/regenerate').status_code==409

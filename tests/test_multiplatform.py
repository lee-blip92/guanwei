import asyncio
import csv
import io
import json
import sqlite3
import time
import pytest
from insight import config,db,crawler
from insight.ingest import ingest,read_payload
from insight.platforms import PLATFORMS,platform_name
from test_pipeline import create,upload,wait

def native_export(platform):
    title='飞书文档体验 · '+platform_name(platform)
    note={'xhs':{'note_id':'1','title':title},'dy':{'aweme_id':'1','title':title},
          'ks':{'video_id':'1','title':title},'bili':{'video_id':'1','title':title},
          'wb':{'note_id':'1','content':title},'tieba':{'note_id':'1','title':title},
          'zhihu':{'content_id':'1','content_type':'answer','title':title}}[platform]
    relation={'note_id':'1'} if platform in ('xhs','wb','tieba') else {'aweme_id':'1'} if platform=='dy' else {'content_id':'1','content_type':'answer'} if platform=='zhihu' else {'video_id':'1'}
    parent={'comment_id':'101',**relation,'content':'飞书文档经常卡顿 · '+platform,'like_count':2}
    reply={'comment_id':'102',**relation,'content':'我也是','parent_comment_id':'101'}
    third={'comment_id':'103',**relation,'content':'飞书协作很好用'}
    duplicate={**parent,'source_keyword':'飞书 问题反馈'}
    return {'platform':platform,'notes':[note],'comments':[parent,reply,third,duplicate]}

def test_all_platform_import_analyze_filter_export_and_review(client):
    selected=list(PLATFORMS)
    tid=create(client,platforms=selected)
    for p in selected:upload(client,tid,native_export(p),name=p+'.json')
    assert client.post(f'/api/tasks/{tid}/analyze').status_code==200
    task=wait(client,tid);stats=task['stats'];report=task['report']
    assert task['status']=='completed' and stats['raw']==28 and stats['unique']==21 and stats['notes']==7 and stats['included']==21
    for p in selected:
        assert stats['platforms'][p]['raw']==4 and stats['platforms'][p]['unique']==3
        rows=client.get(f'/api/tasks/{tid}/comments?platform={p}').json()
        assert rows['total']==3 and {x['platform'] for x in rows['items']}=={p}
        reply=next(x for x in rows['items'] if x['comment_id']=='102')
        assert reply['parent_text']=='飞书文档经常卡顿 · '+p
        assert reply['parent_evidence_id']==p+':101'
        assert reply['note_title']=='飞书文档体验 · '+platform_name(p)
        assert reply['effective']['relevance']=='target'
    assert set(report['evidence'])=={p+':'+c for p in selected for c in ('101','102','103')}
    for eid,row in report['evidence'].items():assert row['platform']==eid.split(':')[0]
    csv_rows=list(csv.DictReader(io.StringIO(client.get(f'/api/tasks/{tid}/export/csv').text.lstrip('\ufeff'))))
    assert len(csv_rows)==stats['unique']
    assert sum(x['included']=='True' for x in csv_rows)==stats['included']
    for fmt in ('md','html'):
        text=client.get(f'/api/tasks/{tid}/export/{fmt}').text
        assert '平台样本分布' in text and '每个平台' in text
        assert all(platform_name(p) in text for p in selected)
    assert client.patch(f'/api/tasks/{tid}/comments/dy:101',json={'excluded':True}).status_code==200
    client.post(f'/api/tasks/{tid}/analyze');new=wait(client,tid)['report']
    assert 'dy:101' not in new['evidence'] and 'xhs:101' in new['evidence']
    assert new['evidence'].get('dy:102',{}).get('parent_text') is None
    assert new['evidence']['xhs:102']['parent_text'].endswith('xhs')

def test_platform_selection_aliases_and_capabilities(client):
    catalog=client.get('/api/platforms').json()
    assert [p['id'] for p in catalog]==list(PLATFORMS)
    assert '嵌入回复' in next(p for p in catalog if p['id']=='wb')['reply_note']
    for ids in ([],['unknown']):
        assert client.post('/api/tasks',json={'product':'飞书','platforms':ids}).status_code==422
    tid=create(client,platforms=['douyin','dy','抖音','bilibili'])
    assert db.task(tid)['config']['platforms']==['dy','bili']
    r=client.post('/api/tasks',json={'product':'飞书','source':'crawl','platforms':['dy','ks'],'sort':'time_descending'})
    assert r.status_code==422 and '排序' in r.text
    r=client.post('/api/tasks',json={'product':'飞书','source':'demo','platforms':['dy']})
    assert r.status_code==422

def test_ambiguous_and_unselected_import_platforms_are_rejected_atomically(client):
    tid=create(client,platforms=['xhs','dy'])
    for payload in ([{'comment_id':'x','text':'飞书好用'}],{'platform':'ks','comments':[{'comment_id':'x','text':'飞书好用'}]}):
        response=client.post(f'/api/tasks/{tid}/import',content=json.dumps(payload).encode())
        assert response.status_code==422
        assert db.stats(tid)['raw']==0
    # A later invalid record must roll back an earlier valid one as well.
    payload=[{'platform':'dy','comment_id':'ok','text':'飞书好用'},{'platform':'ks','comment_id':'bad','text':'飞书好用'}]
    assert client.post(f'/api/tasks/{tid}/import',content=json.dumps(payload).encode()).status_code==422
    assert db.stats(tid)['raw']==0

def test_jsonl_native_rows_and_weibo_excel_without_title(client):
    tid=create(client,platforms=['dy','wb'])
    data='\n'.join(json.dumps(r,ensure_ascii=False) for r in [
        {'kind':'note','platform':'dy','data':{'aweme_id':'1','desc':'飞书使用体验'}},
        {'platform':'dy','cid':'201','aweme_id':'1','text':'飞书好用'}])
    payload=read_payload(data.encode(),'native.jsonl');ingest(tid,payload)
    assert db.comments(tid)[0]['note_title']=='飞书使用体验'
    ingest(tid,{'kind':'note','platform':'wb','data':{'note_id':'other','content':'微博笔记','comments_count':'2','shared_count':'0'}})
    ingest(tid,[{'platform':'wb','note_id':'json','content':'原生微博笔记','comments_count':'2','shared_count':'0'}])
    assert db.stats(tid)['unique']==1 and db.stats(tid)['notes']==3
    from openpyxl import Workbook
    book=Workbook();notes=book.active;notes.title='内容'
    notes.append(['note_id','content','note_url']);notes.append(['1','微博飞书体验','https://m.weibo.cn/detail/1'])
    comments=book.create_sheet('评论');comments.append(['comment_id','note_id','content','parent_comment_id']);comments.append(['201','1','<b>飞书很好用</b>','0'])
    content=io.BytesIO();book.save(content)
    payload=read_payload(content.getvalue(),'wb.xlsx');payload['platform']='wb';ingest(tid,payload)
    rows=db.comments(tid)
    assert len(rows)==2 and rows[1]['text']=='飞书很好用' and rows[1]['note_title']=='微博飞书体验'
    assert rows[1]['parent_comment_id'] is None

def test_file_default_platform_for_multiplatform_export_import(client):
    tid=create(client,platforms=['dy','bili'])
    headers={'X-Filename':'dy.json','X-Platform':'dy'}
    data=[{'comment_id':'1','aweme_id':'1','content':'飞书体验很好'}]
    r=client.post(f'/api/tasks/{tid}/import',content=json.dumps(data).encode(),headers=headers)
    assert r.status_code==200 and db.comments(tid)[0]['platform']=='dy'
    # Explicit record metadata wins over a file default. Unselected defaults fail.
    data[0]['platform']='bili'
    assert client.post(f'/api/tasks/{tid}/import',content=json.dumps(data).encode(),headers=headers).status_code==200
    assert {x['platform'] for x in db.comments(tid)}=={'dy','bili'}
    data[0].pop('platform');headers['X-Platform']='ks'
    assert client.post(f'/api/tasks/{tid}/import',content=json.dumps(data).encode(),headers=headers).status_code==422

def wait_status(client,tid,status):
    end=time.monotonic()+3
    while time.monotonic()<end:
        t=client.get('/api/tasks/'+tid).json()
        if t['status']==status:return t
        time.sleep(.01)
    raise AssertionError(t)

def test_serial_platform_failure_continues_and_report_is_partial(client,monkeypatch):
    seen=[]
    async def collect_one(tid,cfg,manager,p):
        seen.append(p)
        assert db.task(tid)['current_platform']==p
        if p=='dy':raise crawler.CrawlFailure('协议替身故障',2)
        ingest(tid,native_export(p));await asyncio.sleep(0)
        return 0
    monkeypatch.setattr(crawler,'collect_platform',collect_one)
    tid=create(client,source='crawl',platforms=['bili','dy','wb'])
    task=wait(client,tid)
    assert seen==['bili','dy','wb'] and task['status']=='partial' and task['failure_count']==2
    assert task['stats']['unique']==6 and task['current_platform'] is None
    assert [p['status'] for p in task['platform_runs']]==['completed','failed','completed']
    assert task['report']['stats']['platforms']['wb']['included']==3

def test_login_resume_is_scoped_to_current_platform(client,monkeypatch):
    seen=[]
    async def collect_one(tid,cfg,manager,p):
        seen.append(p);folder=config.DATA/'tasks'/tid/p;folder.mkdir(parents=True,exist_ok=True)
        manager.processes[tid]=object()
        db.update(tid,status='waiting_login',stage='协议替身等待登录',current_platform=p)
        db.set_platform_run(tid,p,'waiting_login','协议替身等待登录')
        try:
            while not (folder/'resume').exists():await asyncio.sleep(.01)
            assert not (folder.parent/'resume').exists()
            ingest(tid,native_export(p));return 0
        finally:manager.processes.pop(tid,None)
    monkeypatch.setattr(crawler,'collect_platform',collect_one)
    tid=create(client,source='crawl',platforms=['dy','bili'])
    assert wait_status(client,tid,'waiting_login')['current_platform']=='dy'
    assert client.post(f'/api/tasks/{tid}/resume').status_code==200
    end=time.monotonic()+3
    while time.monotonic()<end:
        task=client.get('/api/tasks/'+tid).json()
        if task['status']=='waiting_login' and task['current_platform']=='bili':break
        time.sleep(.01)
    else:raise AssertionError(task)
    assert seen==['dy','bili']
    client.post(f'/api/tasks/{tid}/resume')
    assert wait(client,tid)['status']=='completed'

def test_cancel_stops_remaining_platforms(client,monkeypatch):
    seen=[]
    async def collect_one(tid,cfg,manager,p):
        seen.append(p)
        ingest(tid,native_export(p))
        await asyncio.sleep(30)
        return 0
    monkeypatch.setattr(crawler,'collect_platform',collect_one)
    tid=create(client,source='crawl',platforms=['dy','wb','bili'])
    wait_status(client,tid,'collecting')
    client.post(f'/api/tasks/{tid}/cancel');t=wait(client,tid)
    assert seen==['dy'] and t['status']=='cancelled' and t['stats']['unique']==3
    assert all(r['status']=='cancelled' for r in t['platform_runs'])

def test_restart_platform_states_are_interrupted(client):
    tid=create(client,platforms=['dy','wb'])
    db.update(tid,status='waiting_login',current_platform='dy')
    db.set_platform_run(tid,'dy','waiting_login');db.set_platform_run(tid,'wb','queued')
    db.recover()
    assert db.task(tid)['status']=='failed' and db.task(tid)['current_platform'] is None
    assert all(r['status']=='interrupted' for r in db.platform_runs(tid))

def test_legacy_note_migration_backups_and_preserves_analysis(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DATA',tmp_path)
    path=tmp_path/'insight.sqlite3'
    # Build a current DB then substitute the three legacy table definitions.
    db.init()
    with db.connect() as c:
        cfg={'product':'飞书','source':'import','analysis_mode':'rules','keywords':['飞书']}
        c.execute('INSERT INTO tasks(id,config,status,created_at,updated_at,report) VALUES(?,?,?,?,?,?)',('legacy',db.dumps(cfg),'completed',db.now(),db.now(),db.dumps({'preserved':True})))
        c.execute('DROP TABLE notes')
        c.execute('CREATE TABLE notes(task_id TEXT NOT NULL,note_id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(task_id,note_id))')
        c.execute('INSERT INTO notes VALUES(?,?,?)',('legacy','n1',db.dumps({'note_id':'n1','title':'旧笔记'})))
        c.execute('ALTER TABLE tasks DROP COLUMN current_platform')
        c.execute('ALTER TABLE raw_records DROP COLUMN platform')
    db.init();db.init()  # Safe to reopen repeatedly.
    assert db.task('legacy')['config']['platforms']==['xhs']
    assert db.task('legacy')['report']=={'preserved':True}
    with db.connect() as c:
        row=c.execute('SELECT * FROM notes').fetchone()
        assert row['platform']=='xhs' and json.loads(row['data'])['title']=='旧笔记'
        assert 'current_platform' in {r['name'] for r in c.execute('PRAGMA table_info(tasks)')}
    snapshots=list((tmp_path/'backups').glob('before-multiplatform-*.sqlite3'))
    assert len(snapshots)==1
    with sqlite3.connect(snapshots[0]) as c:
        assert 'platform' not in {r[1] for r in c.execute('PRAGMA table_info(notes)')}
        assert c.execute('SELECT count(*) FROM notes').fetchone()[0]==1

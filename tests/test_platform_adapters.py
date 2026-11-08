"""Recorded-shape protocol substitutes, never claimed as live platform collection."""
import asyncio
import inspect
import json
import subprocess
import sys
import time
import queue
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
from insight.platforms import PLATFORMS, normalize_note, normalize_comment
from insight.upstream_adapter import PlatformAdapter

NEW_PLATFORMS=['dy','ks','bili','wb','tieba','zhihu']

class Model(dict):
    def __getattr__(self,key):return self[key]
    def model_dump(self):return dict(self)

class Page:
    async def title(self):return ''
    async def goto(self,*args,**kwargs):pass
    async def content(self):return 'fixture html'

class ProtocolClient:
    def __init__(self,platform):
        self.platform=platform;self.calls=[]
        self._extractor=self;self._page_extractor=self
        self.playwright_page=Page();self._host='https://tieba.baidu.com'

    def note(self,nid='1'):
        p=self.platform
        if p=='dy':return {'aweme_id':nid,'desc':'飞书文档使用体验'}
        if p=='ks':return {'photo':{'id':nid,'caption':'飞书文档使用体验'}}
        if p=='bili':return {'aid':int(nid),'title':'飞书文档使用体验'}
        if p=='wb':return {'id':nid,'text':'<p>飞书文档使用体验</p>'}
        if p=='tieba':return Model(note_id=nid,title='飞书文档使用体验',desc='讨论飞书',note_url='https://tieba.baidu.com/p/'+nid,total_replay_page=1)
        return Model(content_id=nid,content_type='answer',title='飞书文档使用体验',content_text='讨论飞书',content_url='https://www.zhihu.com/question/1/answer/'+nid)

    def comment(self,cid='101',reply=False):
        text='我也是' if reply else '飞书同步经常卡顿'
        p=self.platform
        if p=='dy':return {'cid':cid,'text':text,'digg_count':2,'reply_comment_total':0 if reply else 1}
        if p=='ks':return {'comment_id':int(cid),'content':text,'hasSubComments':not reply}
        if p=='bili':return {'rpid':int(cid),'content':{'message':text},'rcount':0 if reply else 1,'parent':0}
        if p=='wb':return {'id':cid,'text':'<p>'+text+'</p>','comments':[] if reply else [self.comment('102',True)]}
        return Model(comment_id=cid,content=text,sub_comment_count=0 if reply else 1,tieba_id='5',parent_comment_id='')

    def roots(self):return [self.comment('101'),self.comment('103')]
    def subs(self):self.calls.append('replies');return [self.comment('102',True)]

    async def search_info_by_keyword(self,keyword,offset,sort_type,search_id):
        self.calls.append(('search',offset));return {'data':[{'aweme_info':self.note()},{'aweme_info':self.note('2')}],'has_more':False}
    async def search_info_by_keyword_v2(self,keyword,pcursor,search_session_id):
        self.calls.append(('search',pcursor));return {'result':1,'feeds':[self.note(),self.note('2')],'pcursor':'no_more'}
    async def search_video_by_keyword(self,keyword,page,page_size,order,pubtime_begin_s,pubtime_end_s):
        assert pubtime_begin_s==pubtime_end_s==0
        self.calls.append(('search',page));return {'result':[self.note(),self.note('2')]}
    async def get_note_by_keyword(self,keyword,page,search_type=None):
        self.calls.append(('search',page))
        return {'cards':[{'card_group':[{'mblog':self.note()},{'mblog':self.note('2')}]}]} if self.platform=='wb' else [self.note(),self.note('2')]
    async def get_notes_by_keyword(self,keyword,page,page_size,sort,note_type):
        self.calls.append(('search',page));return [self.note(),self.note('2')]
    async def get_video_info(self,aid):return {'View':self.note(str(aid))}
    async def get_note_by_id(self,note_id):return self.note(note_id)
    async def get_aweme_comments(self,aweme_id,cursor):return {'comments':self.roots(),'has_more':False,'cursor':0}
    async def get_sub_comments(self,aweme_id,comment_id,cursor):return {'comments':self.subs(),'has_more':False,'cursor':0}
    async def get_video_comments(self,video_id,pcursor='',*,order_mode=None,next=0):
        if self.platform=='ks':return {'rootCommentsV2':self.roots(),'pcursorV2':'no_more'}
        return {'replies':self.roots(),'cursor':{'is_end':True,'next':0},'top':{'upper':self.comment('101')}}
    async def get_video_sub_comments(self,photo_id,root_comment_id,pcursor):
        assert isinstance(root_comment_id,int)
        return {'subCommentsV2':self.subs(),'pcursorV2':'no_more'}
    async def get_video_level_two_comments(self,video_id,level_one_comment_id,pn,ps,order_mode):
        return {'replies':self.subs(),'page':{'count':1}}
    async def get_note_comments(self,mid_id,max_id,max_id_type):return {'data':self.roots(),'max_id':0}
    async def _get_pc_page_data(self,note_id,page):return {'note_id':note_id}
    def extract_tieba_note_parent_comments_from_api(self,data,note_detail):return self.roots()
    def extract_tieba_note_sub_comments(self,html,parent_comment):return self.subs()
    async def get_root_comments(self,content_id,content_type,offset,limit):return {'data':self.roots(),'paging':{'is_end':True}}
    async def get_child_comments(self,root_comment_id,offset,limit):return {'data':self.subs(),'paging':{'is_end':True}}
    def extract_comments(self,note,data):return data
    def extract_offset(self,paging):return ''
    async def request(self,*args,**kwargs):return {}

FIELDS=SimpleNamespace(
    SearchSortType=SimpleNamespace(GENERAL=0,MOST_LIKE=1,LATEST=2,TIME_DESC=3,RELEVANCE_ORDER=4),
    SearchOrderType=SimpleNamespace(DEFAULT=0,MOST_CLICK=1,LAST_PUBLISH=2),
    SearchType=SimpleNamespace(DEFAULT=0,POPULAR=1,REAL_TIME=2),
    SearchNoteType=SimpleNamespace(MAIN_THREAD=1),CommentOrderType=SimpleNamespace(DEFAULT=0))

def collect_fixture(platform,**overrides):
    cfg={'keywords':['飞书'],'max_notes':1,'max_comments':2,'max_comments_per_note':2,'sort':'general','include_replies':True,'_crawl_interval':0,**overrides}
    events=[];c=ProtocolClient(platform)
    adapter=PlatformAdapter(platform,c,FIELDS,cfg,lambda kind,**data:events.append({'kind':kind,**data}))
    asyncio.run(adapter.run())
    comments=[r for e in events if e['kind']=='comments' for r in e['data']]
    notes=[e['data'] for e in events if e['kind']=='note']
    return comments,notes,c

@pytest.mark.parametrize('platform',NEW_PLATFORMS)
def test_native_protocol_reply_parent_and_total_budget(platform):
    rows,notes,c=collect_fixture(platform)
    assert len(rows)==2 and len(notes)==1
    assert rows[1]['text']=='我也是'
    assert str(rows[1]['parent_comment_id'])=='101'
    assert rows[1]['parent_text']=='飞书同步经常卡顿'
    assert all(x['platform']==platform and '_upstream_record' in x for x in rows+notes)

@pytest.mark.parametrize('platform',NEW_PLATFORMS)
def test_root_limit_and_reply_switch(platform):
    rows,notes,c=collect_fixture(platform,max_comments_per_note=1,max_comments=10,include_replies=False)
    assert len(rows)==1 and len(notes)==1 and 'replies' not in c.calls

@pytest.mark.parametrize('platform',NEW_PLATFORMS)
def test_existing_platform_budget_prevents_extra_search(platform):
    c=ProtocolClient(platform);events=[]
    cfg={'keywords':['飞书'],'max_notes':1,'max_comments':3,'max_comments_per_note':2,'sort':'general','include_replies':True,'_crawl_interval':0}
    adapter=PlatformAdapter(platform,c,FIELDS,cfg,lambda *a,**k:events.append(a),{'existing_note_ids':['1'],'existing_comment_count':1})
    asyncio.run(adapter.run())
    assert not c.calls and not events

def test_repeating_cursor_stops_without_fake_duplicates():
    c=ProtocolClient('dy');calls=[];events=[]
    async def roots(nid,cursor):
        calls.append(cursor)
        return {'comments':c.roots(),'cursor':10 if cursor!=10 else 20,'has_more':True}
    c.get_aweme_comments=roots
    cfg={'max_comments':10,'max_comments_per_note':10,'include_replies':False,'_crawl_interval':0}
    adapter=PlatformAdapter('dy',c,FIELDS,cfg,lambda k,**d:events.append((k,d)))
    asyncio.run(adapter.collect_comments(c.note(),'1','飞书'))
    assert len(calls)<=4
    assert len([e for e in events if e[0]=='comments'])==2

def test_reply_to_unfetched_child_does_not_invent_parent_text():
    x=normalize_comment('bili',{'rpid':4,'parent':3,'content':{'message':'我也是'}},'1','2','另一条根评论')
    assert x['parent_comment_id']==3 and x['parent_text'] is None

@pytest.mark.parametrize('platform,action',[(p,'complete') for p in NEW_PLATFORMS]+[('dy','resume'),('dy','cancel')])
def test_independent_bridge_process_protocol(platform,action,tmp_path):
    """Actual child-process bootstrap with fake upstream, not a platform network test."""
    spec=PLATFORMS[platform];module=spec['module']
    upstream=tmp_path/'upstream';upstream.mkdir()
    fixtures='from types import SimpleNamespace\n'+ '\n\n'.join(inspect.getsource(cls) for cls in (Model,Page,ProtocolClient))
    files={
        'fixture_protocol.py':fixtures,
        'config.py':'CRAWLER_MAX_SLEEP_SEC=0\n',
        'var.py':'from contextvars import ContextVar\nsource_keyword_var=ContextVar("source",default="")\nrequest_keyword_var=ContextVar("request",default="")\n',
        'tools/__init__.py':'','tools/utils.py':'class Logger: disabled=False\nlogger=Logger()\n',
        'media_platform/__init__.py':'',f'media_platform/{module}/__init__.py':'',
        f'media_platform/{module}/client.py':f'from fixture_protocol import ProtocolClient\nclass {spec["client"]}(ProtocolClient):\n def __init__(self):super().__init__({platform!r})\n',
        f'media_platform/{module}/field.py':'\n'.join(f'{name}=SimpleNamespace(**{vars(value)!r})' for name,value in vars(FIELDS).items()),
        f'media_platform/{module}/core.py':f'''from .client import {spec['client']}
class {spec['login']}:
 async def begin(self):pass
class {spec['crawler']}:
 async def start(self):
  self.browser_context=None
  self.cookie_urls=[]
  self.{spec['attribute']}={spec['client']}()
  await self.search()
''',
    }
    if action!='complete':
        files[f'media_platform/{module}/client.py']+=''' async def request(self,*args,**kwargs):
  if not getattr(self,'expired',False):
   self.expired=True
   raise RuntimeError('登录失效 SENSITIVE_UPSTREAM_DETAIL')
  return {}
 async def update_cookies(self,*args,**kwargs):pass
 async def get_aweme_comments(self,aweme_id,cursor):
  await self.request()
  return {"comments":self.roots(),"has_more":False,"cursor":0}
'''
    files[f'media_platform/{module}/field.py']='from types import SimpleNamespace\n'+files[f'media_platform/{module}/field.py']
    for path,content in files.items():
        f=upstream/path;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(content,encoding='utf-8')
    cfg={'keywords':['飞书'],'max_notes':1,'max_comments':2,'max_comments_per_note':2,'include_replies':True,'sort':'general'}
    path=tmp_path/'settings.json';path.write_text(json.dumps({'platform':platform,'config':cfg,'crawler_path':str(upstream),'output':str(tmp_path)}),encoding='utf-8')
    bridge=Path(__file__).resolve().parents[1]/'backend/insight/multi_bridge.py'
    proc=subprocess.Popen([sys.executable,str(bridge),str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    lines=[];messages=queue.Queue()
    def read_events():
        for line in proc.stdout:
            lines.append(line);messages.put(line)
    reader=threading.Thread(target=read_events,daemon=True);reader.start()
    try:
        if action!='complete':
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                try:line=messages.get(timeout=.1)
                except queue.Empty:
                    if proc.poll() is not None:raise AssertionError('bridge exited before login pause')
                    continue
                if b'waiting_login' in line:break
            else:raise AssertionError('no login pause event')
            (tmp_path/('resume' if action=='resume' else 'cancel')).touch()
        proc.wait(timeout=10);reader.join(timeout=1)
        stdout=b''.join(lines);stderr=proc.stderr.read()
        assert proc.returncode==0,stderr.decode(errors='replace')
    finally:
        if proc.poll() is None:proc.kill();proc.wait(timeout=2)
        reader.join(timeout=1);proc.stdout.close();proc.stderr.close()
    assert b'SENSITIVE_UPSTREAM_DETAIL' not in stdout
    events=[json.loads(line[14:]) for line in stdout.decode('utf-8').splitlines() if line.startswith('INSIGHT_EVENT ')]
    if action=='cancel':
        assert any(e['kind']=='waiting_login' for e in events)
        assert not any(e['kind']=='done' for e in events)
        return
    if action=='resume':assert any(e['kind']=='waiting_login' for e in events)
    assert events[-1]['kind']=='done',events
    comments=[r for e in events if e['kind']=='comments' for r in e['data']]
    assert len(comments)==2 and comments[1]['parent_text']=='飞书同步经常卡顿'
    assert all(e['platform']==platform for e in events)

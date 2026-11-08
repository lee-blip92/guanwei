import json
import subprocess
import sys
from pathlib import Path

def test_real_bridge_subprocess_contract_limits_and_parent_context(tmp_path):
    # Fake upstream modules, real bridge/subprocess. Does not claim a real platform login/crawl.
    files={
      'config.py':'CRAWLER_MAX_SLEEP_SEC=0\n',
      'media_platform/__init__.py':'', 'media_platform/xhs/__init__.py':'',
      'media_platform/xhs/exception.py':'class NoteNotFoundError(Exception): pass\nclass PlatformAccessError(Exception): pass\nclass IPBlockError(Exception): pass\n',
      'media_platform/xhs/field.py':'from enum import Enum\nclass SearchSortType(Enum):\n GENERAL="general"\n',
      'tools/__init__.py':'', 'tools/utils.py':'class Logger: disabled=False\nlogger=Logger()\n',
      'media_platform/xhs/core.py':'''from .client import XiaoHongShuClient
def get_search_id(): return "test"
class XiaoHongShuLogin:
 async def begin(self): pass
class XiaoHongShuCrawler:
 async def start(self):
  self.xhs_client=XiaoHongShuClient()
  await self.search()
''',
      'media_platform/xhs/client.py':'''class XiaoHongShuClient:
 async def request(self,*args,**kwargs): return {}
 async def get_note_by_keyword(self,*args,**kwargs): return {"has_more":False,"items":[{"id":"n1"},{"id":"n2"}]}
 async def get_note_by_id(self,*args): return {"title":"飞书体验","desc":"飞书文档"}
 async def get_note_comments(self,*args): return {"comments":[{"id":"c1","content":"飞书卡顿","sub_comments":[{"id":"c2","content":"我也是"}]},{"id":"c3","content":"飞书好用"},{"id":"c4","content":"不应超过上限"}],"has_more":False}
'''
    }
    upstream=tmp_path/'upstream'
    for path,text in files.items():
        f=upstream/path;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(text,encoding='utf-8')
    cfg={'keywords':['飞书'],'max_notes':2,'max_comments':3,'max_comments_per_note':10,'include_replies':True,'sort':'general'}
    setting=tmp_path/'config.json';setting.write_text(json.dumps({'config':cfg,'output':str(tmp_path),'crawler_path':str(upstream)}),encoding='utf-8')
    bridge=Path(__file__).resolve().parents[1]/'backend/insight/crawler_bridge.py'
    output=subprocess.run([sys.executable,str(bridge),str(setting)],capture_output=True,timeout=10)
    assert output.returncode==0,output.stderr.decode(errors='replace')
    events=[json.loads(x.removeprefix('INSIGHT_EVENT ')) for x in output.stdout.decode('utf-8').splitlines() if x.startswith('INSIGHT_EVENT ')]
    assert events[-1]['kind']=='done'
    comments=[c for e in events if e['kind']=='comments' for c in e['data']]
    assert len(comments)==3
    assert next(c for c in comments if c['comment_id']=='c2')['parent_text']=='飞书卡顿'
    assert all('_upstream_record' in c for c in comments)
    assert len([e for e in events if e['kind']=='note'])<=2

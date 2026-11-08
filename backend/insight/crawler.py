"""Serial multi-platform coordinator. Each process owns one platform config."""
import asyncio
import json
import os
import subprocess
from . import config,db
from .platforms import PLATFORMS,catalog,platform_name

class CrawlFailure(RuntimeError):
    def __init__(self,message,failure_count=1):
        super().__init__(message)
        self.failure_count=failure_count

async def collect_platform(task_id,cfg,manager,platform):
    folder=config.DATA/'tasks'/task_id/platform
    folder.mkdir(parents=True,exist_ok=True)
    for marker in ('resume','cancel'):(folder/marker).unlink(missing_ok=True)
    existing=[x for x in db.comments(task_id) if x['platform']==platform]
    with db.connect() as c:
        ids=[r[0] for r in c.execute('SELECT note_id FROM notes WHERE task_id=? AND platform=?',(task_id,platform))]
    run_config=folder/'crawl_config.json'
    run_config.write_text(json.dumps({'platform':platform,'config':cfg,'output':str(folder),'crawler_path':str(config.CRAWLER),
                                     'existing_note_ids':ids,'existing_comment_ids':[x['comment_id'] for x in existing if x['comment_id']],
                                     'existing_comment_count':len(existing)},ensure_ascii=False),encoding='utf-8')
    module=PLATFORMS[platform]['module']
    if not (config.CRAWLER/f'media_platform/{module}/core.py').exists():raise CrawlFailure('找不到该平台 MediaCrawler 源码')
    env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1'};env.pop('OPENAI_API_KEY',None)
    bridge='crawler_bridge.py' if platform=='xhs' else 'multi_bridge.py'
    proc=subprocess.Popen([config.CRAWLER_PYTHON,str(config.ROOT/'backend/insight'/bridge),str(run_config)],cwd=str(config.CRAWLER),
                          stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    manager.processes[task_id]=proc
    failures=0;completed=False;name=platform_name(platform)
    previous_failures=db.task(task_id)['failure_count']
    try:
        while True:
            line=await asyncio.to_thread(proc.stdout.readline)
            if not line:break
            if not line.startswith(b'INSIGHT_EVENT '):continue
            try:event=json.loads(line[len(b'INSIGHT_EVENT '):])
            except (ValueError,UnicodeDecodeError):failures+=1;continue
            kind=event.get('kind')
            if kind in ('note','comments'):
                from .ingest import ingest
                records=[event['data']] if kind=='note' else event['data']
                ingest(task_id,{'notes' if kind=='note' else 'comments':[{**r,'platform':platform} for r in records]})
                with db.connect() as c:count=c.execute('SELECT count(*) FROM notes WHERE task_id=?',(task_id,)).fetchone()[0]
                db.update(task_id,processed_notes=count)
            elif kind=='waiting_login':
                message=f'{name}：采集暂停，请在采集浏览器完成登录/验证，再恢复'
                db.update(task_id,status='waiting_login',stage=message,current_platform=platform)
                db.set_platform_run(task_id,platform,'waiting_login',message,failures);db.event(task_id,kind,message)
            elif kind=='collecting':
                message=f'{name}：'+event.get('message','采集中')
                db.update(task_id,status='collecting',stage=message,current_platform=platform)
                db.set_platform_run(task_id,platform,'collecting',message,failures)
            elif kind=='failure':
                failures+=1;db.set_platform_run(task_id,platform,'collecting','部分请求失败，继续采集',failures)
                db.update(task_id,failure_count=previous_failures+failures)
                db.event(task_id,'failure',f'{name}：'+event.get('message','单条采集失败'))
            elif kind=='fatal':raise CrawlFailure(event.get('message','平台采集失败'),failures+1)
            elif kind=='done':completed=True
        code=await asyncio.to_thread(proc.wait)
        if code or not completed:raise CrawlFailure('平台采集子进程异常退出，已保存数据保留',failures+1)
        return failures
    finally:
        if proc.poll() is None:
            (folder/'cancel').touch()
            try:await asyncio.to_thread(proc.wait,5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:await asyncio.to_thread(proc.wait,3)
                except subprocess.TimeoutExpired:proc.kill()
        if proc.stdout:proc.stdout.close()
        manager.processes.pop(task_id,None)

async def collect(task_id,cfg,manager):
    selected=cfg.get('platforms',['xhs'])
    for p in selected:db.set_platform_run(task_id,p,'queued','等待平台采集')
    failures=0
    try:
        for platform in selected:
            name=platform_name(platform)
            db.update(task_id,status='collecting',current_platform=platform,stage=f'{name}：启动采集浏览器')
            db.set_platform_run(task_id,platform,'collecting','正在启动采集浏览器')
            try:
                count=await collect_platform(task_id,cfg,manager,platform);failures+=count
                stats=db.stats(task_id)['platforms'].get(platform,{})
                state='partial' if count else 'completed' if stats.get('unique') else 'empty'
                db.set_platform_run(task_id,platform,state,'平台采集完成' if stats.get('unique') else '未取得评论',count)
                db.event(task_id,'platform_complete',f'{name}：已保存 {stats.get("unique",0)} 条去重评论')
            except asyncio.CancelledError:raise
            except Exception as exc:
                count=getattr(exc,'failure_count',1);failures+=count
                message=str(exc) if isinstance(exc,CrawlFailure) else f'平台执行失败（{type(exc).__name__}）'
                db.set_platform_run(task_id,platform,'failed',message,count,message)
                db.event(task_id,'platform_failed',f'{name}：{message}；继续后续平台')
            db.update(task_id,failure_count=failures)
    except asyncio.CancelledError:
        for row in db.platform_runs(task_id):
            if row['status'] in ('queued','collecting','waiting_login'):
                db.set_platform_run(task_id,row['platform'],'cancelled','任务已取消',row['failure_count'])
        raise
    finally:db.update(task_id,current_platform=None)
    return failures

def diagnostics():
    platforms=[{**p,'available':(config.CRAWLER/f'media_platform/{PLATFORMS[p["id"]]["module"]}/core.py').exists()} for p in catalog()]
    return {'available':any(p['available'] for p in platforms),'platforms':platforms,
            'python_available':os.path.isfile(config.CRAWLER_PYTHON),'login_state':'unknown',
            'login_message':'各平台在采集时分别检查登录；本地浏览器目录存在不代表登录有效。',
            'license':'NON-COMMERCIAL LEARNING LICENSE 1.1 · 仅限非商业学习研究；商业使用需作者书面授权',
            'supported':['七个平台可多选','关键词搜索','各平台实际支持的排序','一级评论与可获取回复','人工登录/验证'],
            'unsupported':['统一日期范围过滤','验证码绕过','保证完整评论','断点游标恢复']}

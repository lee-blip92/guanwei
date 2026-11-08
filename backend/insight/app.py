import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from . import config, db
from .schemas import TaskConfig, Review, Settings, Narrative, Extraction, Grouping
from .ingest import read_payload, ingest, ImportErrorDetail
from .jobs import manager
from .crawler import diagnostics
from .exports import markdown, comments_csv, print_html
from .platforms import catalog, platform_names

def lock_instance():
    config.DATA.mkdir(parents=True, exist_ok=True)
    handle = open(config.DATA / 'server.lock', 'a+b')
    handle.write(b'0')
    handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('同一数据目录只能运行一个服务进程，避免采集浏览器冲突。')
    return handle

@asynccontextmanager
async def lifespan(app):
    lock = lock_instance()
    db.init()
    db.recover()
    yield
    await manager.close()
    lock.close()

app = FastAPI(title='观微 · 产品反馈洞察', version='0.1.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['http://127.0.0.1:5173', 'http://localhost:5173'], allow_methods=['GET','POST','PATCH'], allow_headers=['Content-Type','X-Filename','X-Platform'])

@app.middleware('http')
async def local_request_guard(request: Request, call_next):
    host = request.headers.get('host', '').split(':')[0]
    if host not in ('127.0.0.1', 'localhost', 'testserver', '[::1]'):
        return JSONResponse({'detail': '仅允许本机访问'}, status_code=403)
    origin = request.headers.get('origin')
    if request.method in ('POST','PATCH','DELETE') and origin and origin not in ('http://127.0.0.1:5173','http://localhost:5173','http://127.0.0.1:8765','http://localhost:8765'):
        return JSONResponse({'detail': '不允许来自其他网站的写操作'}, status_code=403)
    return await call_next(request)

@app.exception_handler(KeyError)
async def missing(request, exc):
    return JSONResponse({'detail': '任务或评论不存在'}, status_code=404)

def detail(task_id):
    value = db.task(task_id)
    value['stats'] = db.stats(task_id)
    value['platform_runs'] = db.platform_runs(task_id)
    prior=value.get('report') or db.latest_report_snapshot(task_id)
    value['can_recount_report']=bool(prior and prior.get('analysis_version')==2)
    value['validation_summary']=validation_summary(task_id,value)
    with db.connect() as c:
        value['events'] = [dict(x) for x in c.execute('SELECT kind,message,created_at FROM events WHERE task_id=? ORDER BY id DESC LIMIT 30', (task_id,))]
    return value

def validation_summary(task_id,t):
    rows=db.comments(task_id)
    failed=[x['evidence_id'] for x in rows if not x['effective'].get('excluded') and 'points' not in x['overrides'] and
        ((x.get('analysis') or {}).get('opinion_audit',{}).get('state') in ('pending','needs_review') or
         x['effective'].get('research_status')=='needs_review')]
    report=t.get('report') or {};decision=report.get('decision') or {}
    reviews=decision.get('generated_review',[])
    return {'comment_failures':len(failed),'comment_ids':failed,
        'unmapped_opinions':len(decision.get('unmapped_unit_ids',[])),
        'generated_claims':len({(x.get('text'),tuple(x.get('evidence_ids',[]))) for x in reviews}),
        'can_repair':bool(failed and t['config'].get('analysis_version')==2 and t['config'].get('insight_version')==3 and t['config']['analysis_mode']=='ai')}

def idle(task_id):
    t = db.task(task_id)
    if task_id in manager.running or t['status'] in db.ACTIVE:
        raise HTTPException(409, '任务执行中，请先取消或等待完成')
    return t

@app.get('/api/health')
def health():
    return {'status':'ok','version':'0.1.0'}

@app.post('/api/keywords')
def keywords(payload: dict):
    name = str(payload.get('product', '')).strip()[:100]
    if not name:
        raise HTTPException(422, '请输入产品名称')
    ambiguous = name.casefold() in {'signal','飞书助手','夸克','小米','苹果','剪刀','notion ai','通义','豆包','即梦','arc','flow','cursor','sift'} or len(name) == 1
    return {'keywords': [name, name+' 使用体验', name+' 新手上手', name+' 使用场景', name+' 好用', name+' 问题反馈'],
            'needs_context': ambiguous and not payload.get('description'),
            'message': '产品名称可能重名，请补充产品简介或官网，以便判断反馈归属。' if ambiguous else '搜索词同时覆盖中性、正面与问题反馈，可自由编辑。'}

@app.get('/api/platforms')
def platforms():return catalog()

@app.get('/api/tasks')
def list_tasks():
    with db.connect() as c:
        ids = [r[0] for r in c.execute('SELECT id FROM tasks ORDER BY created_at DESC')]
    result = []
    for tid in ids:
        t = db.task(tid)
        t.pop('report')
        t['stats'] = db.stats(tid)
        result.append(t)
    return result

@app.post('/api/tasks', status_code=201)
async def create_task(payload: TaskConfig):
    cfg = payload.model_dump()
    terms = keywords({'product':cfg['product'], 'description':cfg['description']})
    if terms['needs_context']:
        raise HTTPException(422, terms['message'])
    if not cfg['keywords']:
        cfg['keywords'] = terms['keywords']
    tid = str(uuid.uuid4())
    with db.connect() as c:
        c.execute('INSERT INTO tasks(id,config,status,stage,created_at,updated_at,source_name) VALUES(?,?,?,?,?,?,?)',
                  (tid, db.dumps(cfg), 'awaiting_data' if cfg['source']=='import' else 'queued', '等待导入数据' if cfg['source']=='import' else '等待开始', db.now(), db.now(), '演示模式 · 人工虚构数据' if cfg['source']=='demo' else platform_names(cfg['platforms'])+' · MediaCrawler' if cfg['source']=='crawl' else None))
    if cfg['source'] == 'demo':
        demo = json.loads((config.ROOT / 'samples/demo-feishu.json').read_text(encoding='utf-8'))
        ingest(tid, demo)
        manager.start(tid)
    elif cfg['source'] == 'crawl':
        manager.start(tid, crawl=True)
    return detail(tid)

@app.get('/api/tasks/{task_id}')
def get_task(task_id: str):
    return detail(task_id)

@app.post('/api/tasks/{task_id}/import')
async def import_data(task_id: str, request: Request):
    t = idle(task_id)
    if t['config']['source'] != 'import':
        raise HTTPException(409, '真实导入请创建独立导入任务；演示与采集数据不混用')
    from urllib.parse import unquote
    filename = Path(unquote(request.headers.get('x-filename', 'import.json')).replace('\\','/')).name
    if not filename.lower().endswith(('.json','.jsonl','.xlsx')):
        raise HTTPException(422, '支持 JSON、JSONL 和 MediaCrawler Excel 文件')
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(413, '文件超过 20 MB')
    try:
        payload = read_payload(bytes(data), filename)
        if isinstance(payload, dict) and payload.get('data_mode') == 'demo':
            raise ImportErrorDetail('此文件标记为演示数据，请使用首页“先用演示数据体验”，不要导入真实研究任务。')
        count = ingest(task_id, payload, file_platform=request.headers.get('x-platform'))
    except ImportErrorDetail as exc:
        raise HTTPException(422, str(exc))
    folder = config.DATA / 'tasks' / task_id / 'imports'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'{uuid.uuid4().hex}{Path(filename).suffix}').write_bytes(data)
    db.snapshot_report(task_id)
    previous = t.get('source_name')
    db.update(task_id, source_name=(previous + '；' if previous else '') + filename, report=None, stage=f'已导入 {count} 条原始评论，等待分析', status='ready')
    db.event(task_id, 'import', f'导入 {filename}：{count} 条原始评论；原始文件已保存。')
    return detail(task_id)

@app.post('/api/tasks/{task_id}/analyze')
async def analyze(task_id: str, options: dict | None = None):
    t = idle(task_id)
    if not db.comments(task_id):
        raise HTTPException(422, '请先导入或采集评论')
    if options and 'analysis_version' in options and options['analysis_version'] not in (1,2):
        raise HTTPException(422,'不支持的分析版本')
    t['config']['analysis_version']=(options or {}).get('analysis_version',t['config'].get('analysis_version',2))
    if options and 'insight_version' in options:
        if options['insight_version'] not in (2,3):raise HTTPException(422,'不支持的洞察版本')
        t['config']['insight_version']=options['insight_version']
    if options and options.get('analysis_mode') in ('ai','rules'):
        t['config']['analysis_mode'] = options['analysis_mode']
    with db.connect() as c:
        c.execute('UPDATE tasks SET config=? WHERE id=?', (db.dumps(t['config']), task_id))
    manager.start(task_id)
    return detail(task_id)

@app.post('/api/tasks/{task_id}/resume')
async def resume(task_id: str):
    t = db.task(task_id)
    folder = config.DATA / 'tasks' / task_id / (t.get('current_platform') or 'xhs')
    if t['status'] == 'waiting_login' and task_id in manager.processes:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'resume').touch()
        db.update(task_id, stage='已提交恢复操作，等待平台验证登录状态')
    else:
        idle(task_id)
        if t['config']['source'] != 'crawl':
            raise HTTPException(409, '仅采集任务可恢复采集；导入任务请重新分析')
        # Restart from keywords; append raw records, dedup by real IDs. No fake cursor resume.
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'cancel').unlink(missing_ok=True)
        (folder / 'resume').unlink(missing_ok=True)
        manager.start(task_id, crawl=True)
        db.event(task_id, 'resume', '重新运行关键词采集，保留已有数据并按评论 ID 合并；不支持断点游标恢复。')
    return detail(task_id)

@app.post('/api/tasks/{task_id}/analysis/repair')
async def repair_validation(task_id: str):
    t=idle(task_id);summary=validation_summary(task_id,t)
    if not summary['can_repair']:
        raise HTTPException(409,'没有可自动修复的评论校验失败项；主题未分类或证据不足需要单独核查。')
    if not t.get('report') or t['report'].get('decision_version')!=3:
        raise HTTPException(409,'请先完成决策报告，才能仅修复失败项并复用其余分析。')
    manager.start(task_id,repair_ids=set(summary['comment_ids']))
    db.event(task_id,'validation_repair',f'仅修复 {summary["comment_failures"]} 条校验失败评论；保留合格分析与人工修正，不重新采集；报告会按修复结果更新。')
    return detail(task_id)

@app.post('/api/tasks/{task_id}/report/regenerate')
async def regenerate_report(task_id: str):
    t = idle(task_id)
    if not t['report'] or t['status'] not in ('completed','partial'):
        raise HTTPException(409, '仅能复用已完成且未经人工修改的分析结果；请先完成重新分析。')
    manager.start(task_id, report_only=True)
    db.event(task_id, 'report_regenerate', '复用完整已校验分析结果重生成报告，不重复采集或逐条提取。')
    return detail(task_id)

@app.post('/api/tasks/{task_id}/report/upgrade')
async def upgrade_decision_report(task_id: str):
    t=idle(task_id);rows=db.comments(task_id)
    if not rows or any(not x.get('analysis') and not x['effective'].get('excluded') for x in rows):
        raise HTTPException(422,'请先完成评论的初步分析，再复用观点生成决策报告')
    db.snapshot_report(task_id)
    t['config'].update(analysis_version=2,insight_version=3)
    with db.connect() as c:c.execute('UPDATE tasks SET config=? WHERE id=?',(db.dumps(t['config']),task_id))
    manager.start(task_id,report_only=True)
    db.event(task_id,'decision_upgrade','复用已有原始观点，升级独立研究轴、层级分类与决策报告；不重新采集或初提取。')
    return detail(task_id)

@app.post('/api/tasks/{task_id}/cancel')
async def cancel(task_id: str):
    db.task(task_id)
    await manager.cancel(task_id)
    return detail(task_id)

@app.post('/api/tasks/{task_id}/report/recount')
async def recount_report(task_id: str):
    t=idle(task_id)
    db.snapshot_report(task_id)
    previous=db.latest_report_snapshot(task_id)
    if not previous or previous.get('analysis_version')!=2 or t['config'].get('analysis_version')!=2:
        raise HTTPException(422,'需要已保存的 V2 报告，才能复算统计并保守复用旧文案')
    rows=db.comments(task_id)
    if any(not x['effective'].get('excluded') and (x['effective'].get('analysis_version')!=2 or
           (x.get('analysis') or {}).get('opinion_audit',{}).get('state')=='pending') for x in rows):
        raise HTTPException(422,'存在尚未完成观点分析的新增评论，请先完成分析；仅重算不会调用模型')
    manager.start(task_id,report_only=True,cached_report=previous)
    db.event(task_id,'report_recount','仅重算统计，不调用模型；删除依赖已修改或排除评论的旧结论。')
    return detail(task_id)

@app.get('/api/tasks/{task_id}/comments')
def get_comments(task_id: str, q: str='', relevance: str='', sentiment: str='', theme: str='', status: str='', platform: str='', offset: int=0, limit: int=100):
    db.task(task_id)
    rows = db.comments(task_id)
    def matches(x):
        e = x['effective']
        return (not q or q.casefold() in ' '.join(str(x.get(k) or '') for k in ('text','note_title','parent_text','evidence_id')).casefold()) and (not relevance or e.get('relevance')==relevance) and (not sentiment or e.get('sentiment')==sentiment) and (not theme or theme in e.get('themes',[])) and (not status or (status=='excluded' and e.get('excluded')) or (status=='included' and db.included(x)) or (status=='reviewed' and bool(x['overrides'])) or (status=='ad' and e.get('suspected_ad')) or (status=='unreviewed' and not x['overrides']))
    selected = [x for x in rows if matches(x) and (not platform or x['platform']==platform)]
    offset, limit = max(0, offset), max(1,min(200,limit))
    return {'items':selected[offset:offset+limit], 'total':len(selected), 'themes':sorted({t for x in rows for t in x['effective'].get('themes',[])})}

@app.get('/api/tasks/{task_id}/comments/{evidence_id:path}')
def get_comment(task_id: str, evidence_id: str):
    value=next((x for x in db.comments(task_id) if x['evidence_id']==evidence_id), None) or (_ for _ in ()).throw(KeyError(evidence_id))
    value['decision_trace']=(db.task(task_id).get('report') or {}).get('decision',{}).get('evidence_links',{}).get(evidence_id)
    return value

@app.patch('/api/tasks/{task_id}/comments/{evidence_id:path}')
def review(task_id: str, evidence_id: str, payload: Review):
    idle(task_id)
    change = payload.model_dump(exclude_none=True)
    if 'points' in change:
        from .aspect_analysis import validate_points,labels_from_points
        from .analysis import ModelFailure
        item=get_comment(task_id,evidence_id)
        try:validate_points(change['points'],item,strict_literals=True)
        except ModelFailure as exc:raise HTTPException(422,str(exc))
        derived=labels_from_points(change['points'],item['effective'].get('relevance','uncertain'))
        change={**derived,**change}
    if 'themes' in change:
        change['themes'] = list(dict.fromkeys(x.strip()[:60] for x in change['themes'] if x.strip()))
    db.snapshot_report(task_id)
    with db.connect() as c:
        row = c.execute('SELECT overrides FROM comments WHERE task_id=? AND evidence_id=?', (task_id,evidence_id)).fetchone()
        if not row:
            raise KeyError(evidence_id)
        before = json.loads(row[0])
        after = {**before, **change}
        c.execute('UPDATE comments SET overrides=? WHERE task_id=? AND evidence_id=?', (db.dumps(after), task_id,evidence_id))
        c.execute('INSERT INTO reviews(task_id,evidence_id,before_value,change_value,created_at) VALUES(?,?,?,?,?)', (task_id,evidence_id,db.dumps(before),db.dumps(change),db.now()))
    db.update(task_id, report=None, stage='人工修正已保存；请重新分析更新报告', status='ready')
    return get_comment(task_id,evidence_id)

@app.get('/api/tasks/{task_id}/export/{format}')
def export(task_id: str, format: str,view: str='brief',download: bool=False):
    t = db.task(task_id)
    if not t['report'] or t['status'] not in ('completed','partial'):
        raise HTTPException(409, '报告未生成或已因人工修正失效，请先重新分析')
    if view not in ('brief','full'):raise HTTPException(422,'报告视图应为 brief 或 full')
    if format == 'csv':
        content, mime, suffix = comments_csv(task_id), 'text/csv', 'csv'
    elif format == 'md':
        content, mime, suffix = markdown(t['report'],view), 'text/markdown', 'md'
    elif format == 'html':
        from .report_renderer import ReportRenderFailure
        try:content=print_html(t['report'],view,version=t['version'])
        except ReportRenderFailure as exc:raise HTTPException(503,str(exc)) from exc
        headers={}
        if download:
            filename=t['config']['product']+'-反馈报告-'+('精简版' if view=='brief' else '完整版')+'.html'
            headers['Content-Disposition']="attachment; filename*=UTF-8''"+quote(filename)
        return Response(content, media_type='text/html',headers=headers)
    elif format == 'json':
        content, mime, suffix = db.dumps(t['report']), 'application/json', 'json'
    else:
        raise HTTPException(404, '不支持的格式')
    return Response(content, media_type=mime, headers={'Content-Disposition':f"attachment; filename*=UTF-8''{quote(t['config']['product']+'-反馈报告.'+suffix)}"})

@app.get('/api/settings')
def get_settings():
    return {**db.settings(), 'key_configured':bool(os.getenv('OPENAI_API_KEY')), 'crawler':diagnostics()}

@app.patch('/api/settings')
def save_settings(payload: Settings):
    if manager.running:
        raise HTTPException(409, '请等待当前任务完成后修改模型配置')
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO settings VALUES('model',?)", (db.dumps(payload.model_dump()),))
    return get_settings()

@app.post('/api/settings/test')
async def test_model():
    from .analysis import ModelClient, ModelFailure
    try:
        result = await ModelClient().call('连通性测试：只返回四个空数组。', {}, Narrative)
        return {'ok':True,'message':'模型连通且 JSON Schema 校验通过；此结果不代表分析质量已评估。'}
    except ModelFailure as exc:
        raise HTTPException(422, str(exc))

@app.get('/api/schemas')
def schemas():
    from .schemas import AspectExtraction,OpinionGrouping,TopicAdviceBatch,OpinionAuditBatch
    from .decision_schemas import ResearchBatch,TaxonomyPlan,TaxonomyMapping,DecisionNarrative
    from .semantic_schemas import MeaningBatch,AssignmentBatch,MembershipBatch,ComparisonBatch,ClaimRepairBatch,ConsolidationPlan,ScopeBatch
    return {'extraction':Extraction.model_json_schema(),'grouping':Grouping.model_json_schema(),'narrative':Narrative.model_json_schema(),
            'aspect_extraction_v2':AspectExtraction.model_json_schema(),'opinion_grouping_v2':OpinionGrouping.model_json_schema(),
            'topic_advice_v2':TopicAdviceBatch.model_json_schema(),'opinion_audit_v2':OpinionAuditBatch.model_json_schema(),
            'meaning_semantic_v1':MeaningBatch.model_json_schema(),'issue_assignment_semantic_v1':AssignmentBatch.model_json_schema(),
            'issue_membership_semantic_v1':MembershipBatch.model_json_schema(),'comparability_semantic_v1':ComparisonBatch.model_json_schema(),
            'claim_repair_semantic_v1':ClaimRepairBatch.model_json_schema(),
            'issue_consolidation_semantic_v1':ConsolidationPlan.model_json_schema(),
            'issue_scope_semantic_v1':ScopeBatch.model_json_schema(),
            'research_v3':ResearchBatch.model_json_schema(),'taxonomy_v3':TaxonomyPlan.model_json_schema(),
            'taxonomy_mapping_v3':TaxonomyMapping.model_json_schema(),'decision_narrative_v3':DecisionNarrative.model_json_schema()}

dist = config.ROOT / 'frontend/dist'
if dist.exists():
    app.mount('/assets', StaticFiles(directory=dist/'assets'), name='assets')
    @app.get('/{path:path}')
    def frontend(path: str):
        if path.startswith('api/'):
            raise HTTPException(404)
        return FileResponse(dist/'index.html')

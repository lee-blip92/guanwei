import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from . import config

ACTIVE = ('queued', 'waiting_login', 'collecting', 'cleaning', 'analyzing', 'reporting')

def now():
    return datetime.now(timezone.utc).isoformat()

def dumps(value):
    return json.dumps(value, ensure_ascii=False)

@contextmanager
def connect():
    config.DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DATA / 'insight.sqlite3', timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init():
    with connect() as c:
        old_columns={r['name'] for r in c.execute('PRAGMA table_info(notes)')}
        if old_columns and 'platform' not in old_columns:
            # Snapshot the complete previous database before changing its compound keys.
            backups=config.DATA/'backups'
            backups.mkdir(parents=True,exist_ok=True)
            path=backups/('before-multiplatform-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.sqlite3')
            with sqlite3.connect(path) as backup:c.backup(backup)
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY, config TEXT NOT NULL, status TEXT NOT NULL,
          stage TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          error TEXT, failure_count INTEGER NOT NULL DEFAULT 0, processed_notes INTEGER NOT NULL DEFAULT 0,
          version INTEGER NOT NULL DEFAULT 0, report TEXT, model_info TEXT, source_name TEXT,
          analyzed_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS notes (
          task_id TEXT NOT NULL REFERENCES tasks(id), platform TEXT NOT NULL DEFAULT 'xhs', note_id TEXT NOT NULL, data TEXT NOT NULL,
          PRIMARY KEY(task_id,platform,note_id)
        );
        CREATE TABLE IF NOT EXISTS raw_records (
          id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), kind TEXT NOT NULL,
          data TEXT NOT NULL, collected_at TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'xhs'
        );
        CREATE TABLE IF NOT EXISTS comments (
          task_id TEXT NOT NULL REFERENCES tasks(id), evidence_id TEXT NOT NULL,
          platform TEXT NOT NULL, comment_id TEXT, note_id TEXT,
          data TEXT NOT NULL, analysis TEXT, overrides TEXT NOT NULL DEFAULT '{}',
          PRIMARY KEY(task_id,evidence_id)
        );
        CREATE TABLE IF NOT EXISTS reviews (
          id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), evidence_id TEXT NOT NULL,
          before_value TEXT NOT NULL, change_value TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), kind TEXT NOT NULL,
          message TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS report_snapshots (
          task_id TEXT NOT NULL REFERENCES tasks(id),version INTEGER NOT NULL,report TEXT NOT NULL,created_at TEXT NOT NULL,
          PRIMARY KEY(task_id,version)
        );
        CREATE INDEX IF NOT EXISTS raw_task_idx ON raw_records(task_id,kind);
        CREATE TABLE IF NOT EXISTS research_cache (
          task_id TEXT NOT NULL REFERENCES tasks(id),cache_key TEXT NOT NULL,input_hash TEXT NOT NULL,
          result TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(task_id,cache_key,input_hash)
        );
        CREATE TABLE IF NOT EXISTS platform_runs (
          task_id TEXT NOT NULL REFERENCES tasks(id), platform TEXT NOT NULL, status TEXT NOT NULL,
          stage TEXT NOT NULL DEFAULT '', failure_count INTEGER NOT NULL DEFAULT 0, error TEXT,
          updated_at TEXT NOT NULL, PRIMARY KEY(task_id,platform)
        );
        ''')
        # Keep key migration atomic, including DDL. Old releases only stored xhs.
        c.execute('BEGIN IMMEDIATE')
        if 'platform' not in {r['name'] for r in c.execute('PRAGMA table_info(notes)')}:
            c.execute('CREATE TABLE IF NOT EXISTS notes_v2(task_id TEXT NOT NULL REFERENCES tasks(id),platform TEXT NOT NULL,note_id TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(task_id,platform,note_id))')
            c.execute("INSERT OR REPLACE INTO notes_v2 SELECT task_id,'xhs',note_id,data FROM notes")
            c.execute('DROP TABLE notes')
            c.execute('ALTER TABLE notes_v2 RENAME TO notes')
        if 'platform' not in {r['name'] for r in c.execute('PRAGMA table_info(raw_records)')}:
            c.execute("ALTER TABLE raw_records ADD COLUMN platform TEXT NOT NULL DEFAULT 'xhs'")
        if 'current_platform' not in {r['name'] for r in c.execute('PRAGMA table_info(tasks)')}:
            c.execute('ALTER TABLE tasks ADD COLUMN current_platform TEXT')

def recover():
    with connect() as c:
        rows = c.execute('SELECT id,status FROM tasks').fetchall()
        for row in rows:
            if row['status'] in ACTIVE:
                c.execute("UPDATE tasks SET status='failed',stage='应用已重启；已保存数据，可重新分析或恢复采集',error='应用重启导致后台执行中断',current_platform=NULL,updated_at=? WHERE id=?", (now(), row['id']))
                c.execute('INSERT INTO events(task_id,kind,message,created_at) VALUES(?,?,?,?)', (row['id'], 'restart', '检测到应用重启，已结束遗留执行状态；不会自动重复采集。', now()))
        c.execute("UPDATE platform_runs SET status='interrupted',stage='应用重启，平台采集中断',updated_at=? WHERE status IN ('queued','collecting','waiting_login')",(now(),))

def task(task_id):
    with connect() as c:
        row = c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
    if not row:
        raise KeyError(task_id)
    value = dict(row)
    for key in ('config', 'report', 'model_info'):
        value[key] = json.loads(value[key]) if value[key] else None
    value['config'].setdefault('platforms',['xhs'])
    return value

def update(task_id, **values):
    allowed = {'status', 'stage', 'error', 'failure_count', 'processed_notes', 'report', 'version', 'analyzed_count', 'model_info', 'source_name','current_platform'}
    assert values.keys() <= allowed
    values['updated_at'] = now()
    with connect() as c:
        c.execute('UPDATE tasks SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?', [dumps(v) if k in ('report', 'model_info') and v is not None else v for k,v in values.items()] + [task_id])

def event(task_id, kind, message):
    with connect() as c:
        c.execute('INSERT INTO events(task_id,kind,message,created_at) VALUES(?,?,?,?)', (task_id, kind, message, now()))

def snapshot_report(task_id):
    with connect() as c:
        row=c.execute('SELECT report,version FROM tasks WHERE id=?',(task_id,)).fetchone()
        if row and row['report']:
            c.execute('INSERT OR IGNORE INTO report_snapshots VALUES(?,?,?,?)',(task_id,row['version'],row['report'],now()))

def latest_report_snapshot(task_id):
    with connect() as c:
        row=c.execute('SELECT report FROM report_snapshots WHERE task_id=? ORDER BY version DESC LIMIT 1',(task_id,)).fetchone()
    return json.loads(row[0]) if row else None

def comments(task_id):
    with connect() as c:
        research={}
        for r in c.execute("SELECT cache_key,result FROM research_cache WHERE task_id=? AND cache_key LIKE 'comment:%' ORDER BY created_at",(task_id,)):
            research.setdefault(r['cache_key'][8:],[]).append(json.loads(r['result']))
        cfgrow=c.execute('SELECT config,report FROM tasks WHERE id=?',(task_id,)).fetchone()
        if not cfgrow:raise KeyError(task_id)
        cfg=json.loads(cfgrow[0])
        report=json.loads(cfgrow['report']) if cfgrow['report'] else None
        rows = c.execute('SELECT * FROM comments WHERE task_id=? ORDER BY rowid', (task_id,)).fetchall()
        notes = {(r['platform'],r['note_id']): json.loads(r['data']) for r in c.execute('SELECT * FROM notes WHERE task_id=?', (task_id,))}
        audits = {}
        for r in c.execute('SELECT * FROM reviews WHERE task_id=? ORDER BY id', (task_id,)):
            audits.setdefault(r['evidence_id'], []).append({'at': r['created_at'], 'change': json.loads(r['change_value'])})
    result = []
    for row in rows:
        item = json.loads(row['data'])
        item['evidence_id'] = row['evidence_id']
        n = notes.get((item['platform'],item.get('note_id')), {})
        item['note_title'] = item.get('note_title') or n.get('title')
        item['note_body'] = item.get('note_body') or n.get('body')
        item['source_url'] = item.get('source_url') or n.get('source_url')
        item['source_keywords'] = list(dict.fromkeys(item.get('source_keywords', []) + n.get('source_keywords', [])))
        item['analysis'] = json.loads(row['analysis']) if row['analysis'] else None
        item['overrides'] = json.loads(row['overrides'])
        item['effective'] = {**(item['analysis'] or {}), **item['overrides']}
        item['audit'] = audits.get(item['evidence_id'], [])
        result.append(item)
    by_id = {(x['platform'], x['comment_id']): x for x in result if x.get('comment_id')}
    for item in result:
        parent = by_id.get((item['platform'], item.get('parent_comment_id')))
        if parent:
            item['parent_text'] = parent['text']
            item['parent_evidence_id'] = parent['evidence_id']
    blocked={x['evidence_id'] for x in result if x['effective'].get('excluded')}
    for item in result:
        e=item['effective']
        if e.get('analysis_version')==2 and item.get('parent_evidence_id') in blocked:
            item['parent_blocked']=True
            points=[dict(p) for p in e.get('points',[])]
            for p in points:
                if p['basis']=='agreement' and p['product_scope']=='target':p['product_scope']='uncertain'
            e['points']=points
            target=[p for p in points if p['product_scope']=='target']
            e['has_feedback']=bool(target)
            if not target and e.get('relevance')=='target':e['relevance']='uncertain'
            e['reason']='父评论已被人工排除；依赖该上下文的附和观点暂不参与报告。'
        if cfg.get('insight_version')==3:
            from .decision_state import apply_research,source_hash
            candidates=research.get(item['evidence_id'],[])
            record=next((r for r in reversed(candidates) if r.get('source_hash')==source_hash(item)),candidates[-1] if candidates else None)
            item['research']=record
            apply_research(item,record)
            if report and report.get('decision_version')==3:
                ds={d['id']:d['name'] for d in report['decision']['taxonomy']};cs={c['id']:c['name'] for d in report['decision']['taxonomy'] for c in d['children']}
                mapped=[u for u in report['opinion_points'] if u['evidence_id']==item['evidence_id']]
                e['themes']=list(dict.fromkeys(ds[u['domain_id']] for u in mapped if u.get('domain_id') in ds))
                e['research_categories']=list(dict.fromkeys(cs[u['category_id']] for u in mapped if u.get('category_id') in cs))
    return result

def cached_research(task_id,key,input_hash):
    with connect() as c:row=c.execute('SELECT result FROM research_cache WHERE task_id=? AND cache_key=? AND input_hash=?',(task_id,key,input_hash)).fetchone()
    return json.loads(row[0]) if row else None

def save_research(task_id,key,input_hash,result):
    with connect() as c:c.execute('INSERT OR REPLACE INTO research_cache VALUES(?,?,?,?,?)',(task_id,key,input_hash,dumps(result),now()))

def hydrate_provenance(task_id):
    """Recover only explicit identifiers from preserved exports, never guess from a nickname."""
    from .platforms import normalize_comment
    with connect() as c:
        latest={}
        for raw in c.execute("SELECT platform,data FROM raw_records WHERE task_id=? AND kind='comment' ORDER BY id",(task_id,)):
            try:value=normalize_comment(raw['platform'],json.loads(raw['data']))
            except (TypeError,ValueError,AttributeError):continue
            if value.get('comment_id'):latest[(raw['platform'],str(value['comment_id']))]=value
        for row in c.execute('SELECT evidence_id,data FROM comments WHERE task_id=?',(task_id,)).fetchall():
            item=json.loads(row['data']);raw=latest.get((item['platform'],item.get('comment_id')))
            if not raw:continue
            for key in ('user_id','parent_relation_known','root_comment_id'):
                if key not in item or item[key] is None:item[key]=raw.get(key)
            c.execute('UPDATE comments SET data=? WHERE task_id=? AND evidence_id=?',(dumps(item),task_id,row['evidence_id']))

def included(item):
    e = item['effective']
    return e.get('relevance') == 'target' and e.get('has_feedback',True) and not e.get('excluded', False) and not e.get('suspected_ad', False)

def stats(task_id, items=None):
    items = comments(task_id) if items is None else items
    with connect() as c:
        raw = c.execute("SELECT count(*) FROM raw_records WHERE task_id=? AND kind='comment'", (task_id,)).fetchone()[0]
        notes = c.execute('SELECT count(*) FROM notes WHERE task_id=?', (task_id,)).fetchone()[0]
        raw_counts = dict(c.execute("SELECT platform,count(*) FROM raw_records WHERE task_id=? AND kind='comment' GROUP BY platform",(task_id,)).fetchall())
        note_counts = dict(c.execute('SELECT platform,count(*) FROM notes WHERE task_id=? GROUP BY platform',(task_id,)).fetchall())
    pending = sum(not x['effective'].get('relevance') for x in items)
    uncertain = sum(x['effective'].get('relevance') == 'uncertain' and not x['effective'].get('excluded') for x in items)
    accepted = sum(included(x) for x in items)
    per_platform={}
    for platform in set(raw_counts)|set(note_counts)|{x['platform'] for x in items}:
        rows=[x for x in items if x['platform']==platform]
        per_platform[platform]={'raw':raw_counts.get(platform,0),'unique':len(rows),'notes':note_counts.get(platform,0),
                                'included':sum(included(x) for x in rows),'uncertain':sum(x['effective'].get('relevance')=='uncertain' and not x['effective'].get('excluded') for x in rows)}
    return {'raw': raw, 'unique': len(items), 'duplicates': raw-len(items), 'included': accepted,
            'excluded': len(items)-accepted-uncertain-pending, 'uncertain': uncertain, 'pending': pending, 'notes': notes,
            'manual_excluded': sum(bool(x['effective'].get('excluded')) for x in items),'platforms':per_platform}

def set_platform_run(task_id,platform,status,stage='',failure_count=0,error=None):
    with connect() as c:
        c.execute('INSERT INTO platform_runs VALUES(?,?,?,?,?,?,?) ON CONFLICT(task_id,platform) DO UPDATE SET status=excluded.status,stage=excluded.stage,failure_count=excluded.failure_count,error=excluded.error,updated_at=excluded.updated_at',
                  (task_id,platform,status,stage,failure_count,error,now()))

def platform_runs(task_id):
    from .platforms import platform_name
    with connect() as c:
        runs={r['platform']:dict(r) for r in c.execute('SELECT * FROM platform_runs WHERE task_id=?',(task_id,))}
    counts=stats(task_id)['platforms']
    return [{**runs.get(p,{'platform':p,'status':'not_started','stage':'尚未采集','failure_count':0,'error':None}),
             'name':platform_name(p),'stats':counts.get(p,{'raw':0,'unique':0,'included':0,'uncertain':0,'notes':0})} for p in task(task_id)['config']['platforms']]

def settings():
    result = config.model_defaults()
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key='model'").fetchone()
    if row:
        result.update(json.loads(row[0]))
    return result

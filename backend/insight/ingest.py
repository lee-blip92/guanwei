"""Boundary for exported MediaCrawler records and the application's stable data contract."""
import hashlib
import io
import json
from datetime import datetime, timezone
from . import db
from .platforms import PLATFORMS, platform_id, normalize_note, normalize_comment

class ImportErrorDetail(ValueError):
    pass

COMMENT_KEYS={'comment_id','cid','rpid','commentId','parent_comment_id','reply_id'}
NOTE_KEYS={'title','caption','desc','content_text','aweme_info','photo','View','mblog'}

def is_note(record):
    if record.get('kind')=='note':return True
    if record.get('kind')=='comment' or COMMENT_KEYS.intersection(record):return False
    if record.get('note_id') and 'comments_count' in record and 'shared_count' in record:return True  # Native Weibo export has no title.
    return bool(NOTE_KEYS.intersection(record)) and 'text' not in record or 'mblog' in record

def read_payload(content: bytes, filename: str):
    if len(content) > 20 * 1024 * 1024:
        raise ImportErrorDetail('文件超过 20 MB，请拆分导入')
    if filename.lower().endswith('.xlsx'):
        from openpyxl import load_workbook
        import zipfile
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                if sum(x.file_size for x in z.infolist()) > 100 * 1024 * 1024:
                    raise ImportErrorDetail('Excel 解压内容超过 100 MB')
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            result = {'notes': [], 'comments': []}
            for sheet in workbook:
                rows = sheet.values
                header = next(rows, [])
                # All current upstream comment sheets include comment_id. Weibo note
                # sheets only have content/note_id, without a title column.
                kind = 'comments' if COMMENT_KEYS.intersection(header) else 'notes'
                for row in rows:
                    result[kind].append(dict(zip(header, row)))
                    if len(result[kind]) > 10000:
                        raise ImportErrorDetail('最多导入 10000 条评论/笔记')
            workbook.close()
            return result
        except ImportErrorDetail:
            raise
        except Exception as exc:
            raise ImportErrorDetail('无法读取 Excel，请使用 MediaCrawler 导出的 .xlsx') from exc
    try:
        text = content.decode('utf-8-sig')
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            result = []
            for index, line in enumerate(text.splitlines(), 1):
                if line.strip():
                    try:
                        result.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        raise ImportErrorDetail(f'第 {index} 行不是有效 JSON') from exc
        return result
    except UnicodeDecodeError as exc:
        raise ImportErrorDetail('请使用 UTF-8 编码的 JSON/JSONL 文件') from exc

def split_payload(payload):
    if isinstance(payload, dict):
        if payload.get('kind') in ('note','comment') and 'data' in payload:return split_payload([payload])
        if 'comments' in payload or 'notes' in payload:
            notes, comments = payload.get('notes', []), payload.get('comments', [])
        elif is_note(payload):
            notes, comments = [payload], []
        elif COMMENT_KEYS.intersection(payload) or 'text' in payload or 'content' in payload:
            notes, comments = [], [payload]
        else:
            raise ImportErrorDetail('JSON 对象需包含 comments/notes 数组，或为单条评论')
    elif isinstance(payload, list):
        notes, comments = [], []
        for item in payload:
            if not isinstance(item, dict):
                raise ImportErrorDetail('每条记录必须是 JSON 对象')
            value=item.get('data',item)
            if not isinstance(value,dict):raise ImportErrorDetail('每条记录的 data 必须是对象')
            if item.get('platform') and 'platform' not in value:value={**value,'platform':item['platform']}
            if item.get('kind')=='note' or (item.get('kind')!='comment' and is_note(value)):
                notes.append(value)
            else:
                comments.append(value)
    else:
        raise ImportErrorDetail('JSON 顶层需为对象或数组')
    if not isinstance(notes, list) or not isinstance(comments, list) or len(notes) > 10000 or len(comments) > 10000:
        raise ImportErrorDetail('notes/comments 需为数组，每类最多 10000 条')
    if any(not isinstance(x, dict) for x in notes+comments):
        raise ImportErrorDetail('每条笔记和评论必须是对象')
    return notes, comments

def string(value):
    return str(value) if value is not None and value != '' else None

def timestamp(value):
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        try:
            n = float(value)
            return datetime.fromtimestamp(n / 1000 if n > 1e11 else n, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return str(value)
    return str(value)

def keywords(record):
    values = record.get('source_keywords') or record.get('source_keyword') or []
    return [str(x) for x in values] if isinstance(values, list) else [str(values)]

def number(value):
    try:
        return int(value) if value is not None and value != '' else None
    except (TypeError, ValueError):
        return None

def ingest(task_id, payload, file_platform=None):
    notes, records = split_payload(payload)
    selected=db.task(task_id)['config'].get('platforms',['xhs'])
    default_platform=(payload.get('platform') if isinstance(payload,dict) else None) or file_platform
    default_platform=platform_id(default_platform or (selected[0] if len(selected)==1 else ''))
    def resolve(r):
        platform=platform_id(r.get('platform') or default_platform)
        if not platform:raise ImportErrorDetail('多平台导入必须为每条记录标记 platform，或在文件顶层指定 platform')
        if platform not in PLATFORMS:raise ImportErrorDetail('不支持的平台：'+platform)
        if platform not in selected:raise ImportErrorDetail('记录平台未在任务中选择：'+platform)
        return platform
    stamp = db.now()
    with db.connect() as c:
        for n in notes:
            original=n; platform=resolve(n); n=normalize_note(platform,n)
            c.execute('INSERT INTO raw_records(task_id,kind,data,collected_at,platform) VALUES(?,?,?,?,?)', (task_id, 'note', db.dumps(original), stamp,platform))
            nid = string(n.get('note_id') or n.get('id'))
            if not nid:
                continue
            note = {'platform':platform,'note_id': nid, 'title': string(n.get('title')), 'body': string(n.get('body') or n.get('desc')),
                    'source_url': string(n.get('source_url') or n.get('note_url')), 'source_keywords': keywords(n)}
            existing = c.execute('SELECT data FROM notes WHERE task_id=? AND platform=? AND note_id=?', (task_id,platform,nid)).fetchone()
            if existing:
                old = json.loads(existing[0])
                note['source_keywords'] = list(dict.fromkeys(old.get('source_keywords', []) + note['source_keywords']))
            c.execute('INSERT OR REPLACE INTO notes VALUES(?,?,?,?)', (task_id,platform,nid,db.dumps(note)))
        for i, r in enumerate(records):
            if 'text' not in r and 'content' not in r:
                raise ImportErrorDetail(f'第 {i+1} 条评论缺少 text/content 字段')
            original=r; platform=resolve(r); r=normalize_comment(platform,r)
            text = r.get('text', r.get('content'))
            if text is None and ('text' in r or 'content' in r):
                text = ''  # Image-only/deleted-text comments remain as raw evidence, never invented.
            if not isinstance(text, str):
                raise ImportErrorDetail(f'第 {i+1} 条评论缺少字符串 text/content 字段')
            if len(text) > 20000:
                raise ImportErrorDetail(f'第 {i+1} 条评论超过 20000 字')
            cur = c.execute('INSERT INTO raw_records(task_id,kind,data,collected_at,platform) VALUES(?,?,?,?,?)', (task_id, 'comment', db.dumps(original), stamp,platform))
            cid = string(r.get('comment_id') or r.get('id'))
            # Missing IDs are retained separately. Do not guess identity using text or a note URL.
            evidence_id = f'{platform}:{cid}' if cid else f'{platform}:missing:{cur.lastrowid}'
            item = {'platform': platform, 'comment_id': cid, 'note_id': string(r.get('note_id')),
                    'user_id':string(r.get('user_id')),'parent_relation_known':bool(r.get('parent_relation_known')),
                    'root_comment_id':string(r.get('root_comment_id')),
                    'text': text, 'parent_comment_id': string(r.get('parent_comment_id')),
                    'parent_text': string(r.get('parent_text')), 'note_title': string(r.get('note_title')), 'note_body': string(r.get('note_body')),
                    'published_at': timestamp(r.get('published_at', r.get('create_time'))),
                    'like_count': number(r.get('like_count')), 'source_url': string(r.get('source_url') or r.get('note_url')),
                    'source_keywords': keywords(r), 'collected_at': timestamp(r.get('collected_at') or r.get('last_modify_ts')) or stamp,
                    'missing_id': not bool(cid)}
            existing = c.execute('SELECT data FROM comments WHERE task_id=? AND evidence_id=?', (task_id, evidence_id)).fetchone()
            if existing:
                old = json.loads(existing[0])
                old['source_keywords'] = list(dict.fromkeys(old.get('source_keywords', []) + item['source_keywords']))
                c.execute('UPDATE comments SET data=? WHERE task_id=? AND evidence_id=?', (db.dumps(old), task_id, evidence_id))
            else:
                c.execute('INSERT INTO comments(task_id,evidence_id,platform,comment_id,note_id,data) VALUES(?,?,?,?,?,?)', (task_id, evidence_id, platform, cid, item['note_id'], db.dumps(item)))
    return len(records)

"""Run an actual configured-model acceptance check using the local real export.

Requires the loopback app to be running. Does not print credentials or provider bodies.
Keeps the earlier rules report/task intact and records its own task ID for resuming checks.
"""
import argparse
import csv
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'md/data/xhs/xhs_search_20261029_203212.xlsx'
STATE = ROOT / 'runtime/acceptance/deepseek-run.json'

def say(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)

def check(client, path, **kwargs):
    response = client.post(path, **kwargs)
    if not response.is_success:
        raise RuntimeError(f'Local API {response.status_code}: {response.json().get("detail", "failed")}')
    return response.json()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--resume', action='store_true', help='Keep polling the recorded task instead of starting another run')
    parser.add_argument('--reanalyze', action='store_true', help='Explicitly retry the recorded task using its saved data')
    parser.add_argument('--report-only', action='store_true', help='Regenerate only a completed, unmodified report')
    args = parser.parse_args()
    client = httpx.Client(base_url='http://127.0.0.1:8765', timeout=200, trust_env=False)
    settings = client.get('/api/settings').json()
    started = time.monotonic()
    if args.resume or args.reanalyze or args.report_only:
        saved = json.loads(STATE.read_text(encoding='utf-8'))
        task_id = saved['task_id']
        if args.reanalyze:
            check(client, f'/api/tasks/{task_id}/analyze', json={'analysis_mode':'ai'})
        elif args.report_only:
            check(client, f'/api/tasks/{task_id}/report/regenerate')
    else:
        check(client, '/api/settings/test')
        task = check(client, '/api/tasks', json={'product':'飞书','description':'字节跳动旗下协作与办公软件','focus':'综合用户体验','source':'import','analysis_mode':'ai'})
        task_id = task['id']
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({'task_id':task_id,'created_at':datetime.now(timezone.utc).isoformat(),'model':settings['model']},ensure_ascii=False,indent=2),encoding='utf-8')
        check(client, f'/api/tasks/{task_id}/import', content=SOURCE.read_bytes(), headers={'X-Filename':SOURCE.name,'Content-Type':'application/octet-stream'})
        check(client, f'/api/tasks/{task_id}/analyze', json={'analysis_mode':'ai'})
    say({'task_id':task_id,'model':settings['model'],'source':'real_existing_xlsx','mode':'actual_ai'})
    previous = None
    while True:
        task = client.get('/api/tasks/'+task_id).json()
        state = (task['status'],task['analyzed_count'],task['stage'])
        if state != previous:
            say({'status':state[0],'analyzed_count':state[1],'total':task['stats']['unique'],'stage':state[2],'elapsed_seconds':round(time.monotonic()-started,1)})
            previous = state
        if task['status'] not in ('queued','collecting','cleaning','analyzing','reporting','waiting_login'):
            break
        time.sleep(2)
    if task['status'] not in ('completed','partial'):
        say({'result':'failed','task_id':task_id,'error':task['error']})
        raise SystemExit(1)
    report = task['report']
    assert report['analysis_mode']=='ai' and report['model']==settings['model']
    all_comments = []
    for offset in range(0,task['stats']['unique'],200):
        response = client.get(f'/api/tasks/{task_id}/comments', params={'offset':offset,'limit':200})
        response.raise_for_status()
        all_comments.extend(response.json()['items'])
    by_id = {x['evidence_id']:x for x in all_comments}
    allowed = {i for i,x in by_id.items() if x['effective'].get('relevance')=='target' and not x['effective'].get('excluded') and not x['effective'].get('suspected_ad')}
    assert set(report['evidence'])==allowed
    for name in ('findings','positives','recommendations','questions'):
        for claim in report[name]:
            assert claim['evidence_ids'] and set(claim['evidence_ids'])<=allowed
    for theme in report['themes']:
        assert set(theme['evidence_ids'])<=allowed
        assert theme['count']==len(set(theme['evidence_ids']))
        assert theme['share']==round(theme['count']/len(allowed)*100,1)
    for x in all_comments:
        if x['analysis']:
            assert x['analysis']['quote'] in x['text']
    out = ROOT/'examples'
    for fmt in ('md','csv','html','json'):
        response = client.get(f'/api/tasks/{task_id}/export/{fmt}')
        response.raise_for_status()
        (out/f'feishu-real-import-deepseek.{fmt}').write_bytes(response.content)
    rows = list(csv.DictReader(io.StringIO((out/'feishu-real-import-deepseek.csv').read_text(encoding='utf-8-sig'))))
    assert len(rows)==task['stats']['unique']
    assert sum(x['included']=='True' for x in rows)==len(allowed)
    verification = {'checked_at':datetime.now(timezone.utc).isoformat(),'task_id':task_id,'model':report['model'],
                    'check_kind':'report_regeneration' if args.report_only else 'resume_existing' if args.resume else 'full_analysis',
                    'source_file':SOURCE.name,'source_kind':'real_existing_export','actual_model_call':True,
                    'stats':task['stats'],'theme_count':len(report['themes']),
                    'claim_counts':{k:len(report[k]) for k in ('findings','positives','recommendations','questions')},
                    'export_rows':len(rows),'all_evidence_ids_exist':True,'all_quotes_match_raw_text':True,
                    'theme_counts_programmatically_verified':True,'quality_accuracy':'not_measured_without_human_gold_labels',
                    'support_check':{k:report.get('support_check',{}).get(k) for k in ('method','checked','retained','removed')} if report.get('support_check') else None,
                    'report_url':f'http://127.0.0.1:8765/#/tasks/{task_id}/report','elapsed_seconds_this_check':round(time.monotonic()-started,1)}
    (out/'verification-deepseek.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
    say({'result':'passed',**verification})

if __name__=='__main__':
    main()

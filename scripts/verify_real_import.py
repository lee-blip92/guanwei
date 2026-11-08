"""Exercise the running loopback service with the existing local MediaCrawler export.

This is a real imported-data check, not a fresh crawl and not a model-quality evaluation.
"""
import csv
import io
import json
import time
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'md/data/xhs/xhs_search_20261029_203212.xlsx'

def main():
    client=httpx.Client(base_url='http://127.0.0.1:8765',timeout=40,trust_env=False)
    client.get('/api/health').raise_for_status()
    existing=client.get('/api/tasks').json()
    task=next((t for t in existing if t['config']['source']=='import' and t['config']['product']=='飞书' and (t['source_name']==SOURCE.name or t['stats']['raw']==0)),None)
    if not task:
        task=client.post('/api/tasks',json={'product':'飞书','description':'字节跳动旗下协作与办公软件','source':'import','analysis_mode':'rules','focus':'综合用户体验'}).json()
    tid=task['id']
    if not task.get('stats',{}).get('raw'):
        response=client.post(f'/api/tasks/{tid}/import',content=SOURCE.read_bytes(),headers={'X-Filename':SOURCE.name,'Content-Type':'application/octet-stream'})
        assert response.is_success,response.text
    response=client.post(f'/api/tasks/{tid}/analyze');response.raise_for_status()
    for _ in range(300):
        task=client.get('/api/tasks/'+tid).json()
        if task['status'] not in ('queued','cleaning','analyzing','reporting'):
            break
        time.sleep(.1)
    assert task['status']=='completed',task.get('error')
    report=task['report']
    out=ROOT/'examples';out.mkdir(exist_ok=True)
    for fmt in ('md','csv','html','json'):
        result=client.get(f'/api/tasks/{tid}/export/{fmt}');result.raise_for_status()
        (out/f'feishu-real-import-rules.{fmt}').write_bytes(result.content)
    rows=list(csv.DictReader(io.StringIO((out/'feishu-real-import-rules.csv').read_text(encoding='utf-8-sig'))))
    assert len(rows)==task['stats']['unique']
    assert sum(r['included']=='True' for r in rows)==task['stats']['included']
    known=set(report['evidence'])
    assert all(set(c['evidence_ids'])<=known for k in ('findings','positives','recommendations','questions') for c in report[k])
    assert all(set(t['evidence_ids'])<=known and t['count']==len(set(t['evidence_ids'])) for t in report['themes'])
    verification={'task_id':tid,'source':str(SOURCE),'source_kind':'real_existing_export','model_mode':'rules_no_ai_call',
                  'status':task['status'],'stats':task['stats'],'themes':len(report['themes']),
                  'export_rows':len(rows),'report_evidence_ids_valid':True,'web_url':f'http://127.0.0.1:8765/#/tasks/{tid}/report'}
    (out/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(verification,ensure_ascii=True,indent=2))

if __name__=='__main__':main()

"""Create a deterministic small review sample and compute metrics only from filled human labels."""
import argparse
import csv
import io
import json
import random
from pathlib import Path
import httpx

def prepare(task_id, output):
    client=httpx.Client(base_url='http://127.0.0.1:8765',trust_env=False)
    rows=[]
    for offset in range(0,10000,200):
        page=client.get(f'/api/tasks/{task_id}/comments',params={'offset':offset,'limit':200})
        page.raise_for_status();payload=page.json();rows.extend(payload['items'])
        if len(rows)>=payload['total']:break
    rng=random.Random(20261029)
    groups={key:[] for key in ('target','uncertain','other_product','irrelevant')}
    for x in rows:groups.setdefault(x['effective'].get('relevance','uncertain'),[]).append(x)
    chosen=[]
    for group in groups.values():
        rng.shuffle(group);chosen.extend(group[:10])
    fields=['evidence_id','text','note_title','note_body','parent_text','source_url','predicted_relevance','predicted_themes','gold_relevance','gold_themes','reviewer','reason']
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fields);writer.writeheader()
        for x in chosen:
            writer.writerow({**{k:x.get(k) for k in fields[:6]},'predicted_relevance':x['effective'].get('relevance'),'predicted_themes':'|'.join(x['effective'].get('themes',[]))})
    print(json.dumps({'sample_rows':len(chosen),'labelled_rows':0,'metrics':None,'file':str(output),'note':'分层核查样本，不能直接作为总体准确率；gold 字段需人工填写'},ensure_ascii=True))

def score(source):
    with source.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    labelled=[x for x in rows if x.get('gold_relevance') in ('target','uncertain','other_product','irrelevant')]
    if not labelled:
        print(json.dumps({'labelled_rows':0,'metrics':None,'note':'尚无人工作出标注，不计算效果指标'},ensure_ascii=True));return
    tp=sum(x['gold_relevance']=='target' and x['predicted_relevance']=='target' for x in labelled)
    fp=sum(x['gold_relevance']!='target' and x['predicted_relevance']=='target' for x in labelled)
    fn=sum(x['gold_relevance']=='target' and x['predicted_relevance']!='target' for x in labelled)
    themes=[x for x in labelled if x.get('gold_themes','').strip()]
    def terms(text):return {t.strip() for t in text.split('|') if t.strip()}
    jaccard=[len(terms(x['gold_themes'])&terms(x['predicted_themes']))/len(terms(x['gold_themes'])|terms(x['predicted_themes'])) for x in themes]
    result={'labelled_rows':len(labelled),'counts':{'tp':tp,'fp':fp,'fn':fn},
            'sample_precision':tp/(tp+fp) if tp+fp else None,'sample_recall':tp/(tp+fn) if tp+fn else None,
            'theme_labelled_rows':len(themes),'theme_jaccard':sum(jaccard)/len(jaccard) if jaccard else None,
            'scope':'仅本次已标注分层核查样本；不是总体效果指标'}
    print(json.dumps(result,ensure_ascii=True,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('prepare');p.add_argument('task_id');p.add_argument('--output',default='runtime/evaluation/review.csv')
    p=sub.add_parser('score');p.add_argument('file')
    args=parser.parse_args()
    if args.action=='prepare':prepare(args.task_id,Path(args.output))
    else:score(Path(args.file))

"""Prepare reproducible V3 review worksheets; never invent human gold labels."""
import argparse
import json
import random
from pathlib import Path
import httpx


def write_rows(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task_id')
    parser.add_argument('--base-url',default='http://127.0.0.1:8765')
    parser.add_argument('--prefix',default='runtime/evaluation/decision-v3')
    args=parser.parse_args()
    with httpx.Client(base_url=args.base_url,trust_env=False,timeout=30) as client:
        response=client.get('/api/tasks/'+args.task_id);response.raise_for_status();task=response.json()
        report=task['report']
        if not report or report.get('decision_version')!=3:raise SystemExit('需要已生成的V3报告')
        items=[]
        while len(items)<task['stats']['unique']:
            response=client.get('/api/tasks/'+args.task_id+'/comments',params={'offset':len(items),'limit':200})
            response.raise_for_status();page=response.json()['items']
            if not page:raise SystemExit('分页记录不完整')
            items.extend(page)
    rng=random.Random(20261030)
    strata={key:[] for key in ('target','uncertain','other_product','irrelevant')}
    for item in items:strata.setdefault(item['effective'].get('relevance') or 'pending',[]).append(item)
    selected={}
    for key,group in strata.items():
        rng.shuffle(group)
        for item in group[:8]:selected[item['evidence_id']]={'stratum':key,'item':item}
    by={item['evidence_id']:item for item in items}
    for issue in report['decision']['issues'][:6]:
        for eid in issue['representative_ids']+issue['opposing_positive_ids'][:1]:
            if eid not in selected and len(selected)<40:selected[eid]={'stratum':'priority_or_divergence','item':by[eid]}
    shared={'task_id':args.task_id,'report_version':task['version'],'model':report['model'],'gold_status':'unlabelled'}
    comment_rows=[]
    for eid,value in selected.items():
        item=value['item'];effective=item['effective']
        comment_rows.append({**shared,'evidence_id':eid,'sampling_stratum':value['stratum'],
            'text':item['text'],'parent_text':item.get('parent_text'),'note_title':item.get('note_title'),
            'note_body':item.get('note_body'),'source_url':item.get('source_url'),
            'predicted_relevance':effective.get('relevance'),'predicted_reason':effective.get('reason'),
            'predicted_points':effective.get('points',[]),
            'taxonomy_assignments':[{'unit_id':u['unit_id'],'domain_id':u.get('domain_id'),'category_id':u.get('category_id')} for u in report['opinion_points'] if u['evidence_id']==eid],
            'gold_relevance':None,'gold_points':None,'gold_taxonomy_assignments':None,'reviewer':None,'reason':None})
    claims=[]
    for index,claim in enumerate(report['findings']):claims.append(('finding:'+str(index),claim))
    for issue in report['decision']['issues'][:6]:
        for field in ('observation','deeper_need','direction','validation'):
            if issue.get(field):claims.append((issue['id']+':'+field,issue[field]))
        for index,claim in enumerate(issue['cause_hypotheses']):claims.append((issue['id']+':cause:'+str(index),claim))
    claim_rows=[{**shared,'claim_id':key,**claim,'evidence':{eid:report['evidence'][eid] for eid in claim['evidence_ids']},
        'gold_support':None,'gold_overclaim':None,'reviewer':None,'reason':None} for key,claim in claims]
    priority_rows=[{**shared,'issue_id':issue['id'],'name':issue['name'],'priority':issue['priority'],
        'counts':{key:issue[key] for key in ('count','point_count','thread_count','known_user_count','detailed_direct_count','agreement_count')},
        'evidence':{eid:report['evidence'][eid] for eid in issue['evidence_ids']},
        'gold_priority_appropriate':None,'gold_severity_supported':None,'reviewer':None,'reason':None} for issue in report['decision']['issues']]
    for suffix,rows in (('comments',comment_rows),('claims',claim_rows),('priorities',priority_rows)):
        write_rows(Path(args.prefix+'.'+suffix+'.jsonl'),rows)
    print(json.dumps({'comment_rows':len(comment_rows),'claim_rows':len(claim_rows),'priority_rows':len(priority_rows),
        'labelled_rows':0,'metrics':None,'note':'固定种子的分层与边界核查样本；gold字段留空，不代表随机总体评估'},ensure_ascii=False))


if __name__=='__main__':main()

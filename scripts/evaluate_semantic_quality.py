"""Repeatable source/statistics/error-type checks; human metrics only from explicit gold.

No model requests, no collection and no task mutations.
"""
import argparse
import json
import sys
from pathlib import Path
from collections import Counter
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from insight.semantic_quality import normalized

KNOWN_CASES=[('权限审批与附件下载必须分开','dy:7647221926028493609','xhs:691d43c00000000039000aee'),
             ('画板流畅不能反驳保存入口困难','xhs:690a1b95000000003902802e','xhs:6aa65d82000000000a01f266')]

def evaluate(report,labels=None):
    units=report['opinion_points'];domains={d['id']:d['name'] for d in report['aspects']}
    by={u['unit_id']:{**u,'domain_name':domains.get(u.get('domain_id'))} for u in units};d=report['decision'];stats=report['stats'];evidence=set(report['evidence'])
    checks={
        'status_partition':sum(stats[k] for k in ('included','excluded','uncertain','pending'))==stats['unique'],
        'raw_dedup_balance':stats['raw']==stats['unique']+stats['duplicates'],
        'mapping_balance':sum(bool(u.get('category_id')) for u in units)+len(d['unmapped_unit_ids'])==len(units),
        'unique_units':len(by)==len(units),
        'unit_evidence_ids':all(u['evidence_id'] in evidence for u in units),
        'claim_evidence_ids':all(set(c['evidence_ids'])<=evidence for field in ('findings','positives','recommendations','questions') for c in report[field]),
        'reason_partition':sum(x['count'] for x in d['reason_summary'])==stats['unique']-stats['included'],
        'theme_coverage':all(len(set(t['evidence_ids']))==t['count'] for t in report['themes'])}
    duplicates=[]
    for u in units:
        values=[normalized(u.get(k)) for k in ('scenario','manifestation','impact','expected_outcome') if u.get(k)]
        if len(values)!=len(set(values)):duplicates.append(u['unit_id'])
    cases=[]
    for name,left,right in KNOWN_CASES:
        a={u['category_id'] for u in units if u['evidence_id']==left and u.get('category_id')};b={u['category_id'] for u in units if u['evidence_id']==right and u.get('category_id')}
        cases.append({'name':name,'left':left,'right':right,'result':'separate' if a and b and not a&b else 'shared_issue' if a&b else 'inconclusive','shared_issue_ids':sorted(a&b)})
    labelled=[]
    for row in labels or []:
        if row.get('reviewer') and row.get('gold_points') is not None:
            labelled.extend(row['gold_points'])
    metrics=None
    if labelled:
        matched=[(g,by.get(g['unit_id'])) for g in labelled]
        def accuracy(field):
            values=[(g,p) for g,p in matched if g.get(field) is not None]
            correct=sum(p is not None and p.get(field)==g[field] for g,p in values)
            return {'correct':correct,'labelled':len(values),'accuracy':correct/len(values)} if values else None
        tp=fp=fn=0
        for gold,pred in matched:
            if gold.get('intents') is None:continue
            g=set(gold['intents']);p=set((pred or {}).get('intents',[]));tp+=len(g&p);fp+=len(p-g);fn+=len(g-p)
        precision=tp/(tp+fp) if tp+fp else None;recall=tp/(tp+fn) if tp+fn else None
        metrics={'sentiment':accuracy('sentiment'),'dimension':accuracy('domain_name'),
            'intent_micro':{'tp':tp,'fp':fp,'fn':fn,'precision':precision,'recall':recall,
                'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None}}
        pair_tp=pair_fp=pair_fn=pair_count=0
        grouped=[(g,p) for g,p in matched if g.get('issue_label') is not None]
        for i,(ga,pa) in enumerate(grouped):
            for gb,pb in grouped[i+1:]:
                expected=ga['issue_label']==gb['issue_label'];predicted=bool(pa and pb and pa.get('category_id') and pa.get('category_id')==pb.get('category_id'))
                pair_tp+=expected and predicted;pair_fp+=predicted and not expected;pair_fn+=expected and not predicted;pair_count+=1
        metrics['issue_pairwise']={'labelled_pairs':pair_count,'tp':pair_tp,'fp':pair_fp,'fn':pair_fn,
            'precision':pair_tp/(pair_tp+pair_fp) if pair_tp+pair_fp else None,'recall':pair_tp/(pair_tp+pair_fn) if pair_tp+pair_fn else None}
    return {'statistics_checks':checks,'regression_cases':cases,'duplicate_role_fields':duplicates,
        'opinion_count':len(units),'unmapped_opinions':len(d['unmapped_unit_ids']),'generated_claims_needing_review':len(d.get('generated_review',[])),
        'human_labelled_points':len(labelled),'human_metrics':metrics,
        'not_accuracy':True,'note':'结构/统计与已知错误回归；不据模型自评或程序通过率宣称人工分类准确率。'}

def main():
    p=argparse.ArgumentParser();p.add_argument('task_id');p.add_argument('--report');p.add_argument('--before');p.add_argument('--labels',default='examples/evaluation/semantic-real-comments.jsonl');p.add_argument('--output',default='runtime/evaluation/semantic-quality-result.json');a=p.parse_args()
    r=json.loads(Path(a.report).read_text(encoding='utf-8')) if a.report else httpx.get('http://127.0.0.1:8765/api/tasks/'+a.task_id,trust_env=False).json()['report']
    if not r:raise SystemExit('报告尚未生成')
    labels=[json.loads(line) for line in Path(a.labels).read_text(encoding='utf-8').splitlines() if line.strip()] if Path(a.labels).exists() else []
    result={'task_id':a.task_id,'after':evaluate(r,labels)}
    if a.before:result['before']=evaluate(json.loads(Path(a.before).read_text(encoding='utf-8')),labels)
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
    if not all(result['after']['statistics_checks'].values()):raise SystemExit(1)

if __name__=='__main__':main()

"""Read-only acceptance of an already completed real AI task; never starts collection/model calls."""
import argparse
import csv
import io
import json
from pathlib import Path
import httpx

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('task_id')
    parser.add_argument('--base-url',default='http://127.0.0.1:8765')
    parser.add_argument('--output-prefix',default='examples/codex-aspect-v2-real')
    args=parser.parse_args()
    with httpx.Client(base_url=args.base_url,timeout=30,trust_env=False) as client:
        task=client.get('/api/tasks/'+args.task_id).json()
        if task['status'] not in ('completed','partial'):
            raise SystemExit('Task not complete: '+task['status']+' / '+task['stage'])
        report=task['report'];assert report['analysis_version']==2 and report['analysis_mode']=='ai'
        rows=[]
        while len(rows)<task['stats']['unique']:
            response=client.get('/api/tasks/'+args.task_id+'/comments',params={'offset':len(rows),'limit':200});response.raise_for_status()
            batch=response.json()['items'];assert batch
            rows.extend(batch)
        by_id={x['evidence_id']:x for x in rows};evidence=report['evidence'];included=set(evidence)
        assert len(rows)==task['stats']['unique']==report['stats']['unique']
        assert len(included)==report['stats']['included']
        units=report['opinion_points'];assert len({u['unit_id'] for u in units})==report['opinion_count']
        for u in units:
            row=by_id[u['evidence_id']];assert u['product_scope']=='target' and u['quote'] in row['text']
            assert u['evidence_id'] in included and not row['effective'].get('excluded')
            sources=[row['text']]
            if u['basis']=='agreement':
                assert u['context_quote'] and u['context_quote'] in (row.get('parent_text') or '')
                sources.append(row.get('parent_text') or '')
            for field in ('scenario','impact','expected_outcome'):
                assert not u.get(field) or any(u[field] in source for source in sources),(u['unit_id'],field)
        for section in ('themes','aspects','keywords','pain_points','needs','strengths'):
            for group in report[section]:
                ids=set(group['evidence_ids']);assert ids<=included and len(ids)==group['count']
                assert group['share']==round(len(ids)/len(included)*100,1)
        for section in ('findings','positives','recommendations','questions'):
            for claim in report[section]:assert claim['evidence_ids'] and set(claim['evidence_ids'])<=included
        for theme in report['themes']:
            assert set(theme.get('recommendation_evidence_ids',[]))<=set(theme['evidence_ids'])
            assert set(theme.get('validation_evidence_ids',[]))<=set(theme['evidence_ids'])
            if theme.get('unit_ids'):
                members=[u for u in units if u['unit_id'] in theme['unit_ids']]
                assert len(members)==theme['point_count'] and {u['evidence_id'] for u in members}==set(theme['evidence_ids'])
        prefix=Path(args.output_prefix);prefix.parent.mkdir(parents=True,exist_ok=True)
        exported={}
        for fmt,suffix in [('md','.md'),('csv','.csv'),('html','.html'),('json','.json')]:
            response=client.get('/api/tasks/'+args.task_id+'/export/'+fmt);response.raise_for_status()
            prefix.with_suffix(suffix).write_text(response.text,encoding='utf-8');exported[fmt]=len(response.content)
        csv_rows=list(csv.DictReader(io.StringIO(prefix.with_suffix('.csv').read_text(encoding='utf-8-sig'))))
        assert len(csv_rows)==report['stats']['unique'] and sum(x['included']=='True' for x in csv_rows)==len(included)
        assert all('opinion_points_json' in x for x in csv_rows)
        # A blank gold worksheet. This is a stratified pilot sample, not an evaluated gold set.
        selected=[];seen=set()
        for cls in ('information_only','non_feedback','uncertain','feedback'):
            choices=[x for x in rows if (x['analysis'] or {}).get('opinion_audit',{}).get('content_class')==cls][:3]
            for row in choices:
                if row['evidence_id'] not in seen:selected.append(row);seen.add(row['evidence_id'])
        template=[{'product':task['config']['product'],'evidence_id':x['evidence_id'],'text':x['text'],
            'parent_text':x.get('parent_text'),'note_title':x.get('note_title'),'reviewer':'',
            'gold_relevance':None,'gold_content_class':None,'gold_points':None,'notes':''} for x in selected]
        prefix.with_suffix('.review-template.jsonl').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in template)+'\n',encoding='utf-8')
        summary={'task_id':args.task_id,'source':'用户已有真实采集数据，仅复用分析；未重新采集','product':report['product'],
            'model':report['model'],'engine':report['engine'],'stats':report['stats'],'opinion_count':report['opinion_count'],
            'generation_mode':report.get('generation_mode'),'provider_issue':report.get('provider_issue'),
            'previous_generated_at':report.get('previous_generated_at'),'current_status':task['status'],
            'new_model_call_in_recount':False if report.get('generation_mode')=='cached_narrative_recount' else None,
            'aspect_count':len(report['aspects']),'topic_count':len(report['themes']),'pain_point_count':len(report['pain_points']),
            'strength_count':len(report['strengths']),'need_count':len(report['needs']),'evidence_count':len(evidence),
            'csv_rows':len(csv_rows),'csv_included':sum(x['included']=='True' for x in csv_rows),
            'quote_and_literal_fields_verified':True,'statistics_recomputed':True,'exports_bytes':exported,
            'audit_removed_candidates':report['quality']['audit_removed_point_count'],
            'blank_review_template_rows':len(template),'not_accuracy_evaluation':True}
        assisted_path=Path('examples/acceptance/aspect-v2-assisted-review.json')
        if assisted_path.exists():
            assisted=json.loads(assisted_path.read_text(encoding='utf-8'))
            if assisted.get('task_id')==args.task_id:
                summary['assisted_review_count']=assisted['reviewed_count']
                summary['assisted_review_method']=assisted['method']
        prefix.with_suffix('.verification.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:summary[k] for k in ('engine','opinion_count','aspect_count','topic_count','pain_point_count','strength_count','need_count','evidence_count','csv_rows')},ensure_ascii=False))

if __name__=='__main__':main()

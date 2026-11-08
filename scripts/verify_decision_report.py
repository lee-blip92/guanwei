"""Read-only V3 acceptance: recompute every statistic and source relation; export both views."""
import argparse,csv,io,json,sys
from html.parser import HTMLParser
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from insight.decision_state import thread_keys,eligible,satisfaction,problem
from insight.decision_analysis import summarize,priority

class EvidenceAnchors(HTMLParser):
    def __init__(self):super().__init__();self.targets=set();self.links=set()
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if values.get('id'):self.targets.add(values['id'])
        if values.get('href','').startswith('#'):self.links.add(values['href'][1:])

def main():
    p=argparse.ArgumentParser();p.add_argument('task_id');p.add_argument('--base-url',default='http://127.0.0.1:8765');p.add_argument('--prefix',default='examples/feishu-decision-v3-real');a=p.parse_args()
    with httpx.Client(base_url=a.base_url,trust_env=False,timeout=30) as c:
        t=c.get('/api/tasks/'+a.task_id).json();assert t['status'] in ('completed','partial'),t.get('error');r=t['report'];assert r['decision_version']==3
        rows=[]
        while len(rows)<t['stats']['unique']:
            batch=c.get('/api/tasks/'+a.task_id+'/comments',params={'offset':len(rows),'limit':200}).json()['items'];assert batch;rows+=batch
        by={x['evidence_id']:x for x in rows};threads=thread_keys(rows);units=r['opinion_points'];evidence=set(r['evidence']);n=len(evidence)
        assert n==t['stats']['included']==r['stats']['included']
        assert len(units)==r['opinion_count']==len({u['unit_id'] for u in units})
        for u in units:
            x=by[u['evidence_id']];assert eligible(u) and not x['effective'].get('excluded') and u['quote'] in x['text']
            sources=[x['text']]+([x.get('parent_text') or ''] if u['basis']=='agreement' and not x.get('parent_blocked') else [])
            if u['basis']=='agreement':assert u['context_quote'] in (x.get('parent_text') or '')
            for k in ('scenario','manifestation','impact','expected_outcome','severity_quote','group_quote'):assert not u.get(k) or any(u[k] in s for s in sources),(u['unit_id'],k)
        fields=('count','point_count','share','denominator','thread_count','thread_unknown_comments','known_user_count','user_id_comments','direct_count','detailed_direct_count','agreement_count','repeated_agreement_count','positive_count','negative_count','negative_share')
        for collection in ('aspects','themes','pain_points','strengths','needs'):
            for group in r[collection]:
                if collection=='aspects':selected=[u for u in units if u.get('domain_id')==group['id']]
                else:
                    cid=group.get('id') or group['topic_id'];selected=[u for u in units if u.get('category_id')==cid]
                    if collection=='pain_points':selected=[u for u in selected if problem(u)]
                    elif collection=='strengths':selected=[u for u in selected if satisfaction(u)]
                    elif collection=='needs':selected=[u for u in selected if set(u['intents']) & {'feature_request','improvement_suggestion'}]
                computed=summarize(selected,n,by,threads)
                for k in fields:assert computed[k]==group[k],(collection,group['name'],k,computed[k],group[k])
                assert set(group['evidence_ids'])<=evidence
        for i in r['decision']['issues']:
            if i.get('unit_ids'):
                selected=[u for u in units if u['unit_id'] in i['unit_ids']]
                assert all(u.get('category_id')==i['id'] and (problem(u) or set(u['intents']) & {'feature_request','improvement_suggestion'}) for u in selected)
            else:
                selected=[u for u in units if u.get('category_id')==i['id'] and problem(u)]
                if not selected:selected=[u for u in units if u.get('category_id')==i['id'] and set(u['intents']) & {'feature_request','improvement_suggestion'}]
            s=summarize(selected,n,by,threads);assert priority(selected,s,n)==i['priority']
            for k in ('observation','deeper_need','direction','validation'):
                if i[k]:assert set(i[k]['evidence_ids'])<=set(i.get('narrative_evidence_ids',i['representative_ids']))<=evidence
            for h in i['cause_hypotheses']:assert set(h['evidence_ids'])<=set(i.get('narrative_evidence_ids',i['representative_ids']))
        for distribution in r['decision']['intent_distribution']:
            ids={u['evidence_id'] for u in units if distribution['key'] in u['intents']}
            assert len(ids)==distribution['count'] and distribution['denominator']==n and distribution['share']==round(len(ids)/n*100,1)
        assert sum(g['count'] for g in r['decision']['reason_summary'])==len(rows)-n
        assert all(x['status']=='pass' for x in r['decision']['quality_checks'])
        prefix=Path(a.prefix);prefix.parent.mkdir(exist_ok=True,parents=True);sizes={}
        for fmt in ('md','html','csv','json'):
            for view in ('brief','full') if fmt in ('md','html') else ('brief',):
                response=c.get('/api/tasks/'+a.task_id+'/export/'+fmt,params={'view':view});response.raise_for_status()
                path=Path(str(prefix)+('.'+view if fmt in ('md','html') else '')+'.'+fmt);path.write_text(response.text,encoding='utf-8');sizes[fmt+'_'+view]=len(response.content)
                if fmt=='html':
                    anchors=EvidenceAnchors();anchors.feed(response.text);assert anchors.links<=anchors.targets,anchors.links-anchors.targets
        assert sizes['md_brief']<sizes['md_full'] and sizes['html_brief']<sizes['html_full']
        data=list(csv.DictReader(io.StringIO(Path(str(prefix)+'.csv').read_text(encoding='utf-8-sig'))));assert len(data)==len(rows) and sum(x['included']=='True' for x in data)==n
        report={'task_id':a.task_id,'version':t['version'],'model':r['model'],'stats':r['stats'],'opinion_count':len(units),'primary_domains':len(r['aspects']),
            'secondary_topics':len(r['themes']),'unmapped_opinions':len(r['decision']['unmapped_unit_ids']),'known_users':r['decision']['sample_evidence']['known_user_count'],
            'known_threads':r['decision']['sample_evidence']['thread_count'],'csv_rows':len(data),'csv_included':n,'quality_checks':len(r['decision']['quality_checks']),
            'priority_distribution':{key:sum(i['priority']['level']==key for i in r['decision']['issues']) for key in ('P0','P1','P2','P3')},'export_bytes':sizes,
            'statistics_sources_priority_and_literal_evidence_verified':True,'not_independent_accuracy_evaluation':True,'warnings':r['generation_warnings']}
        Path(str(prefix)+'.verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k not in ('warnings','export_bytes','stats')},ensure_ascii=False))

if __name__=='__main__':main()

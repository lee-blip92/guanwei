"""Actual model calls on explicitly synthetic context fixtures; not a quality benchmark."""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from insight.analysis import ModelClient, EXTRACT, validate_extraction, payload_comment
from insight.schemas import Extraction

CASES=[
    {'evidence_id':'fixture:short','text':'我也是','note_title':'飞书上手体验','note_body':'讨论飞书使用体验','parent_text':'飞书权限设置入口太复杂，第一次用找不到。','expected_relevance':'target','expected_sentiment':'negative'},
    {'evidence_id':'fixture:worse','text':'更新后更严重','note_title':'飞书电脑端更新体验','note_body':'讨论飞书电脑端使用体验','parent_text':'飞书最近打开文档经常卡顿和闪退。','expected_relevance':'target','expected_sentiment':'negative'},
    {'evidence_id':'fixture:other','text':'钉钉的通知太吵了','note_title':'飞书与钉钉对比','note_body':'笔记同时讨论飞书和钉钉','parent_text':'','expected_relevance':'other_product'},
    {'evidence_id':'fixture:ambiguous','text':'这个确实不太好用','note_title':'飞书与钉钉对比','note_body':'笔记同时讨论飞书和钉钉，没有具体指代','parent_text':'','expected_relevance':'uncertain'},
    {'evidence_id':'fixture:other-reply','text':'我也是','note_title':'飞书与钉钉对比','note_body':'笔记同时讨论飞书和钉钉','parent_text':'钉钉的通知太吵了，我关掉了。','expected_relevance':'other_product'},
    {'evidence_id':'fixture:ad','text':'飞书会员代充，加微优惠，私信购买。','note_title':'飞书使用体验','note_body':'飞书办公协作','parent_text':'','expected_relevance':'target','expected_ad':True},
]

async def main():
    client=ModelClient()
    result=await client.call(EXTRACT,{'product':'飞书','description':'字节跳动旗下协作软件','focus':'综合用户体验','aliases':[],
                                     'data_source':'人工构造的合成边界用例，非真实平台评论',
                                     'comments':[payload_comment(x) for x in CASES]},Extraction)
    values=validate_extraction(result,CASES)
    by_id={x['evidence_id']:x for x in values}
    checks=[]
    for case in CASES:
        actual=by_id[case['evidence_id']]
        checked={'evidence_id':case['evidence_id'],'relevance_pass':actual['relevance']==case['expected_relevance'],
                 'quote_pass':actual['quote'] in case['text']}
        if 'expected_sentiment' in case:checked['sentiment_pass']=actual['sentiment']==case['expected_sentiment']
        if 'expected_ad' in case:checked['ad_pass']=actual['suspected_ad']==case['expected_ad']
        checked['passed']=all(v for k,v in checked.items() if k.endswith('_pass'))
        checks.append(checked)
    output={'checked_at':datetime.now(timezone.utc).isoformat(),'model':client.settings['model'],'actual_model_call':True,
            'source':'synthetic_engineering_fixtures_not_real_comments','quality_metric':None,
            'note':'这是固定边界用例检查，不代表真实用户反馈的准确率或召回率。',
            'cases':CASES,'outputs':values,'checks':checks,'all_passed':all(x['passed'] for x in checks)}
    (ROOT/'examples/deepseek-context-cases.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'model':client.settings['model'],'source':output['source'],'checks':checks,'all_passed':output['all_passed']},ensure_ascii=True),flush=True)
    if not output['all_passed']:raise SystemExit(1)

if __name__=='__main__':asyncio.run(main())

"""Evaluation bookkeeping using constructed labels, never a reported real-data score."""
import importlib.util
from pathlib import Path

path=Path(__file__).resolve().parents[1]/'scripts/evaluate_semantic_quality.py'
spec=importlib.util.spec_from_file_location('semantic_eval',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def sample():
    units=[{'unit_id':'xhs:1#p1','evidence_id':'xhs:1','category_id':'a','domain_id':'d','sentiment':'negative','intents':['fault_report'],'scenario':None,'impact':None},
        {'unit_id':'xhs:2#p1','evidence_id':'xhs:2','category_id':'b','domain_id':'d','sentiment':'positive','intents':['positive_experience']}]
    return {'opinion_points':units,'aspects':[{'id':'d','name':'测试维度'}],'themes':[{'count':1,'evidence_ids':['xhs:1']},{'count':1,'evidence_ids':['xhs:2']}],
        'evidence':{'xhs:1':{},'xhs:2':{}},'stats':{'raw':2,'unique':2,'included':2,'excluded':0,'uncertain':0,'pending':0,'duplicates':0},
        'decision':{'unmapped_unit_ids':[],'reason_summary':[],'generated_review':[]},'findings':[],'positives':[],'recommendations':[],'questions':[]}

def test_unlabelled_data_cannot_produce_accuracy_or_f1():
    result=module.evaluate(sample(),[{'reviewer':None,'gold_points':None}])
    assert result['human_metrics'] is None and result['human_labelled_points']==0
    assert all(result['statistics_checks'].values())

def test_explicit_reviewed_gold_controls_metrics_and_dimension_lookup():
    labels=[{'reviewer':'constructed-test-reviewer','gold_points':[{'unit_id':'xhs:1#p1','sentiment':'negative','domain_name':'测试维度','intents':['fault_report'],'issue_label':'A'},
        {'unit_id':'xhs:2#p1','sentiment':'negative','domain_name':'测试维度','intents':['feature_request'],'issue_label':'B'}]}]
    result=module.evaluate(sample(),labels);metrics=result['human_metrics']
    assert result['human_labelled_points']==2
    assert metrics['sentiment']=={'correct':1,'labelled':2,'accuracy':.5}
    assert metrics['dimension']['accuracy']==1 and metrics['intent_micro']['tp']==1
    assert metrics['intent_micro']['fp']==1 and metrics['intent_micro']['fn']==1

def test_different_counting_denominators_and_role_duplication_are_detected():
    r=sample();r['stats']['excluded']=1;r['opinion_points'][0].update(scenario='复制内容',impact='复制内容')
    result=module.evaluate(r)
    assert not result['statistics_checks']['status_partition']
    assert result['duplicate_role_fields']==['xhs:1#p1']

from typing import Literal
from pydantic import Field
from .schemas import Strict,Claim,Sentiment,Relevance

Intent=Literal['positive_experience','negative_experience','feature_request','usage_question','fault_report','improvement_suggestion','use_scenario','factual_statement','irrelevant','uncertain']

class ResearchPoint(Strict):
    point_index:int=Field(ge=0,le=7)
    product_scope:Literal['target','other_product','uncertain']
    intents:list[Intent]=Field(min_length=1,max_length=10)
    evaluation_object:Literal['product','feature','tutorial','creator','platform_content','third_party','other','uncertain']
    sentiment:Sentiment
    stance:Literal['support','oppose','neutral','uncertain']
    epistemic:Literal['experience','request','question','information','speculation','uncertain']
    severity:Literal['none','inconvenience','time_cost','task_blocked','data_loss','security_risk','unknown']
    severity_quote:str|None=Field(default=None,max_length=500)
    group_quote:str|None=Field(default=None,max_length=150)
    reason:str=Field(min_length=1,max_length=500)

class ResearchComment(Strict):
    evidence_id:str
    points:list[ResearchPoint]=Field(max_length=8)

class ResearchBatch(Strict):
    items:list[ResearchComment]

class CategoryPlan(Strict):
    name:str=Field(min_length=1,max_length=45)
    definition:str=Field(min_length=1,max_length=180)

class DomainPlan(CategoryPlan):
    children:list[CategoryPlan]=Field(min_length=1,max_length=4)

class TaxonomyPlan(Strict):
    domains:list[DomainPlan]=Field(min_length=1,max_length=8)

class MappingItem(Strict):
    unit_id:str
    domain_id:str
    category_id:str|None=None
    new_category:str|None=Field(default=None,max_length=45)
    reason:str=Field(min_length=1,max_length=300)

class TaxonomyMapping(Strict):
    items:list[MappingItem]

class IssueInsight(Strict):
    issue_id:str
    observation:Claim
    deeper_need:Claim|None=None
    cause_hypotheses:list[Claim]=Field(default_factory=list,max_length=2)
    direction:Claim|None=None
    validation:Claim|None=None
    metrics:list[str]=Field(default_factory=list,max_length=4)

class DecisionNarrative(Strict):
    findings:list[Claim]=Field(default_factory=list,max_length=5)
    issues:list[IssueInsight]=Field(default_factory=list,max_length=6)

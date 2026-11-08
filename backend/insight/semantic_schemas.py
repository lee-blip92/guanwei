"""Versioned, additive semantic contracts; raw opinion extraction remains unchanged."""
from typing import Literal
from pydantic import Field
from .schemas import Strict

class UnitMeaning(Strict):
    unit_id:str
    state:Literal['specific','overall','non_product','uncertain']
    object_name:str|None=None
    user_task:str|None=None
    obstacle:str|None=None
    expected_result:str|None=None
    scenario_label:str|None=None
    scenario_quote:str|None=None
    manifestation_quote:str|None=None
    impact_quote:str|None=None
    expectation_quote:str|None=None
    impact_kind:Literal['unknown','none','inconvenience','time_cost','task_blocked','data_loss','security_risk']='unknown'
    reason:str

class MeaningBatch(Strict):
    items:list[UnitMeaning]

class IssueDefinition(Strict):
    name:str=Field(min_length=1,max_length=60)
    domain:str=Field(min_length=1,max_length=45)
    object_name:str
    user_task:str|None=None
    obstacle:str|None=None
    expected_result:str|None=None
    definition:str=Field(min_length=1,max_length=500)

class IssueAssignment(Strict):
    unit_id:str
    existing_issue_id:str|None=None
    new_issue:IssueDefinition|None=None
    reason:str

class AssignmentBatch(Strict):
    items:list[IssueAssignment]

class Membership(Strict):
    unit_id:str
    issue_id:str
    verdict:Literal['supported','split','uncertain','non_product','overall']
    split_issue:IssueDefinition|None=None
    reason:str

class MembershipBatch(Strict):
    items:list[Membership]

class ComparablePair(Strict):
    pair_id:str
    verdict:Literal['comparable','different','uncertain']
    reason:str

class ComparisonBatch(Strict):
    items:list[ComparablePair]

class ConsolidatedIssue(Strict):
    name:str=Field(min_length=1,max_length=60)
    definition:str=Field(min_length=1,max_length=180)
    member_issue_ids:list[str]=Field(min_length=1)

class ConsolidationPlan(Strict):
    groups:list[ConsolidatedIssue]

class IssueScopeCheck(Strict):
    issue_id:str
    verdict:Literal['supported','split','uncertain']
    name:str=Field(min_length=1,max_length=60)
    definition:str=Field(min_length=1,max_length=500)
    reason:str

class ScopeBatch(Strict):
    items:list[IssueScopeCheck]

class ClaimRepair(Strict):
    claim_id:str
    text:str|None=None
    evidence_ids:list[str]=Field(default_factory=list)
    reason:str

class ClaimRepairBatch(Strict):
    items:list[ClaimRepair]

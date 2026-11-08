from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from .platforms import PLATFORMS, platform_id

Relevance = Literal['target', 'other_product', 'irrelevant', 'uncertain']
Sentiment = Literal['positive', 'negative', 'mixed', 'neutral', 'unknown']

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class TaskConfig(Strict):
    analysis_version: Literal[1,2] = 2
    insight_version: Literal[2,3] = 3
    semantic_version: Literal[0,1] = 1
    platforms: list[Literal['xhs','dy','ks','bili','wb','tieba','zhihu']] = Field(default_factory=lambda:['xhs'],min_length=1,max_length=7)
    product: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=1500)
    focus: str = Field(default='', max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    keywords: list[str] = Field(default_factory=list, max_length=12)
    source: Literal['crawl', 'import', 'demo'] = 'import'
    analysis_mode: Literal['ai', 'rules'] = 'rules'
    max_notes: int = Field(default=5, ge=1, le=50)
    max_comments_per_note: int = Field(default=20, ge=1, le=100)
    max_comments: int = Field(default=100, ge=1, le=1000)
    include_replies: bool = True
    sort: Literal['general', 'popularity_descending', 'time_descending'] = 'general'

    @field_validator('platforms',mode='before')
    @classmethod
    def clean_platforms(cls, values):
        if not isinstance(values,list):raise ValueError('platforms 必须是平台数组')
        return list(dict.fromkeys(platform_id(p) for p in values))

    @model_validator(mode='after')
    def supported_sort(self):
        if self.source=='crawl' and any(self.sort not in PLATFORMS[p]['sorts'] for p in self.platforms):
            raise ValueError('所选平台不共同支持该排序，请选择综合/平台默认排序')
        if self.source=='demo' and self.platforms!=['xhs']:
            raise ValueError('内置演示样本仅来自虚构的小红书数据，请保留小红书平台')
        return self

    @field_validator('product')
    @classmethod
    def clean_product(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('请输入产品名称')
        return value

    @field_validator('keywords', 'aliases')
    @classmethod
    def clean_terms(cls, values):
        if any(not v.strip() or len(v) > 100 or ',' in v for v in values):
            raise ValueError('每个词需为 1–100 字，且不包含英文逗号')
        return list(dict.fromkeys(v.strip() for v in values))

class Feedback(Strict):
    evidence_id: str
    relevance: Relevance
    reason: str = Field(min_length=1, max_length=800)
    suspected_ad: bool
    sentiment: Sentiment
    themes: list[str] = Field(max_length=5)
    scenario: str = Field(max_length=300)
    feedback: str = Field(max_length=800)
    quote: str = Field(max_length=2000)

class Extraction(Strict):
    items: list[Feedback]

class ThemeGroup(Strict):
    name: str = Field(min_length=1, max_length=60)
    member_labels: list[str] = Field(min_length=1)

class Grouping(Strict):
    groups: list[ThemeGroup]

class Claim(Strict):
    text: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)

class Narrative(Strict):
    findings: list[Claim] = Field(default_factory=list,max_length=8)
    positives: list[Claim] = Field(default_factory=list,max_length=8)
    recommendations: list[Claim] = Field(default_factory=list,max_length=8)
    questions: list[Claim] = Field(default_factory=list,max_length=8)

class OpinionPoint(Strict):
    product_scope: Literal['target','other_product','uncertain']
    aspect: str = Field(min_length=1,max_length=60)
    topic: str = Field(min_length=1,max_length=120)
    kind: Literal['experience','problem','request','question','other']
    sentiment: Sentiment
    basis: Literal['direct','agreement']
    scenario: str | None = Field(default=None,max_length=300)
    pain_point: str | None = Field(default=None,max_length=500)
    impact: str | None = Field(default=None,max_length=500)
    expected_outcome: str | None = Field(default=None,max_length=500)
    keywords: list[str] = Field(max_length=6)
    quote: str = Field(min_length=1,max_length=800)
    context_quote: str | None = Field(default=None,max_length=800)
    intents: list[Literal['positive_experience','negative_experience','feature_request','usage_question','fault_report','improvement_suggestion','use_scenario','factual_statement','irrelevant','uncertain']] = Field(default_factory=list,max_length=10)
    evaluation_object: Literal['product','feature','tutorial','creator','platform_content','third_party','other','uncertain'] = 'uncertain'
    stance: Literal['support','oppose','neutral','uncertain'] = 'uncertain'
    epistemic: Literal['experience','request','question','information','speculation','uncertain'] = 'uncertain'
    severity: Literal['none','inconvenience','time_cost','task_blocked','data_loss','security_risk','unknown'] = 'unknown'
    severity_quote: str | None = Field(default=None,max_length=500)
    group_quote: str | None = Field(default=None,max_length=150)

class CommentUnderstanding(Strict):
    evidence_id: str
    relevance: Relevance
    reason: str = Field(min_length=1,max_length=800)
    suspected_ad: bool
    points: list[OpinionPoint] = Field(max_length=8)

class AspectExtraction(Strict):
    items: list[CommentUnderstanding]

class OpinionCheck(Strict):
    point_index: int = Field(ge=0,le=7)
    verdict: Literal['supported','unsupported','uncertain']
    reason: str = Field(min_length=1,max_length=500)
    basis: Literal['direct','agreement'] | None = None
    kind: Literal['experience','problem','request','question','other'] | None = None
    sentiment: Sentiment | None = None
    context_quote: str | None = Field(default=None,max_length=800)
    scenario_quote: str | None = Field(default=None,max_length=300)
    impact_quote: str | None = Field(default=None,max_length=500)
    expected_outcome_quote: str | None = Field(default=None,max_length=500)

    @model_validator(mode='after')
    def supported_fields(self):
        if self.verdict!='unsupported' and any(getattr(self,k) is None for k in ('basis','kind','sentiment')):
            raise ValueError('保留观点必须给出 basis、kind、sentiment；不支持的观点无需补造这些字段')
        return self

class CommentOpinionAudit(Strict):
    evidence_id: str
    content_class: Literal['feedback','information_only','non_feedback','uncertain']
    reason: str = Field(min_length=1,max_length=600)
    checks: list[OpinionCheck] = Field(default_factory=list)

class OpinionAuditBatch(Strict):
    items: list[CommentOpinionAudit]

class OpinionGroup(Strict):
    aspect: str = Field(min_length=1,max_length=60)
    name: str = Field(min_length=1,max_length=120)
    member_ids: list[str] = Field(min_length=1)

class OpinionGrouping(Strict):
    groups: list[OpinionGroup]

class TopicAdvice(Strict):
    topic_id: str
    recommendation: Claim | None = None
    validation: Claim | None = None

class TopicAdviceBatch(Strict):
    topics: list[TopicAdvice]

class SupportVerdict(Strict):
    claim_id: str
    verdict: Literal['supported', 'unsupported', 'uncertain']
    reason: str = Field(min_length=1, max_length=800)

class SupportCheck(Strict):
    items: list[SupportVerdict]

class Review(Strict):
    points: list[OpinionPoint] | None = Field(default=None,max_length=8)
    relevance: Relevance | None = None
    sentiment: Sentiment | None = None
    suspected_ad: bool | None = None
    themes: list[str] | None = Field(default=None, max_length=5)
    excluded: bool | None = None
    note: str = Field(default='', max_length=1000)

class Settings(Strict):
    base_url: str = Field(max_length=500)
    model: str = Field(min_length=1, max_length=150)
    batch_size: int = Field(ge=1, le=20)
    timeout: int = Field(ge=5, le=180)

    @field_validator('base_url')
    @classmethod
    def url(cls, value):
        from urllib.parse import urlparse
        u = urlparse(value)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('请填写无密钥参数的 http(s) API 地址，如 https://api.openai.com/v1')
        return value.rstrip('/')

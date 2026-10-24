"""Real-estate search contracts shared by CLI, GUI and bounded agent tasks."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Mode = Literal['residential_rent', 'residential_buy', 'commercial_lease', 'commercial_buy']
PriceBasis = Literal['total', 'month', 'year', 'sqft_year', 'sqft_month', 'unknown']


def now() -> str:
    return datetime.now(UTC).isoformat(timespec='seconds')


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)


def public_url(value: str) -> str:
    if not value:
        return ''
    value = value.strip()
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', value):
        value = 'https://' + value
    p = urlsplit(value)
    if p.scheme not in {'http', 'https'} or not p.hostname or p.username is not None or p.password is not None or len(value) > 2048:
        raise ValueError('Use a public http or https URL without credentials.')
    return urlunsplit((p.scheme, p.netloc.lower(), p.path, p.query, ''))


def domain_of(value: str) -> str:
    try:
        return (urlsplit(public_url(value)).hostname or '').lower().removeprefix('www.').encode('idna').decode()
    except (ValueError, UnicodeError):
        return ''


def canonical_url(value: str) -> str:
    if not value:
        return ''
    p = urlsplit(public_url(value))
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in {'ref', 'source', 'fbclid', 'gclid'}]
    return urlunsplit(('https', p.netloc.lower().removeprefix('www.'), p.path.rstrip('/'), urlencode(sorted(query)), ''))


def valid_urls(values: list[str]) -> list[str]:
    return list(dict.fromkeys(public_url(x) for x in values if x))


class Client(Model):
    name: str = Field(default='', max_length=200)
    email: str = Field(default='', max_length=254)
    phone: str = Field(default='', max_length=100)
    organization: str = Field(default='', max_length=250)
    brief_text: str = Field(default='', max_length=60000)


class Preferences(Model):
    locations: list[str] = Field(default_factory=list, max_length=20)
    property_types: list[str] = Field(default_factory=list, max_length=20)
    currency: str = Field(default='USD', pattern=r'^[A-Z]{3}$')
    budget_monthly_max: float | None = Field(default=None, gt=0, le=100000000)
    purchase_price_max: float | None = Field(default=None, gt=0, le=10000000000)
    cash_available: float | None = Field(default=None, ge=0, le=10000000000)
    min_area_sqft: float | None = Field(default=None, ge=0, le=100000000)
    max_area_sqft: float | None = Field(default=None, gt=0, le=100000000)
    min_bedrooms: float | None = Field(default=None, ge=0, le=100)
    min_bathrooms: float | None = Field(default=None, ge=0, le=100)
    intended_use: str = Field(default='', max_length=2000)
    must_have: str = Field(default='', max_length=3000)
    exclude: str = Field(default='', max_length=3000)
    move_by: str = Field(default='', max_length=100)
    max_listing_age_days: int = Field(default=60, ge=1, le=730)

    @model_validator(mode='after')
    def area_range(self):
        if self.min_area_sqft is not None and self.max_area_sqft is not None and self.min_area_sqft > self.max_area_sqft:
            raise ValueError('Minimum area exceeds maximum area.')
        return self


class CostAssumptions(Model):
    financing: Literal['mortgage', 'cash'] = 'mortgage'
    down_payment_percent: float = Field(default=20, ge=0, le=100)
    interest_rate_percent: float | None = Field(default=None, ge=0, le=40)
    loan_years: int = Field(default=30, ge=1, le=50)
    closing_cost_percent: float | None = Field(default=None, ge=0, le=30)
    property_tax_annual: float | None = Field(default=None, ge=0, le=100000000)
    insurance_annual: float | None = Field(default=None, ge=0, le=100000000)
    hoa_monthly: float | None = Field(default=None, ge=0, le=10000000)
    maintenance_monthly: float | None = Field(default=None, ge=0, le=10000000)
    mortgage_insurance_monthly: float | None = Field(default=None, ge=0, le=10000000)
    utilities_monthly: float | None = Field(default=None, ge=0, le=10000000)
    other_monthly: float | None = Field(default=None, ge=0, le=10000000)
    repairs_upfront: float | None = Field(default=None, ge=0, le=100000000)
    fit_out_upfront: float | None = Field(default=None, ge=0, le=100000000)
    moving_upfront: float | None = Field(default=None, ge=0, le=10000000)


class Route(Model):
    provider: Literal['inherit', 'codex', 'claude'] = 'inherit'
    model: str = Field(default='', max_length=120)


class ModelRoutes(Model):
    profile: Route = Field(default_factory=Route)
    discovery: Route = Field(default_factory=Route)
    research: Route = Field(default_factory=Route)
    outreach: Route = Field(default_factory=Route)


class Weights(Model):
    property: int = Field(default=25, ge=0, le=100)
    location: int = Field(default=20, ge=0, le=100)
    budget: int = Field(default=25, ge=0, le=100)
    requirements: int = Field(default=25, ge=0, le=100)
    timing: int = Field(default=5, ge=0, le=100)


class Settings(Model):
    provider: Literal['codex', 'claude', 'demo'] = 'codex'
    model: str = Field(default='', max_length=120)
    routes: ModelRoutes = Field(default_factory=ModelRoutes)
    target_results: int = Field(default=25, ge=1, le=1000)
    min_fit: int = Field(default=55, ge=0, le=100)
    discovery_queries: int = Field(default=6, ge=1, le=40)
    max_agent_calls: int = Field(default=100, ge=1, le=5000)
    max_minutes: int = Field(default=90, ge=1, le=1440)
    concurrency: int = Field(default=2, ge=1, le=4)
    agent_timeout_seconds: int = Field(default=180, ge=15, le=900)
    max_turns: int = Field(default=8, ge=2, le=40)
    max_cost_usd: float = Field(default=10, ge=0.1, le=1000)
    research_pages: int = Field(default=6, ge=1, le=10)
    weights: Weights = Field(default_factory=Weights)
    seed_urls: list[str] = Field(default_factory=list, max_length=20)
    _seeds = field_validator('seed_urls')(valid_urls)


class SearchInput(Model):
    name: str = Field(min_length=1, max_length=120)
    mode: Mode = 'residential_rent'
    objective: str = Field(default='', max_length=6000)
    client: Client = Field(default_factory=Client)
    preferences: Preferences = Field(default_factory=Preferences)
    cost_assumptions: CostAssumptions = Field(default_factory=CostAssumptions)
    settings: Settings = Field(default_factory=Settings)
    demo: bool = False


class Evidence(Model):
    url: str = Field(max_length=2048)
    title: str = Field(default='', max_length=300)
    quote: str = Field(default='', max_length=600)
    field: str = Field(default='listing', max_length=80)
    checked_at: str = ''
    _url = field_validator('url')(public_url)


class MarketSignal(Model):
    kind: Literal['price_change', 'new_listing', 'vacancy', 'incentive', 'development', 'infrastructure', 'business_expansion', 'market', 'other']
    summary: str = Field(max_length=800)
    url: str = Field(max_length=2048)
    published_at: str = Field(default='', max_length=100)
    event_at: str = Field(default='', max_length=100)
    _url = field_validator('url')(public_url)


class ListingCosts(Model):
    property_tax_annual: float | None = Field(default=None, ge=0, le=100000000)
    insurance_annual: float | None = Field(default=None, ge=0, le=100000000)
    hoa_monthly: float | None = Field(default=None, ge=0, le=10000000)
    utilities_monthly: float | None = Field(default=None, ge=0, le=10000000)
    parking_monthly: float | None = Field(default=None, ge=0, le=10000000)
    pet_monthly: float | None = Field(default=None, ge=0, le=10000000)
    other_monthly: float | None = Field(default=None, ge=0, le=10000000)
    operating_expenses: float | None = Field(default=None, ge=0, le=10000000)
    operating_expenses_basis: PriceBasis = 'unknown'
    deposit: float | None = Field(default=None, ge=0, le=100000000)
    application_fee: float | None = Field(default=None, ge=0, le=1000000)
    other_upfront: float | None = Field(default=None, ge=0, le=100000000)
    concession_total: float | None = Field(default=None, ge=0, le=100000000)
    lease_months: int | None = Field(default=None, ge=1, le=1200)
    source_url: str = Field(default='', max_length=2048)
    notes: str = Field(default='', max_length=2000)
    _source = field_validator('source_url')(public_url)


class OpportunitySeed(Model):
    mode: Mode
    title: str = Field(min_length=1, max_length=300)
    address: str = Field(default='', max_length=500)
    unit: str = Field(default='', max_length=150)
    location: str = Field(default='', max_length=500)
    property_type: str = Field(default='', max_length=150)
    listing_url: str = Field(default='', max_length=2048)
    broker_url: str = Field(default='', max_length=2048)
    inquiry_url: str = Field(default='', max_length=2048)
    listing_id: str = Field(default='', max_length=200)
    source_provider: str = Field(default='web', max_length=80)
    description: str = Field(default='', max_length=8000)
    features: list[str] = Field(default_factory=list, max_length=40)
    price_amount: float | None = Field(default=None, ge=0, le=10000000000)
    currency: str = Field(default='', max_length=10)
    price_basis: PriceBasis = 'unknown'
    area_sqft: float | None = Field(default=None, gt=0, le=100000000)
    bedrooms: float | None = Field(default=None, ge=0, le=1000)
    bathrooms: float | None = Field(default=None, ge=0, le=1000)
    lease_type: Literal['gross', 'modified_gross', 'nnn', 'unknown'] = 'unknown'
    costs: ListingCosts = Field(default_factory=ListingCosts)
    posted_at: str = Field(default='', max_length=100)
    available_from: str = Field(default='', max_length=100)
    valid_through: str = Field(default='', max_length=100)
    listing_status: Literal['available', 'unavailable', 'unverified'] = 'unverified'
    listing_source: Literal['primary', 'broker', 'aggregator', 'news', 'import', 'unknown'] = 'unknown'
    last_checked_at: str = Field(default='', max_length=100)
    contact_name: str = Field(default='', max_length=200)
    contact_role: str = Field(default='', max_length=200)
    contact_source: str = Field(default='', max_length=2048)
    email: str = Field(default='', max_length=254)
    email_source: str = Field(default='', max_length=2048)
    phone: str = Field(default='', max_length=100)
    source_urls: list[str] = Field(default_factory=list, max_length=25)
    signals: list[MarketSignal] = Field(default_factory=list, max_length=8)
    _urls = field_validator('listing_url', 'broker_url', 'inquiry_url', 'contact_source', 'email_source')(public_url)
    _sources = field_validator('source_urls')(valid_urls)


class MatchComponent(Model):
    criterion: Literal['property', 'location', 'requirements', 'timing']
    match: Literal['strong', 'partial', 'gap', 'unknown']
    reason: str = Field(max_length=1000)
    source_url: str = Field(default='', max_length=2048)
    _url = field_validator('source_url')(public_url)


class RequirementCheck(Model):
    criterion: str = Field(max_length=100)
    status: Literal['met', 'conflict', 'unknown']
    reason: str = Field(max_length=1000)
    source_url: str = Field(default='', max_length=2048)
    _url = field_validator('source_url')(public_url)


class DiligenceItem(Model):
    topic: str = Field(max_length=150)
    status: Literal['unresolved', 'source_found', 'confirmed', 'not_applicable'] = 'unresolved'
    question: str = Field(max_length=1000)
    source_url: str = Field(default='', max_length=2048)
    _url = field_validator('source_url')(public_url)


class Comparable(Model):
    title: str = Field(max_length=300)
    address: str = Field(default='', max_length=500)
    unit: str = Field(default='', max_length=150)
    url: str = Field(max_length=2048)
    mode: Mode
    property_type: str = Field(max_length=150)
    location: str = Field(max_length=500)
    price_amount: float = Field(gt=0, le=10000000000)
    currency: str = Field(max_length=10)
    price_basis: PriceBasis
    area_sqft: float | None = Field(default=None, gt=0, le=100000000)
    bedrooms: float | None = Field(default=None, ge=0, le=1000)
    observed_at: str = Field(default='', max_length=100)
    basis: Literal['asking', 'closed'] = 'asking'
    lease_type: Literal['gross', 'modified_gross', 'nnn', 'unknown'] = 'unknown'
    rationale: str = Field(default='', max_length=800)
    _url = field_validator('url')(public_url)


class DiscoveryResult(Model):
    opportunities: list[OpportunitySeed] = Field(max_length=20)
    coverage_notes: str = Field(max_length=2000)


class ResearchResult(OpportunitySeed):
    evidence: list[Evidence] = Field(min_length=1, max_length=20)
    match_components: list[MatchComponent] = Field(max_length=4)
    requirement_checks: list[RequirementCheck] = Field(max_length=20)
    risks: list[str] = Field(max_length=20)
    due_diligence: list[DiligenceItem] = Field(max_length=20)
    comparables: list[Comparable] = Field(max_length=8)
    research_summary: str = Field(max_length=4000)
    outreach_angle: str = Field(max_length=1500)


class Draft(Model):
    step: int = Field(ge=1, le=3)
    delay_days: int = Field(ge=0, le=60)
    subject: str = Field(max_length=200)
    body: str = Field(max_length=3000)
    source_urls: list[str] = Field(default_factory=list, max_length=12)
    user_claims: list[str] = Field(default_factory=list, max_length=8)
    angle: str = Field(default='', max_length=500)
    _urls = field_validator('source_urls')(valid_urls)


class SequenceResult(Model):
    drafts: list[Draft] = Field(min_length=3, max_length=3)


class ProfileBrief(Model):
    summary: str = Field(min_length=1, max_length=6000)
    search_terms: list[str] = Field(max_length=25)
    priorities: list[str] = Field(max_length=15)
    unknowns: list[str] = Field(max_length=15)
    source_plan: list[str] = Field(max_length=15)


class Opportunity(ResearchResult):
    evidence: list[Evidence] = Field(default_factory=list)
    match_components: list[MatchComponent] = Field(default_factory=list)
    requirement_checks: list[RequirementCheck] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    due_diligence: list[DiligenceItem] = Field(default_factory=list)
    comparables: list[Comparable] = Field(default_factory=list)
    research_summary: str = ''
    outreach_angle: str = ''
    id: str
    search_id: str
    identity: str
    domain: str = ''
    created_at: str
    updated_at: str
    fit_score: int = 0
    deal_score: int | None = None
    confidence: int = 0
    score_breakdown: list[dict] = Field(default_factory=list)
    estimate: dict = Field(default_factory=dict)
    deal: dict = Field(default_factory=dict)
    eligibility: Literal['eligible', 'review', 'blocked'] = 'review'
    blockers: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    next_action: Literal['review', 'contact', 'research', 'skip'] = 'research'
    research_status: Literal['pending', 'complete', 'error'] = 'pending'
    research_fingerprint: str = ''
    draft_fingerprint: str = ''
    source_check: Literal['unverified', 'matched', 'reachable', 'unavailable', 'closed'] = 'unverified'
    verified_comparable_urls: list[str] = Field(default_factory=list)
    email_status: Literal['missing', 'published', 'mx_found', 'no_mx', 'unknown', 'invalid', 'verified', 'suppressed', 'catch_all', 'risky'] = 'missing'
    email_checked_at: str = ''
    verification_source: str = ''
    verification_at: str = ''
    status: Literal['new', 'researched', 'drafted', 'approved', 'rejected', 'suppressed'] = 'new'
    drafts: list[Draft] = Field(default_factory=list)
    qa_issues: list[str] = Field(default_factory=list)
    review_notes: str = ''
    workflow_status: Literal['new', 'shortlisted', 'contacted', 'replied', 'tour_scheduled', 'toured', 'applied', 'offered', 'negotiating', 'closed', 'rejected', 'not_interested', 'bounced'] = 'new'
    activity_at: str = ''
    next_follow_up: str = ''


class RunRequest(Model):
    stage: Literal['pipeline', 'profile', 'discover', 'research', 'verify', 'draft'] = 'pipeline'


class SuppressionInput(Model):
    kind: Literal['email', 'domain']
    value: str = Field(min_length=1, max_length=254)
    reason: str = Field(default='Do not contact', max_length=500)


def strict_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    def walk(obj):
        if isinstance(obj, dict):
            obj.pop('default', None)
            if obj.get('type') == 'object':
                obj['additionalProperties'] = False
                obj['required'] = list(obj.get('properties', {}))
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)
    walk(schema)
    return schema


def route_for(settings: dict, task: str) -> dict[str, str]:
    if settings['provider'] == 'demo':
        return {'provider': 'demo', 'model': ''}
    route = settings.get('routes', {}).get(task, {})
    provider = route.get('provider', 'inherit')
    resolved = settings['provider'] if provider == 'inherit' else provider
    model = route.get('model') or (settings.get('model', '') if resolved == settings['provider'] else '')
    return {'provider': resolved, 'model': model}

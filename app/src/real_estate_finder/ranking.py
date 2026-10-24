"""Explainable fit and asking-price comparisons; no hidden agent-generated scores."""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, time

from .costs import deal_comparison, estimate_costs
from .models import canonical_url


def date_age(value: str) -> float | None:
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return (datetime.now(UTC) - stamp).total_seconds() / 86400
    except (ValueError, TypeError):
        return None


def deadline_passed(value: str) -> bool:
    if not value:
        return False
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if len(value) == 10:
            stamp = datetime.combine(stamp.date(), time.max, tzinfo=UTC)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return stamp < datetime.now(UTC)
    except (ValueError, TypeError):
        return False


def norm(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', value.casefold()).strip()


def identity_of(row: dict) -> str:
    # Never deduplicate an apartment building at domain level. Units are separate.
    if row.get('address') and row.get('location'):
        value = '|'.join((row['mode'], norm(row['address']), norm(row.get('unit', '')), norm(row['location'])))
    elif row.get('listing_url'):
        value = '|'.join((row['mode'], canonical_url(row['listing_url']), norm(row.get('unit', ''))))
    else:
        raise ValueError('A property needs a listing URL or a full address and location.')
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def user_claims_supported(claims: list[str], original: str) -> list[str]:
    text = re.sub(r'\s+', ' ', original).casefold()
    return [claim for claim in claims if len(claim.strip()) >= 3 and re.sub(r'\s+', ' ', claim).casefold() in text]


def default_diligence(mode: str) -> list[dict]:
    items = [('Availability', 'Is this exact unit or property still available, and on what date?'), ('Representative', 'Can the owner or authorized representative be independently verified?'), ('Costs', 'What mandatory recurring fees and upfront charges apply?'), ('Condition', 'What defects, repairs, inspections or disclosures need review?')]
    if mode.startswith('commercial'):
        items += [('Permitted use', 'Does local planning confirm the intended use, occupancy and required permits?'), ('Site needs', 'Do power, loading, access, parking, signage and accessibility meet the operating requirements?'), ('Environmental risk', 'Which environmental, flood, fire or other site reports need professional review?')]
    if mode.endswith('_buy'):
        items += [('Title and obligations', 'Which title, lien, association, tax and other obligations need professional review?'), ('Financing and closing', 'What are the actual loan terms, insurance quotes, closing charges and cash needed?')]
    else:
        items += [('Lease terms', 'What are the lease term, renewal, escalation, deposit and termination terms?'), ('Inclusions', 'Which utilities, maintenance, taxes and insurance are included?')]
    return [{'topic': topic, 'status': 'unresolved', 'question': question, 'source_url': ''} for topic, question in items]


def assess(search: dict, row: dict, original: str = '') -> dict:
    prefs, weights = search['preferences'], search['settings']['weights']
    estimate = estimate_costs(search, row)
    deal = deal_comparison(row)
    blockers, questions, components = [], [], []
    evidence_urls = {item['url'] for item in row.get('evidence', []) if item.get('quote')}
    matches = {item['criterion']: item for item in row.get('match_components', [])}
    if row['mode'] != search['mode']:
        blockers.append('Different transaction type')
    if row.get('listing_status') == 'unavailable' or deadline_passed(row.get('valid_through', '')):
        blockers.append('Listing is unavailable or expired')
    if row.get('price_amount') == 0:
        questions.append('Zero asking price: confirm the actual price and terms before comparing value')
    if row.get('listing_status') != 'available':
        questions.append('Confirm current availability')
    # Numeric constraints have deterministic authority over language-model opinions.
    for pref, field, direction, label in [('min_area_sqft', 'area_sqft', 'min', 'Minimum floor area'), ('max_area_sqft', 'area_sqft', 'max', 'Maximum floor area'), ('min_bedrooms', 'bedrooms', 'min', 'Bedrooms'), ('min_bathrooms', 'bathrooms', 'min', 'Bathrooms')]:
        limit = prefs.get(pref)
        if limit is not None:
            value = row.get(field)
            if value is None:
                questions.append(label + ' not confirmed')
            elif (direction == 'min' and value < limit) or (direction == 'max' and value > limit):
                blockers.append(label + ' conflicts with your brief')
    currency_ok = bool(row.get('currency')) and row['currency'] == prefs['currency']
    budget_checks, budget_unknown = [], []
    for pref, key, known_key, label in [('budget_monthly_max', 'monthly_total', 'known_monthly', 'Monthly budget'), ('purchase_price_max', 'purchase_price', 'purchase_price', 'Purchase price'), ('cash_available', 'cash_needed', 'known_cash_needed', 'Cash available')]:
        limit = prefs.get(pref)
        if limit is None or (pref == 'purchase_price_max' and not row['mode'].endswith('_buy')):
            continue
        if not currency_ok:
            budget_unknown.append(label + ': currency not comparable; no exchange-rate assumption')
            continue
        value, known = estimate.get(key), estimate.get(known_key)
        if (value if value is not None else known) is not None and (value if value is not None else known) > limit:
            blockers.append(label + ' exceeded by known costs')
            budget_checks.append(0.0)
        elif value is None:
            budget_unknown.append(label + ': missing costs')
        else:
            budget_checks.append(1.0)
    questions.extend(budget_unknown)
    for criterion in weights:
        weight = weights[criterion]
        if criterion == 'budget':
            value = sum(budget_checks) / (len(budget_checks) + len(budget_unknown)) if budget_checks or budget_unknown else 0
            known = bool(budget_checks) and not budget_unknown
            reason = '; '.join(budget_unknown) or ('Within entered cost limits' if budget_checks and all(budget_checks) else 'Cost limits exceeded' if budget_checks else 'Set a cost limit to assess budget fit')
            source = row.get('costs', {}).get('source_url', '') or row.get('listing_url', '')
        else:
            match = matches.get(criterion, {})
            known = match.get('source_url') in evidence_urls and match.get('match') in {'strong', 'partial', 'gap'}
            value = {'strong': 1, 'partial': 0.5, 'gap': 0}.get(match.get('match'), 0) if known else 0
            reason = match.get('reason') or 'Not assessed with source evidence'
            source = match.get('source_url', '')
        components.append({'criterion': criterion, 'weight': weight, 'earned': round(weight * value, 2), 'known': known, 'reason': reason, 'source_url': source})
    for check in row.get('requirement_checks', []):
        if check['status'] == 'conflict' and check.get('source_url') in evidence_urls:
            blockers.append(check['criterion'] + ': ' + check['reason'])
        elif check['status'] == 'unknown' or not check.get('source_url'):
            questions.append(check['criterion'] + ': ' + check['reason'])
    age = date_age(row.get('posted_at', ''))
    if age is not None and age > prefs['max_listing_age_days']:
        questions.append('Listing is older than your freshness preference')
    if row.get('source_check') != 'matched':
        questions.append('Exact property source has not been matched')
    if row.get('research_status') != 'complete':
        questions.append('Complete current property research')
    for item in row.get('due_diligence', []):
        if item['status'] not in {'confirmed', 'not_applicable'}:
            questions.append(item['topic'] + ': ' + item['question'])
    total = sum(weights.values()) or 1
    fit = round(sum(item['earned'] for item in components) / total * 100)
    confidence = round(sum(item['weight'] for item in components if item['known']) / total * 70)
    if row.get('source_check') == 'matched':
        confidence += 15
    if row.get('listing_status') == 'available':
        confidence += 5
    if estimate['complete']:
        confidence += 10
    return {'fit_score': fit, 'deal_score': deal['score'], 'confidence': confidence, 'estimate': estimate, 'deal': deal, 'score_breakdown': components, 'blockers': list(dict.fromkeys(blockers)), 'questions': list(dict.fromkeys(questions)), 'eligibility': 'blocked' if blockers else 'review' if questions or estimate['unknowns'] else 'eligible', 'next_action': 'skip' if blockers else 'contact' if row.get('research_status') == 'complete' and row.get('source_check') == 'matched' else 'research'}


def rank_key(row: dict):
    return (row.get('eligibility') == 'blocked', -row.get('fit_score', 0), -row.get('confidence', 0), -(row.get('deal_score') if row.get('deal_score') is not None else -1), row.get('title', '').casefold())

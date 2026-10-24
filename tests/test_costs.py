from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from real_estate_finder import demo
from real_estate_finder.costs import (
    deal_comparison,
    estimate_costs,
    monthly_amount,
    mortgage_payment,
)
from real_estate_finder.models import SearchInput, now


def scenario(mode='residential_rent'):
    return SearchInput(**demo.search_input(mode)).model_dump(), demo.properties(mode)[0]


def test_commercial_annual_per_square_foot_is_divided_by_twelve():
    search, row = scenario('commercial_lease')
    row.update(price_amount=24, price_basis='sqft_year', area_sqft=1000)
    row['costs'].update(operating_expenses=6, operating_expenses_basis='sqft_year')
    estimate = estimate_costs(search, row)
    assert estimate['base_monthly'] == 2000
    assert next(x['amount'] for x in estimate['monthly_breakdown'] if 'NNN' in x['label']) == 500
    assert estimate['monthly_total'] == 2770  # rent, NNN, utilities, tenant insurance


def test_monthly_per_square_foot_is_not_divided_again():
    assert monthly_amount(2, 'sqft_month', 1000) == 2000
    assert monthly_amount(24000, 'year') == 2000
    assert monthly_amount(2000, 'month') == 2000


@pytest.mark.parametrize('amount,basis,area', [(2, 'sqft_month', None), (2000, 'unknown', 1000), (200000, 'total', 1000), (None, 'month', None)])
def test_unknown_price_units_never_become_zero(amount, basis, area):
    assert monthly_amount(amount, basis, area) is None


def test_mortgage_formula_and_zero_rate_case():
    assert mortgage_payment(320000, 6, 30) == pytest.approx(1918.56168, rel=1e-6)
    assert mortgage_payment(120000, 0, 10) == 1000
    assert mortgage_payment(0, 8, 30) == 0


def test_purchase_includes_ownership_costs_and_upfront_costs():
    search, row = scenario('residential_buy')
    row.update(price_amount=400000)
    row['costs'].update(property_tax_annual=4800, insurance_annual=1200, hoa_monthly=50, utilities_monthly=150)
    search['cost_assumptions'].update(interest_rate_percent=6, maintenance_monthly=200, repairs_upfront=1000, moving_upfront=500)
    result = estimate_costs(search, row)
    assert result['mortgage_payment'] == 1918.56
    assert result['monthly_total'] == 2818.56
    assert result['cash_needed'] == 93500
    assert result['complete']


def test_missing_rate_does_not_silently_assume_zero_interest():
    search, row = scenario('residential_buy')
    search['cost_assumptions']['interest_rate_percent'] = None
    result = estimate_costs(search, row)
    assert result['mortgage_payment'] is None and result['monthly_total'] is None
    assert 'Loan interest rate' in result['unknowns']
    assert result['known_monthly'] > 0


def test_cash_purchase_needs_no_rate_or_mortgage_insurance():
    search, row = scenario('residential_buy')
    search['cost_assumptions'].update(financing='cash', interest_rate_percent=None, mortgage_insurance_monthly=None)
    result = estimate_costs(search, row)
    assert result['mortgage_payment'] == 0
    assert result['monthly_total'] is not None
    assert result['cash_needed'] > row['price_amount']


def test_default_costs_are_unknown_until_explicitly_entered():
    search = SearchInput(name='Unknown costs').model_dump()
    _, row = scenario()
    row['costs']['other_monthly'] = None
    result = estimate_costs(search, row)
    assert result['monthly_total'] is None
    assert 'Other recurring charges' in result['unknowns']
    assert result['cash_needed'] is None
    assert result['known_monthly'] >= row['price_amount']


def test_explicit_zero_is_known_and_different_from_blank():
    search, row = scenario()
    row['costs']['utilities_monthly'] = None
    unknown = estimate_costs(search, row)
    row['costs']['utilities_monthly'] = 0
    known = estimate_costs(search, row)
    assert unknown['monthly_total'] is None
    assert known['monthly_total'] is not None


def test_concession_effective_rent_does_not_reduce_gross_or_deposit_budget():
    search, row = scenario()
    row['costs'].update(concession_total=1200, lease_months=12)
    result = estimate_costs(search, row)
    assert result['effective_monthly'] == result['monthly_total'] - 100
    assert result['cash_needed'] == result['monthly_total'] + row['costs']['deposit'] + 50 + 600


def test_unknown_commercial_pass_through_cost_never_means_included():
    search, row = scenario('commercial_lease')
    row['costs']['operating_expenses'] = None
    result = estimate_costs(search, row)
    assert result['monthly_total'] is None
    assert any('NNN' in item for item in result['unknowns'])


def test_scenario_override_is_recorded_and_does_not_mutate_property_facts():
    search, row = scenario('residential_buy')
    search['cost_assumptions']['insurance_annual'] = 3600
    before = row['costs']['insurance_annual']
    result = estimate_costs(search, row)
    assert next(x['amount'] for x in result['monthly_breakdown'] if x['label'] == 'Property insurance') == 300
    assert any('insurance annual = 3600' in item for item in result['assumptions'])
    assert row['costs']['insurance_annual'] == before


def comparable_row(mode='residential_rent'):
    _, row = scenario(mode)
    row.update(source_check='matched', last_checked_at=now(), listing_status='available')
    comps = []
    for i, price in enumerate([2400, 2500, 2600]):
        comps.append({'title': f'Comparable {i}', 'address': f'{i + 400} Sample Avenue', 'unit': '2', 'url': f'https://comp{i}.example/1', 'mode': mode, 'property_type': row['property_type'], 'location': row['location'], 'price_amount': price, 'currency': 'USD', 'price_basis': 'month', 'area_sqft': 1000, 'bedrooms': 2, 'observed_at': now(), 'basis': 'asking', 'lease_type': 'unknown', 'rationale': 'Same fictional submarket and type.'})
    row.update(comparables=comps, verified_comparable_urls=[c['url'] for c in comps])
    return row


def test_three_eligible_recent_comparables_produce_explicit_asking_comparison():
    row = comparable_row()
    result = deal_comparison(row)
    assert result['comparable_count'] == 3
    assert result['median_unit_price'] == 2500
    assert result['discount_percent'] == 16
    assert result['score'] == 82
    assert 'not an appraisal' in result['label']


@pytest.mark.parametrize('patch', [
    {'currency': 'EUR'}, {'mode': 'residential_buy'}, {'location': 'A different city'},
    {'bedrooms': 1}, {'basis': 'closed'}, {'property_type': 'house'}, {'area_sqft': 1500},
    {'observed_at': '2000-01-01'}, {'observed_at': '2099-01-01'},
])
def test_incompatible_comparable_cannot_fill_minimum_evidence(patch):
    row = comparable_row()
    row['comparables'][0].update(patch)
    result = deal_comparison(row)
    assert result['score'] is None and result['comparable_count'] == 2


def test_agent_proposed_comps_without_page_checks_do_not_yield_deal_score():
    row = comparable_row()
    row['verified_comparable_urls'] = []
    assert deal_comparison(row)['score'] is None


def test_same_property_on_multiple_sites_counts_once():
    row = comparable_row()
    row['comparables'][1]['address'] = row['comparables'][0]['address']
    result = deal_comparison(row)
    assert result['score'] is None and result['comparable_count'] == 2


def test_target_property_cannot_be_its_own_comparable():
    row = comparable_row()
    row['comparables'][0].update(address=row['address'], unit=row['unit'])
    assert deal_comparison(row)['comparable_count'] == 2


@pytest.mark.parametrize('patch', [{'source_check': 'reachable'}, {'listing_status': 'unverified'}, {'last_checked_at': (datetime.now(UTC) - timedelta(days=8)).isoformat()}])
def test_target_needs_current_property_check_before_deal_score(patch):
    row = comparable_row()
    row.update(patch)
    assert deal_comparison(row)['score'] is None


def test_commercial_comps_require_same_lease_structure():
    row = comparable_row('commercial_lease')
    for comp in row['comparables']:
        comp.update(area_sqft=2600, price_amount=20, price_basis='sqft_year', bedrooms=None, lease_type='gross')
    assert deal_comparison(row)['score'] is None

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from real_estate_finder import demo
from real_estate_finder.config import AppError
from real_estate_finder.models import OpportunitySeed, SearchInput, now, route_for, strict_schema
from real_estate_finder.quality import sequence_issues
from real_estate_finder.ranking import assess, deadline_passed, identity_of
from real_estate_finder.service import Service


@pytest.fixture
def service(tmp_path):
    return Service(tmp_path)


def search(service, mode='residential_rent', **patch):
    raw = demo.search_input(mode)
    raw.update(demo=False, **patch)
    raw['settings']['provider'] = 'codex'
    return service.create_search(raw)


def property_row(service, saved, index=0):
    return service.upsert_seed(saved['id'], OpportunitySeed(**demo.properties(saved['mode'])[index]))[0]


def researched(service, saved, index=0):
    row = property_row(service, saved, index)
    row.update(demo.research_result(row), research_status='complete', research_fingerprint='current', source_check='matched', last_checked_at=now())
    row.update(demo.sequence_result(row), status='drafted', draft_fingerprint='current')
    row.update(assess(saved, row, service.client_text(saved['id'])))
    service.store.save('opportunities', row)
    return row


def test_properties_with_shared_broker_contact_are_distinct(service):
    saved = search(service, 'residential_buy')
    seeds = demo.properties(saved['mode'])[:2]
    for seed in seeds:
        seed.update(inquiry_url='https://broker.example/contact')
        service.upsert_seed(saved['id'], OpportunitySeed(**seed))
    assert len(service.search_detail(saved['id'])['properties']) == 2


def test_units_do_not_merge_when_building_url_is_shared(service):
    saved = search(service)
    first = demo.properties(saved['mode'])[0]
    for unit in ['1A', '1B']:
        service.upsert_seed(saved['id'], OpportunitySeed(**(first | {'unit': unit})))
    assert len(service.store.list('opportunities', saved['id'])) == 2


def test_same_exact_property_cross_portal_merges_sources(service):
    saved = search(service)
    first = demo.properties(saved['mode'])[0]
    row, _ = service.upsert_seed(saved['id'], OpportunitySeed(**first))
    other, created = service.upsert_seed(saved['id'], OpportunitySeed(**(first | {'listing_url': 'https://portal.example/property/1', 'source_urls': ['https://portal.example/property/1']})))
    assert not created and other['id'] == row['id']
    assert len(other['source_urls']) == 2


def test_distinct_addresses_sharing_generic_listing_url_do_not_merge(service):
    saved = search(service, 'residential_buy')
    first = demo.properties(saved['mode'])[0]
    for street in ['10 Example St', '20 Example St']:
        service.upsert_seed(saved['id'], OpportunitySeed(**(first | {'address': street})))
    assert len(service.store.list('opportunities', saved['id'])) == 2


def test_mode_can_change_only_before_results(service):
    saved = search(service)
    service.update_search(saved['id'], {'mode': 'residential_buy'})
    saved = service.store.get('searches', saved['id'])
    property_row(service, saved)
    with pytest.raises(AppError, match='Create a new search'):
        service.update_search(saved['id'], {'mode': 'commercial_buy'})


def test_csv_cannot_import_live_check_or_approval_authority(service):
    saved = search(service)
    result = service.import_opportunities(saved['id'], 'properties.csv', b'title,listing_url,listing_status,last_checked_at,source_check,status,price_amount,price_basis\nA unit,https://property.example/1,available,2099-01-01,matched,approved,1900,month\n')
    assert result['imported'] == 1
    row = service.store.list('opportunities', saved['id'])[0]
    assert row['listing_status'] == 'unverified' and row['source_check'] == 'unverified'
    assert row['last_checked_at'] == '' and row['status'] == 'new'


def test_cost_assumption_change_keeps_research_invalidates_drafts(service):
    saved = search(service, 'residential_buy')
    row = researched(service, saved)
    service.update_search(saved['id'], {'cost_assumptions': {'interest_rate_percent': 8}})
    current = service.store.get('opportunities', row['id'])
    assert current['research_status'] == 'complete'
    assert current['drafts'] == [] and current['draft_fingerprint'] == ''
    assert current['estimate']['monthly_total'] > row['estimate']['monthly_total']


def test_writer_route_change_preserves_research(service):
    saved = search(service)
    row = researched(service, saved)
    service.update_search(saved['id'], {'settings': {'routes': {'outreach': {'provider': 'claude', 'model': 'writer-model'}}}})
    current = service.store.get('opportunities', row['id'])
    assert current['research_status'] == 'complete' and not current['drafts']


def test_research_route_change_invalidates_results(service):
    saved = search(service)
    row = researched(service, saved)
    service.update_search(saved['id'], {'settings': {'routes': {'research': {'model': 'new-model'}}}})
    current = service.store.get('opportunities', row['id'])
    assert current['research_status'] == 'pending' and current['match_components'] == []


def test_search_preferences_change_invalidates_approved_copy(service):
    saved = search(service)
    row = researched(service, saved)
    service.update_opportunity(row['id'], {'status': 'approved'})
    service.update_search(saved['id'], {'preferences': {'budget_monthly_max': 1500}})
    current = service.store.get('opportunities', row['id'])
    assert current['status'] == 'new' and current['drafts'] == []
    assert current['eligibility'] == 'blocked'


def test_property_cost_edit_requires_new_copy_but_noop_does_not(service):
    saved = search(service)
    row = researched(service, saved)
    service.update_opportunity(row['id'], {'status': 'approved'})
    same = service.update_opportunity(row['id'], {'costs': {'utilities_monthly': row['costs']['utilities_monthly']}})
    assert same['status'] == 'approved'
    updated = service.update_opportunity(row['id'], {'costs': {'utilities_monthly': 800}})
    assert updated['status'] == 'researched' and not updated['drafts']
    assert updated['costs']['deposit'] == row['costs']['deposit']


def test_property_edit_cannot_mark_fabricated_source_verified(service):
    saved = search(service)
    row = researched(service, saved)
    with pytest.raises(AppError, match='unknown field'):
        service.update_opportunity(row['id'], {'source_check': 'matched', 'last_checked_at': now()})


def test_diligence_can_be_explicitly_confirmed_without_web_claiming_approval(service):
    saved = search(service, 'commercial_lease')
    row = researched(service, saved)
    items = row['due_diligence']
    items[0]['status'] = 'confirmed'
    updated = service.update_opportunity(row['id'], {'due_diligence': items})
    assert updated['due_diligence'][0]['status'] == 'confirmed'
    assert updated['eligibility'] == 'review'


def test_outreach_export_requires_approval_then_one_row_per_email(service):
    saved = search(service)
    row = researched(service, saved)
    with pytest.raises(AppError, match='No eligible'):
        service.export(saved['id'], scope='outreach')
    updated = service.update_opportunity(row['id'], {'status': 'approved'})
    assert updated['ready_to_contact']
    raw = service.export(saved['id'], scope='outreach')
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    assert len(rows) == 1
    assert rows[0]['email_1_body'].endswith(saved['client']['email'])
    assert 'estimated_monthly_total' in rows[0] and 'price_basis' in rows[0]


def test_stale_or_unavailable_property_cannot_enter_contact_export(service):
    saved = search(service)
    row = researched(service, saved)
    row.update(status='approved', last_checked_at=(datetime.now(UTC) - timedelta(days=9)).isoformat())
    service.store.save('opportunities', row)
    assert not service.search_detail(saved['id'])['properties'][0]['ready_to_contact']
    row.update(last_checked_at=now(), listing_status='unavailable')
    service.store.save('opportunities', row)
    assert not service.search_detail(saved['id'])['properties'][0]['ready_to_contact']


def test_prior_property_contact_and_bounce_block_repeat_outreach(service):
    first, second = search(service), search(service)
    a, b = researched(service, first), researched(service, second)
    service.update_opportunity(a['id'], {'workflow_status': 'contacted'})
    checked = service.update_opportunity(b['id'], {'status': 'approved'})
    assert not checked['ready_to_contact'] and checked['prior_activity'] == 'contacted'
    service.update_opportunity(a['id'], {'workflow_status': 'bounced'})
    assert service.store.get('opportunities', b['id'])['email_status'] == 'suppressed'


def test_contact_on_same_broker_domain_does_not_block_different_mailbox(service):
    first, second = search(service), search(service)
    a, b = researched(service, first), researched(service, second, 3)
    service.update_opportunity(a['id'], {'workflow_status': 'contacted'})
    checked = service.update_opportunity(b['id'], {'status': 'approved'})
    assert checked['prior_activity'] == ''


def test_inquiry_claims_need_original_user_text(service):
    saved = search(service)
    row = researched(service, saved)
    row['drafts'][0]['user_claims'] = ['I have lender preapproval.']
    assert any('original brief' in issue for issue in sequence_issues(row, service.client_text(saved['id'])))
    with pytest.raises(AppError, match='resolve sequence checks'):
        service.update_opportunity(row['id'], {'drafts': row['drafts'], 'status': 'approved'})


def test_upload_is_bounded_deduplicated_and_invalidates_research(service):
    saved = search(service)
    row = researched(service, saved)
    first = service.upload_document(saved['id'], '../needs.txt', b'Need a ground-floor loading door.')
    again = service.upload_document(saved['id'], 'needs.txt', b'Need a ground-floor loading door.')
    assert first['name'] == 'needs.txt' and again['duplicate']
    assert service.store.get('opportunities', row['id'])['research_status'] == 'pending'
    assert 'ground-floor' in service.client_text(saved['id'])
    service.delete_document(saved['id'], first['id'])
    assert not service.store.documents(saved['id'])


def test_original_user_material_excludes_agent_generated_profile(service):
    saved = search(service)
    saved['profile'] = {'summary': 'I have ten million in cash.'}
    service.store.save('searches', saved)
    assert 'ten million' not in service.client_text(saved['id'])


def test_demo_cannot_be_switched_to_live_or_exported_for_contact(service):
    saved = service.create_demo()
    with pytest.raises(AppError, match='separate search'):
        service.update_search(saved['id'], {'demo': False, 'settings': {'provider': 'codex'}})
    row = researched(service, saved)
    service.update_opportunity(row['id'], {'status': 'approved'})
    with pytest.raises(AppError, match='No eligible'):
        service.export(saved['id'], scope='outreach')


def test_constraints_override_optimistic_agent_match(service):
    saved = search(service)
    row = researched(service, saved, 4)
    assert row['eligibility'] == 'blocked'
    assert any('floor area' in x for x in row['blockers'])


def test_currency_mismatch_never_claims_within_budget(service):
    saved = search(service)
    row = researched(service, saved)
    row['currency'] = 'EUR'
    score = assess(saved, row)
    budget = next(x for x in score['score_breakdown'] if x['criterion'] == 'budget')
    assert budget['earned'] == 0 and not budget['known']
    assert any('currency' in q for q in score['questions'])


def test_published_email_is_not_mailbox_verified(service):
    saved = search(service)
    row = property_row(service, saved)
    assert row['email_status'] == 'published' and not row['verification_at']
    with pytest.raises(AppError, match='verifier'):
        service.update_opportunity(row['id'], {'email_status': 'verified'})


def test_activity_import_does_not_use_shared_inquiry_url(service):
    saved = search(service, 'residential_buy')
    a, b = property_row(service, saved), property_row(service, saved, 1)
    raw = f'property_id,workflow_status,next_follow_up\n{a["id"]},tour_scheduled,2099-01-01\n'.encode()
    assert service.import_outcomes(saved['id'], 'outcomes.csv', raw)['updated'] == 1
    assert service.store.get('opportunities', a['id'])['workflow_status'] == 'tour_scheduled'
    assert service.store.get('opportunities', b['id'])['workflow_status'] == 'new'


def test_date_only_expiry_includes_entire_day():
    assert not deadline_passed(datetime.now(UTC).date().isoformat())
    assert deadline_passed((datetime.now(UTC) - timedelta(days=1)).date().isoformat())


def test_schema_rejects_nonfinite_costs_and_unsupported_routes():
    with pytest.raises(ValidationError):
        SearchInput(name='x', cost_assumptions={'interest_rate_percent': float('nan')})
    with pytest.raises(ValidationError):
        SearchInput(name='x', preferences={'min_area_sqft': 300, 'max_area_sqft': 200})
    schema = strict_schema(SearchInput)
    assert schema['additionalProperties'] is False
    assert set(schema['required']) == set(schema['properties'])


def test_explicit_different_provider_does_not_inherit_wrong_model():
    settings = SearchInput(name='x', settings={'provider': 'codex', 'model': 'codex-special', 'routes': {'outreach': {'provider': 'claude'}}}).settings.model_dump()
    assert route_for(settings, 'outreach') == {'provider': 'claude', 'model': ''}


def test_identity_requires_traceable_property():
    with pytest.raises(ValueError):
        identity_of({'mode': 'residential_rent', 'title': 'A nice place'})


def test_discovery_filling_unknown_fact_invalidates_old_approval(service):
    saved = search(service)
    row = researched(service, saved)
    row.update(status='approved', price_amount=None)
    service.store.save('opportunities', row)
    updated, created = service.upsert_seed(saved['id'], OpportunitySeed(**demo.properties(saved['mode'])[0]))
    assert not created and updated['price_amount'] is not None
    assert updated['status'] == 'new' and not updated['drafts']
    assert updated['source_check'] == 'unverified'


def test_bad_cost_patch_gives_actionable_validation_error(service):
    saved = search(service)
    row = property_row(service, saved)
    with pytest.raises(AppError) as error:
        service.update_opportunity(row['id'], {'costs': None})
    assert error.value.status == 422


def test_fictional_comparables_show_value_workflow_but_never_live_readiness(service):
    from real_estate_finder.pipeline import Jobs
    saved = service.create_demo('commercial_lease')
    run = Jobs(service).create(saved['id'], 'pipeline', background=False)
    assert run['status'] == 'completed'
    rows = service.search_detail(saved['id'])['properties']
    matched = next(row for row in rows if row['deal_score'] is not None)
    assert matched['deal']['comparable_count'] == 3
    assert matched['deal']['discount_percent'] == 13
    assert all('.example/' in comp['url'] for comp in matched['comparables'])
    assert not matched['ready_to_contact'] and not matched['ready_to_tour']

"""Deterministic, conspicuously fictional fixtures. No real people or properties."""
from __future__ import annotations

from .models import OpportunitySeed, now
from .ranking import default_diligence


def search_input(mode: str = 'residential_rent') -> dict:
    buying, commercial = mode.endswith('_buy'), mode.startswith('commercial')
    labels = {'residential_rent': 'Apartment search', 'residential_buy': 'Home search', 'commercial_lease': 'Workshop lease', 'commercial_buy': 'Workshop purchase'}
    return {
        'name': labels.get(mode, 'Property search') + ' · Demo', 'mode': mode, 'demo': True,
        'objective': 'Find a practical ' + ('workshop for a small fabrication business' if commercial else 'two-bedroom home') + ' in fictional Sample Bay. Compare total costs and prepare a short tour inquiry.',
        'client': {'name': 'Alex Example', 'email': 'alex@example.invalid', 'organization': 'Example Workshop' if commercial else '', 'brief_text': 'I need a workspace with loading access.' if commercial else 'I am looking for a two-bedroom home with parking.'},
        'preferences': {'locations': ['Sample Bay, CA'], 'property_types': ['industrial' if commercial else 'house' if buying else 'apartment'], 'budget_monthly_max': 7000 if commercial else 4200 if buying else 2700, 'purchase_price_max': 900000 if commercial and buying else 500000 if buying else None, 'cash_available': 280000 if commercial and buying else 140000 if buying else 18000 if commercial else 6500, 'min_area_sqft': 2000 if commercial else 800, 'min_bedrooms': None if commercial else 2, 'intended_use': 'Light fabrication; permitted use must be confirmed' if commercial else '', 'must_have': 'Loading door and sufficient power' if commercial else 'Parking and two bedrooms'},
        'cost_assumptions': {'interest_rate_percent': 6.5, 'closing_cost_percent': 3, 'maintenance_monthly': 250 if buying else None, 'mortgage_insurance_monthly': 0, 'repairs_upfront': 4000 if buying else 0, 'fit_out_upfront': 3000 if commercial else 0, 'moving_upfront': 1000 if commercial else 600},
        'settings': {'provider': 'demo', 'target_results': 5, 'discovery_queries': 6, 'max_agent_calls': 30, 'max_minutes': 10, 'min_fit': 55},
    }


def profile_result(search: dict) -> dict:
    return {'summary': 'Fictional example search with cost comparisons and property-level inquiry drafts.', 'search_terms': search['preferences']['property_types'] + search['preferences']['locations'], 'priorities': ['Fit the brief', 'Understand total costs', 'Confirm exact availability'], 'unknowns': ['Representative and lease or purchase terms require a real check'], 'source_plan': ['Direct property or broker listings', 'Comparable asking listings', 'Local planning and public records']}


def properties(mode: str) -> list[dict]:
    buying, commercial = mode.endswith('_buy'), mode.startswith('commercial')
    names = ['Foundry Court', 'Harbor Works', 'Maple Terrace', 'Cedar Lane', 'Atlas Place'] if commercial else ['Juniper Court', 'Harbor House', 'Maple Terrace', 'Cedar Lane', 'Atlas Place']
    prices = [780000, 1050000, 730000, 810000, 690000] if buying and commercial else [420000, 590000, 405000, 455000, 380000] if buying else [18, 27, 16, 21, 25] if commercial else [2100, 3150, 2000, 2250, 2400]
    rows = []
    for i, name in enumerate(names):
        url = f'https://sample-listings.example/{mode}/{i + 1}'
        costs = {'property_tax_annual': 6000 if buying else None, 'insurance_annual': 1800 if buying else 240, 'hoa_monthly': 0 if commercial else 180 if buying else None, 'utilities_monthly': 250 if commercial else 120, 'parking_monthly': 0, 'pet_monthly': 0, 'other_monthly': 0, 'operating_expenses': 5 if commercial else None, 'operating_expenses_basis': 'sqft_year' if commercial else 'unknown', 'deposit': 5000 if commercial else prices[i] if not buying else None, 'application_fee': 0 if commercial else 50, 'other_upfront': 0, 'concession_total': 600 if i == 0 and not buying else 0, 'lease_months': 12, 'source_url': url, 'notes': 'Fictional figures for demonstration only.'}
        if i == 3:
            costs['utilities_monthly'] = None
            costs['other_monthly'] = None
        rows.append(OpportunitySeed(mode=mode, title=name + ' · Fictional', address=f'{100 + i * 20} Example Avenue', unit='Suite A' if commercial else 'Unit 2' if not buying else '', location='Sample Bay, CA', property_type='industrial' if commercial else 'house' if buying else 'apartment', listing_url=url, broker_url='https://sample-broker.example', inquiry_url=url, listing_id=f'DEMO-{mode}-{i + 1}', source_provider='demo', description='FICTIONAL PROPERTY. Demonstrates comparing costs, reviewing facts and preparing inquiries.', features=['Loading door', 'Three-phase power'] if commercial else ['Parking', 'Two bedrooms'], price_amount=prices[i], currency='USD', price_basis='total' if buying else 'sqft_year' if commercial else 'month', area_sqft=2600 if commercial else 980 if i != 4 else 650, bedrooms=None if commercial else 2, bathrooms=None if commercial else 1, lease_type='nnn' if commercial and not buying else 'unknown', costs=costs, posted_at=now(), listing_status='unavailable' if i == 2 else 'available', listing_source='broker', contact_name='Example Leasing Team', contact_role='Listing representative', contact_source=url, email=f'property{i + 1}@sample-broker.example', email_source=url, phone='+1 202-555-01' + str(10 + i), source_urls=[url]).model_dump())
    return rows


def discovery_result(search: dict, limit: int, known: list[str]) -> dict:
    return {'opportunities': [row for row in properties(search['mode']) if row['listing_url'] not in known][:limit], 'coverage_notes': 'Five fictional properties; no live sources or real-world availability.'}


def research_result(row: dict) -> dict:
    seed = {key: row[key] for key in OpportunitySeed.model_fields}
    source = row['listing_url']
    comps = []
    if row['listing_id'].endswith('-1'):
        for index, factor in enumerate((1.10, 1.15, 1.20), 1):
            comps.append({'title': f'Fictional comparable {index}', 'address': f'{400 + index * 10} Sample Avenue', 'unit': row['unit'], 'url': f'https://sample-comps.example/{row["mode"]}/{index}', 'mode': row['mode'], 'property_type': row['property_type'], 'location': row['location'], 'price_amount': round(row['price_amount'] * factor, 2), 'currency': row['currency'], 'price_basis': row['price_basis'], 'area_sqft': row['area_sqft'], 'bedrooms': row['bedrooms'], 'observed_at': now(), 'basis': 'asking', 'lease_type': row['lease_type'], 'rationale': 'Fictional example with the same mode, type, submarket, area and lease structure. Not real market evidence.'})
    return seed | {
        'evidence': [{'url': source, 'title': row['title'], 'quote': 'Fictional listing: ' + row['description'], 'field': 'listing', 'checked_at': now()}],
        'match_components': [{'criterion': key, 'match': 'strong', 'reason': reason, 'source_url': source} for key, reason in [('property', 'Fictional property type matches the requested use.'), ('location', 'Located in the fictional target area.'), ('requirements', 'Advertised features match the brief; verify during a tour.'), ('timing', 'Example listing is newly posted.')]],
        'requirement_checks': [], 'risks': ['Demo only. Ownership, condition and terms are not verified.'],
        'due_diligence': default_diligence(row['mode']), 'comparables': comps,
        'research_summary': 'A fictional property dossier showing known costs, unresolved checks and evidence-backed inquiry drafts.',
        'outreach_angle': 'Ask about exact availability and a tour, then clarify the most material missing cost.',
    }


def sequence_result(row: dict) -> dict:
    name = row['title'].replace(' · Fictional', '')
    return {'drafts': [
        {'step': 1, 'delay_days': 0, 'subject': name + ' availability', 'body': f'Hello,\n\nI saw the listing for {name}. The advertised layout caught my interest. Is this exact property still available for a tour?', 'source_urls': [row['listing_url']], 'user_claims': [], 'angle': 'Exact property availability'},
        {'step': 2, 'delay_days': 4, 'subject': name + ' costs', 'body': f'Hello,\n\nI am reviewing {name}. Before planning a visit, could you share the mandatory recurring charges and upfront costs?', 'source_urls': [row['listing_url']], 'user_claims': [], 'angle': 'Cost clarity'},
        {'step': 3, 'delay_days': 9, 'subject': name + ' next step', 'body': f'Hello,\n\nThis is my last note about {name}. If the listing is no longer available, I will close it in my search. Is there a current listing page I should use?', 'source_urls': [row['listing_url']], 'user_claims': [], 'angle': 'Respectful close'},
    ]}

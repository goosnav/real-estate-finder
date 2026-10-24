"""Deterministic, unit-aware cost estimates; unknown charges never become zero."""
from __future__ import annotations

from statistics import median

from .models import canonical_url


def monthly_amount(amount: float | None, basis: str, area_sqft: float | None = None) -> float | None:
    if amount is None:
        return None
    if basis == 'month':
        return amount
    if basis == 'year':
        return amount / 12
    if basis in {'sqft_year', 'sqft_month'} and area_sqft:
        return amount * area_sqft / (12 if basis == 'sqft_year' else 1)
    return None


def mortgage_payment(principal: float, annual_rate: float, years: int) -> float:
    months, rate = years * 12, annual_rate / 1200
    if not principal:
        return 0
    return principal / months if rate == 0 else principal * rate / (1 - (1 + rate) ** -months)


def estimate_costs(search: dict, row: dict) -> dict:
    mode, listing = row['mode'], row.get('costs', {})
    assumptions = search.get('cost_assumptions', {})
    buying, commercial = mode.endswith('_buy'), mode.startswith('commercial')
    monthly, upfront, monthly_unknown, cash_unknown, used = [], [], [], [], []
    currency = row.get('currency', '')
    purchase, base, payment = None, None, None

    def add(target, label, amount, origin):
        if amount is not None:
            target.append({'label': label, 'amount': round(amount, 2), 'basis': origin})

    def chosen(key):
        if assumptions.get(key) is not None:
            used.append(key.replace('_', ' ') + ' = ' + str(assumptions[key]))
            return assumptions[key], 'user assumption'
        return listing.get(key), 'cited listing; review source' if listing.get('source_url') else 'entered property cost; no source'

    def recurring(key, label, divisor=1):
        amount, origin = chosen(key)
        if amount is None:
            monthly_unknown.append(label)
        else:
            add(monthly, label, amount / divisor, origin)

    def initial(key, label, source='listing'):
        amount = assumptions.get(key) if source == 'assumption' else listing.get(key)
        if amount is None:
            cash_unknown.append(label)
        else:
            add(upfront, label, amount, 'user assumption' if source == 'assumption' else 'listing; review source')
            if source == 'assumption':
                used.append(label + ' = ' + str(amount))

    if buying:
        if row.get('price_basis') == 'total' and row.get('price_amount') is not None:
            purchase = row['price_amount']
            fraction = 1 if assumptions.get('financing') == 'cash' else assumptions.get('down_payment_percent', 20) / 100
            add(upfront, 'Purchase cash' if fraction == 1 else 'Down payment', purchase * fraction, 'user financing assumption')
            principal = purchase * (1 - fraction)
            used.append('Cash purchase' if fraction == 1 else f'{fraction * 100:g}% down payment')
            rate = assumptions.get('interest_rate_percent')
            if principal and rate is None:
                monthly_unknown.append('Loan interest rate')
            else:
                payment = mortgage_payment(principal, rate or 0, assumptions.get('loan_years', 30))
                add(monthly, 'Loan principal and interest', payment, 'fixed-rate amortization scenario')
                if principal:
                    used.append(f'{rate:g}% fixed rate; {assumptions.get("loan_years", 30)} years; no balloon')
            close = assumptions.get('closing_cost_percent')
            if close is None:
                cash_unknown.append('Closing costs and prepaid charges')
            else:
                add(upfront, 'Closing costs and prepaid charges', purchase * close / 100, 'percentage of purchase price; user assumption')
                used.append(f'{close:g}% closing costs; confirm actual cash to close')
        else:
            monthly_unknown.append('Purchase price with total-price basis')
            cash_unknown.append('Purchase price with total-price basis')
        for key, label, divisor in [('property_tax_annual', 'Property taxes', 12), ('insurance_annual', 'Property insurance', 12), ('hoa_monthly', 'Association dues', 1), ('maintenance_monthly', 'Maintenance reserve', 1), ('utilities_monthly', 'Utilities', 1), ('other_monthly', 'Other recurring charges', 1)]:
            recurring(key, label, divisor)
        if assumptions.get('financing') != 'cash' and assumptions.get('down_payment_percent', 20) < 100:
            recurring('mortgage_insurance_monthly', 'Mortgage insurance or loan fees')
        initial('repairs_upfront', 'Repairs and inspections', 'assumption')
    else:
        base = monthly_amount(row.get('price_amount'), row.get('price_basis', 'unknown'), row.get('area_sqft'))
        if base is None:
            monthly_unknown.append('Base rent with known period and area')
            cash_unknown.append('First month base rent')
        else:
            add(monthly, 'Base rent', base, row['price_basis'])
            add(upfront, 'First month base rent', base, 'one month; confirm payment timing')
        for key, label in [('utilities_monthly', 'Utilities'), ('parking_monthly', 'Parking'), ('pet_monthly', 'Pet charges'), ('other_monthly', 'Other recurring charges')]:
            recurring(key, label)
        recurring('insurance_annual', 'Tenant insurance', 12)
        if commercial:
            operating = monthly_amount(listing.get('operating_expenses'), listing.get('operating_expenses_basis', 'unknown'), row.get('area_sqft'))
            if operating is None:
                monthly_unknown.append('Operating expenses / NNN / CAM (enter zero if included)')
            else:
                add(monthly, 'Operating expenses / NNN / CAM', operating, listing.get('operating_expenses_basis', 'unknown'))
            if row.get('lease_type') == 'unknown':
                monthly_unknown.append('Lease structure and included charges')
        for key, label in [('deposit', 'Refundable deposit (confirm terms)'), ('application_fee', 'Application / screening fees'), ('other_upfront', 'Other upfront charges')]:
            initial(key, label)
    if commercial:
        initial('fit_out_upfront', 'Build-out, permits and equipment', 'assumption')
    initial('moving_upfront', 'Moving / setup', 'assumption')
    if not currency:
        monthly_unknown.append('Currency')
        cash_unknown.append('Currency')
    known_monthly = round(sum(item['amount'] for item in monthly), 2)
    # First month's recurring costs are part of a rental move-in budget, but not
    # all are necessarily payable to the landlord on signing. Keep this explicit.
    if not buying:
        extras = sum(item['amount'] for item in monthly if item['label'] != 'Base rent')
        add(upfront, 'First month other recurring costs', extras, 'budget reserve; payment timing may differ')
        cash_unknown.extend(monthly_unknown)
    known_cash = round(sum(item['amount'] for item in upfront), 2)
    total = None if monthly_unknown else known_monthly
    effective = None
    concession = listing.get('concession_total')
    term = listing.get('lease_months')
    if not buying and total is not None and concession is not None and term:
        effective = round(max(0, total - concession / term), 2)
        used.append('Concession spread over stated lease; gross recurring rent remains payable unless terms say otherwise')
    return {
        'currency': currency, 'monthly_total': total, 'known_monthly': known_monthly,
        'base_monthly': round(base, 2) if base is not None else None, 'purchase_price': purchase,
        'mortgage_payment': round(payment, 2) if payment is not None else None,
        'effective_monthly': effective, 'cash_needed': None if cash_unknown else known_cash,
        'known_cash_needed': known_cash, 'monthly_breakdown': monthly, 'cash_breakdown': upfront,
        'unknowns': list(dict.fromkeys(monthly_unknown + cash_unknown)), 'monthly_unknowns': monthly_unknown,
        'cash_unknowns': list(dict.fromkeys(cash_unknown)), 'assumptions': used,
        'complete': not monthly_unknown and not cash_unknown,
    }


def comparable_price(row: dict) -> tuple[float | None, str]:
    buying = row.get('mode', '').endswith('_buy')
    amount, area = row.get('price_amount'), row.get('area_sqft')
    if buying:
        return (amount / area, 'price / sqft') if amount is not None and area and row.get('price_basis') == 'total' else (None, 'price / sqft')
    monthly = monthly_amount(amount, row.get('price_basis', 'unknown'), area)
    if row.get('mode', '').startswith('commercial'):
        return (monthly / area, 'monthly rent / sqft') if monthly is not None and area else (None, 'monthly rent / sqft')
    return monthly, 'monthly base rent'


def deal_comparison(row: dict) -> dict:
    from .ranking import date_age
    price, unit = comparable_price(row)
    valid, used_urls, used_properties, excluded = [], set(), set(), 0
    verified = {canonical_url(url) for url in row.get('verified_comparable_urls', [])}
    target = canonical_url(row.get('listing_url', ''))
    target_identity = '|'.join(str(row.get(key, '')).strip().casefold() for key in ('address', 'unit', 'location')) if row.get('address') else target
    for comp in row.get('comparables', []):
        url = canonical_url(comp['url'])
        identity = '|'.join(str(comp.get(key, '')).strip().casefold() for key in ('address', 'unit', 'location')) if comp.get('address') else url
        age = date_age(comp.get('observed_at', ''))
        value, comp_unit = comparable_price(comp)
        valid_comp = (
            url in verified and url != target and identity != target_identity and url not in used_urls and identity not in used_properties and value is not None
            and comp_unit == unit and comp['mode'] == row['mode']
            and comp['currency'] == row.get('currency') and bool(row.get('currency'))
            and comp['property_type'].casefold() == row.get('property_type', '').casefold() and bool(row.get('property_type'))
            and comp['location'].casefold() == row.get('location', '').casefold() and bool(row.get('location'))
            and comp.get('basis') == 'asking' and age is not None and 0 <= age <= 45
        )
        if row['mode'].startswith('commercial') and not row['mode'].endswith('_buy'):
            valid_comp = valid_comp and row.get('lease_type') != 'unknown' and comp.get('lease_type') == row.get('lease_type')
        if row['mode'] == 'residential_rent':
            valid_comp = valid_comp and row.get('bedrooms') is not None and comp.get('bedrooms') == row.get('bedrooms')
        if row.get('area_sqft') and comp.get('area_sqft'):
            valid_comp = valid_comp and 0.7 <= comp['area_sqft'] / row['area_sqft'] <= 1.3
        elif row['mode'] != 'residential_rent':
            valid_comp = False
        if valid_comp:
            valid.append(value)
            used_urls.add(url)
            used_properties.add(identity)
        else:
            excluded += 1
    result = {'score': None, 'comparable_count': len(valid), 'excluded_count': excluded, 'median_unit_price': None, 'listing_unit_price': price, 'discount_percent': None, 'unit': unit, 'label': 'Insufficient comparable evidence', 'source_urls': sorted(used_urls)}
    target_age = date_age(row.get('last_checked_at', ''))
    target_checked = row.get('source_check') == 'matched' and row.get('listing_status') == 'available' and target_age is not None and -0.01 <= target_age <= 7
    if len(valid) >= 3 and price is not None and price > 0 and median(valid) > 0 and target_checked:
        midpoint = median(valid)
        discount = (midpoint - price) / midpoint * 100
        result.update(score=round(max(0, min(100, 50 + discount * 2))), median_unit_price=round(midpoint, 2), discount_percent=round(discount, 1), label='Asking-price comparison; not an appraisal')
    return result

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from real_estate_finder import sources
from real_estate_finder.research import PublicDocument, SourceUnavailable

URL = "https://broker.example/listings/101"


def listing(**overrides):
    return {
        "@type": "RealEstateListing", "name": "101 Example Street", "url": URL,
        "datePosted": "2026-10-01", "mainEntity": {
            "@type": "Apartment", "name": "101 Example Street", "address": "101 Example Street, Denver",
            "floorSize": {"value": 1000, "unitCode": "FTK"}, "numberOfBedrooms": 2,
            "numberOfBathroomsTotal": 1,
        },
        "offers": {
            "@type": "Offer", "price": 2400, "priceCurrency": "USD",
            "businessFunction": "http://purl.org/goodrelations/v1#LeaseOut",
            "availability": "https://schema.org/InStock",
            "priceSpecification": {"@type": "UnitPriceSpecification", "unitText": "month"},
        }, **overrides,
    }


def page(monkeypatch, node, text="101 Example Street available now", *, html=None):
    raw = html or '<title>Apartment</title><script type="application/ld+json">' + json.dumps(node) + '</script><p>' + text + '</p>'
    calls = []
    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        return PublicDocument(url, raw, "text/html")
    monkeypatch.setattr(sources, "fetch_public_document", fetch)
    return calls


def test_one_fetch_retains_text_and_exact_units(monkeypatch):
    calls = page(monkeypatch, listing())
    result = sources.inspect_listing(URL)
    assert len(calls) == 1
    item = result["listings"][0]
    assert item["price_amount"] == 2400 and item["price_basis"] == "month"
    assert item["area_sqft"] == 1000 and item["bedrooms"] == 2
    assert item["listing_status"] == "available" and item["transaction"] == "rent"
    assert result["text"] == "Apartment 101 Example Street available now"
    assert "application/ld+json" in calls[0][1]["allowed_types"]


def test_http_success_never_proves_availability(monkeypatch):
    page(monkeypatch, {}, text="Welcome to our real estate website")
    result = sources.inspect_listing(URL)
    assert result["listing_status"] == "unverified"
    assert result["listings"] == []


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_denied_and_server_error_remain_unknown(monkeypatch, status):
    def fail(*a, **kw):
        raise SourceUnavailable("Source unavailable", status_code=status)
    monkeypatch.setattr(sources, "fetch_public_document", fail)
    assert sources.inspect_listing(URL)["listing_status"] == "unverified"


@pytest.mark.parametrize("status", [404, 410])
def test_removed_listing_distinguished_from_access_denied(monkeypatch, status):
    def fail(*a, **kw):
        raise SourceUnavailable("Source unavailable", status_code=status)
    monkeypatch.setattr(sources, "fetch_public_document", fail)
    result = sources.inspect_listing(URL)
    assert result["listing_status"] == "unavailable"
    assert str(status) in result["status_basis"]


def test_ambiguous_price_never_infers_period_or_area_unit():
    node = listing()
    node["offers"].pop("priceSpecification")
    node["mainEntity"]["floorSize"]["unitCode"] = "unknown"
    result = sources.extract_listings(node, URL)[0]
    assert result["price_basis"] == "unknown" and result["area_sqft"] is None


@pytest.mark.parametrize(("unit", "expected"), [("$/SF/YR", "sqft_year"), ("sqft/month", "sqft_month"), ("year", "year")])
def test_commercial_per_area_prices_keep_explicit_period(unit, expected):
    node = listing()
    node["offers"]["priceSpecification"]["unitText"] = unit
    assert sources.extract_listings(node, URL)[0]["price_basis"] == expected


def test_square_metres_convert_only_with_unit_and_zero_is_unknown():
    node = listing()
    node["mainEntity"]["floorSize"] = {"value": 100, "unitCode": "MTK"}
    assert sources.extract_listings(node, URL)[0]["area_sqft"] == pytest.approx(1076.391, abs=0.001)
    node["mainEntity"]["floorSize"]["value"] = 0
    assert sources.extract_listings(node, URL)[0]["area_sqft"] is None


def test_graph_reference_resolves_property_without_duplicate_offer():
    node = listing()
    prop = node.pop("mainEntity") | {"@id": "#property"}
    offer = node.pop("offers") | {"@id": "#offer", "itemOffered": {"@id": "#property"}}
    node.update(mainEntity={"@id": "#property"}, offers={"@id": "#offer"})
    result = sources.extract_listings({"@graph": [node, prop, offer]}, URL)
    assert len(result) == 1
    assert result[0]["area_sqft"] == 1000


def test_conflicting_units_and_multiple_offers_never_choose_first():
    node = listing()
    one, two = dict(node), listing()
    two["offers"]["price"] = 2700
    rows = sources.extract_listings([one, two], URL)
    assert len(rows) == 2
    assert sources.matching_listing({"listing_url": URL, "mode": "residential_rent"}, rows) is None
    rows = sources.extract_listings(one, URL)
    assert sources.matching_listing({"listing_url": URL, "mode": "residential_rent", "unit": "2B"}, rows) is None


def test_different_address_does_not_match_even_on_same_listing_url():
    rows = sources.extract_listings(listing(), URL)
    assert sources.matching_listing({"listing_url": URL, "mode": "residential_rent", "address": "999 Other Street"}, rows) is None


def test_opposite_transaction_is_not_imported_as_same_property():
    node = listing()
    node["offers"]["businessFunction"] = "http://purl.org/goodrelations/v1#Sell"
    rows = sources.extract_listings(node, URL)
    assert sources.matching_listing({"listing_url": URL, "mode": "residential_rent"}, rows) is None


def test_date_only_validity_includes_whole_day():
    today = datetime.now(UTC).date().isoformat()
    assert sources.expired(today) is False
    assert sources.expired("2000-01-01") is True
    node = listing()
    node["offers"]["validThrough"] = "2000-01-01"
    assert sources.extract_listings(node, URL)[0]["listing_status"] == "unavailable"


def test_hidden_script_and_generic_product_not_real_estate(monkeypatch):
    page(monkeypatch, {}, html='<script>document.write("available now")</script><p>Property</p><script type="application/ld+json">{"@type":"Product","name":"Phone","offers":{"@type":"Offer","price":200}}</script>')
    result = sources.inspect_listing(URL)
    assert result["listings"] == []
    assert result["text"] == "Property"


def test_oversized_or_invalid_jsonld_does_not_hide_visible_page(monkeypatch):
    page(monkeypatch, {}, html='<p>101 Example Street</p><script type="application/ld+json">' + 'x' * 100001 + '</script><script type="application/ld+json">{bad}</script>')
    result = sources.inspect_listing(URL)
    assert result["listings"] == [] and result["warnings"]
    assert result["text"] == "101 Example Street"


def test_non_finite_and_bool_prices_are_unknown():
    for price in [True, float("nan"), float("inf"), -1]:
        node = listing()
        node["offers"]["price"] = price
        assert sources.extract_listings(node, URL)[0]["price_amount"] is None


@pytest.mark.parametrize(("mode", "phrase", "expected"), [
    ("residential_rent", "available for sale", "unverified"),
    ("commercial_lease", "for sale, available now", "unverified"),
    ("residential_buy", "available for rent", "unverified"),
    ("commercial_buy", "available for lease", "unverified"),
    ("residential_rent", "available for rent", "available"),
    ("commercial_lease", "available for lease", "available"),
    ("residential_buy", "available for sale", "available"),
    ("commercial_buy", "available for sale", "available"),
])
def test_visible_status_respects_transaction_type(mode, phrase, expected):
    result = sources.unstructured_status({"mode": mode, "address": "100 Example Street", "unit": ""}, "100 Example Street " + phrase)
    assert result["listing_status"] == expected
    assert result["identity_matched"] is True


@pytest.mark.parametrize("text", [
    "100 Example Street Unit 2 not available. Unit 3 available now.",
    "100 Example Street Unit 3 available now.",
    "100 Example Street Unit 2. Available units: Unit 2, Unit 3.",
    "100 Example Street Unit 2. Similar properties: 200 Other Avenue available now.",
])
def test_neighbouring_unit_or_other_property_cannot_supply_available_status(text):
    result = sources.unstructured_status({"mode": "residential_rent", "address": "100 Example Street", "unit": "Unit 2"}, text)
    assert result["listing_status"] == "unverified" and result["identity_matched"] is False


def test_exact_combined_address_unit_matches_a_single_property():
    result = sources.unstructured_status({"mode": "commercial_lease", "address": "100 Example Street", "unit": "Suite A"}, "100 Example Street, Suite A is currently available for lease.")
    assert result["listing_status"] == "available" and result["identity_matched"] is True


def test_missing_unit_cannot_assume_building_availability_is_its_own():
    result = sources.unstructured_status({"mode": "residential_rent", "address": "100 Example Street", "unit": ""}, "100 Example Street Unit 3 available now")
    assert result["listing_status"] == "unverified" and result["identity_matched"] is False


def test_unknown_comparable_unit_can_be_resolved_only_from_one_exact_url():
    node = listing()
    node["mainEntity"]["unitNumber"] = "2A"
    rows = sources.extract_listings(node, URL)
    identity = {"listing_url": URL, "mode": "residential_rent"}
    assert sources.matching_listing(identity, rows) is None
    assert sources.matching_listing(identity, rows, allow_missing_unit=True)["unit"] == "2A"
    identity["unit"] = "2B"
    assert sources.matching_listing(identity, rows, allow_missing_unit=True) is None


def test_structured_locality_is_distinct_from_street_name():
    node = listing()
    node["mainEntity"]["address"] = {"@type": "PostalAddress", "streetAddress": "100 Denver Street", "addressLocality": "Aurora", "addressRegion": "CO"}
    result = sources.extract_listings(node, URL)[0]
    assert result["location"] == "Aurora, CO"
    assert sources.same_locality("Denver", result["location"]) is False
    assert sources.same_locality("Aurora", result["location"]) is True
    assert sources.same_locality("York", "New York, NY") is False
    assert sources.same_locality("New York, NY", "New York, NY") is True


def test_explicit_commercial_type_is_preserved_without_inferring_permitted_use():
    node = listing()
    node["mainEntity"]["@type"] = "Place"
    node["mainEntity"]["additionalType"] = "industrial"
    result = sources.extract_listings(node, URL)[0]
    assert result["property_type"] == "industrial"
    assert "permitted_use" not in result

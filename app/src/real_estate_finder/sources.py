"""Bounded public real-estate source inspection.

JSON-LD is publisher-supplied evidence, not a verification of ownership, legal use,
or market value. An HTTP success alone never confirms availability. No portal
account, undocumented API, form submission, or access-control bypass is used.
"""
from __future__ import annotations

import json
import math
import re
import threading
from datetime import UTC, datetime
from datetime import time as daytime
from html.parser import HTMLParser
from urllib.parse import urljoin

from .models import canonical_url, now, public_url
from .research import SourceUnavailable, TextParser, fetch_public_document

PROPERTY_TYPES = {"Apartment", "House", "SingleFamilyResidence", "Residence", "Accommodation", "ApartmentComplex", "Place", "Product"}
SQM_TO_SQFT = 10.763910416709722


def _text(value, limit: int = 1000) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _url(value, base: str = "") -> str:
    value = _text(value, 2048)
    if not value:
        return ""
    try:
        # Fragment identifiers name graph nodes, not distinct listing pages.
        if value.startswith("#"):
            return ""
        return public_url(urljoin(base, value))
    except ValueError:
        return ""


def _kind(value) -> set[str]:
    types = value.get("@type", []) if isinstance(value, dict) else []
    types = [types] if isinstance(types, str) else types
    return {str(t).rsplit("/", 1)[-1] for t in types[:10]} if isinstance(types, list) else set()


def _date(value) -> str:
    value = _text(value, 100)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError:
        return ""


def expired(value: str) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if len(value) == 10:
            parsed = datetime.combine(parsed.date(), daytime.max)
        parsed = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        return parsed < datetime.now(UTC)
    except ValueError:
        return False


class ListingParser(HTMLParser):
    """Collect bounded JSON-LD without executing page code."""
    def __init__(self):
        super().__init__()
        self.title_parts, self.scripts = [], []
        self.current, self.current_size = None, 0
        self.in_title, self.hidden = False, 0

    def handle_starttag(self, tag, attrs):
        if tag in {"noscript", "svg", "style"}:
            self.hidden += 1
        if tag == "title" and not self.hidden:
            self.in_title = True
        if tag == "script" and not self.hidden and len(self.scripts) < 20:
            if (dict(attrs).get("type") or "").casefold().split(";", 1)[0].strip() == "application/ld+json":
                self.current, self.current_size = [], 0

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "script" and self.current is not None:
            if self.current_size <= 100_000:
                self.scripts.append("".join(self.current))
            self.current = None
        if tag in {"noscript", "svg", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data[:1000])
        if self.current is not None:
            self.current_size += len(data)
            if self.current_size <= 100_000:
                self.current.append(data)


def _nodes(value, depth: int = 0):
    if depth > 10:
        return
    if isinstance(value, list):
        for child in value[:100]:
            yield from _nodes(child, depth + 1)
    elif isinstance(value, dict):
        yield value
        for key in ("@graph", "mainEntity", "itemListElement", "item", "about", "offers", "itemOffered"):
            if key in value:
                yield from _nodes(value[key], depth + 1)


def _resolved(value, ids: dict) -> dict:
    if not isinstance(value, dict):
        return {}
    linked = ids.get(value.get("@id"), {})
    return linked | value


def _address(value) -> str:
    if isinstance(value, str):
        return value.strip()[:600]
    if isinstance(value, dict):
        country = value.get("addressCountry")
        country = country.get("name") if isinstance(country, dict) else country
        return ", ".join(_text(v) for v in (
            value.get("streetAddress"), value.get("addressLocality"), value.get("addressRegion"),
            value.get("postalCode"), country,
        ) if _text(v))[:600]
    return ""



def _location(value) -> str:
    if isinstance(value, dict):
        return ", ".join(_text(value.get(key)) for key in ("addressLocality", "addressRegion") if _text(value.get(key)))[:500]
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",") if part.strip()]
        # A free-form street line cannot establish a city simply because the
        # proposed city name appears somewhere in that street's name.
        return ", ".join(parts[1:])[:500] if len(parts) > 1 else ""
    return ""


def _property_type(prop: dict) -> str:
    types = _kind(prop)
    if "Apartment" in types:
        return "apartment"
    if types & {"House", "SingleFamilyResidence"}:
        return "house"
    additional = prop.get("additionalType") or prop.get("category")
    additional = additional if isinstance(additional, list) else [additional]
    supported = {"industrial", "warehouse", "office", "retail", "land", "mixed_use"}
    for value in additional[:10]:
        text = _text(value).rsplit("/", 1)[-1].casefold().replace(" ", "_")
        if text in supported:
            return text
    return ""


def same_locality(claimed: str, actual: str) -> bool:
    def parts(value):
        return [re.sub(r"\s+", " ", part).strip().casefold() for part in value.split(",") if part.strip()]
    requested, observed = parts(claimed), parts(actual)
    return bool(requested and observed and observed[:len(requested)] == requested)


def _area(value) -> float | None:
    if not isinstance(value, dict):
        return None
    amount = _number(value.get("value"))
    unit = _text(value.get("unitCode") or value.get("unitText")).casefold().replace(" ", "")
    if amount is None or amount == 0:
        return None
    if unit in {"ftk", "sqft", "ft2", "ft²", "squarefeet", "squarefoot"}:
        return round(amount, 3)
    if unit in {"mtk", "sqm", "m2", "m²", "squaremetres", "squaremeters"}:
        return round(amount * SQM_TO_SQFT, 3)
    return None


def _price(offer: dict) -> dict:
    spec = offer.get("priceSpecification")
    # Multiple price specifications may describe fees or tiers, not one rent.
    spec = spec if isinstance(spec, dict) else {}
    amount = _number(offer.get("price", spec.get("price")))
    currency = _text(offer.get("priceCurrency") or spec.get("priceCurrency"), 10).upper()
    basis = "unknown"
    unit = _text(spec.get("unitText") or spec.get("unitCode")).casefold()
    reference = spec.get("referenceQuantity")
    reference = reference if isinstance(reference, dict) else {}
    duration = _text(spec.get("billingDuration")).upper()
    unit_text = re.sub(r"[\s_]+", " ", unit).strip()
    if unit_text in {"month", "monthly", "mon", "per month", "/month"} or duration == "P1M":
        basis = "month"
    elif unit_text in {"year", "annual", "yearly", "ann", "per year", "/year"} or duration == "P1Y":
        basis = "year"
    per_foot = unit_text in {"sqft/year", "$/sf/yr", "sf/year", "sf/yr", "sqft/month", "$/sf/mo", "sf/month", "sf/mo"}
    reference_unit = _text(reference.get("unitCode") or reference.get("unitText")).casefold()
    if _number(reference.get("value")) == 1 and reference_unit in {"ftk", "sqft", "square feet"}:
        per_foot = True
    if per_foot:
        if "year" in unit_text or "/yr" in unit_text or basis == "year":
            basis = "sqft_year"
        elif "month" in unit_text or "/mo" in unit_text or basis == "month":
            basis = "sqft_month"
    function = _text(offer.get("businessFunction"))
    transaction = "rent" if function.endswith(("LeaseOut", "Lease")) else "buy" if function.endswith("Sell") else "unknown"
    if transaction == "buy" and basis == "unknown":
        basis = "total"
    return {"price_amount": amount, "currency": currency, "price_basis": basis, "transaction": transaction}


def _metadata(listing: dict, prop: dict, offer: dict, source: str) -> dict:
    raw_address = prop.get("address") or listing.get("address")
    address = _address(raw_address)
    explicit_url = _url(offer.get("url"), source) or _url(listing.get("url"), source) or _url(prop.get("url"), source)
    title = _text(listing.get("name") or prop.get("name") or address, 300)
    status, basis = "unverified", "Publisher did not state current availability"
    availability = _text(offer.get("availability")).rsplit("/", 1)[-1]
    if availability in {"InStock", "LimitedAvailability"}:
        status, basis = "available", "Matching Offer availability: " + availability
    elif availability in {"OutOfStock", "SoldOut", "Discontinued"}:
        status, basis = "unavailable", "Matching Offer availability: " + availability
    valid_through = _date(offer.get("validThrough") or offer.get("availabilityEnds"))
    if expired(valid_through):
        status, basis = "unavailable", "Matching offer validity has expired"
    identifier = prop.get("identifier") or listing.get("identifier")
    identifier = identifier.get("value") if isinstance(identifier, dict) else identifier
    property_type = _property_type(prop)
    rooms = prop.get("numberOfBedrooms")
    rooms = rooms.get("value") if isinstance(rooms, dict) else rooms
    baths = prop.get("numberOfBathroomsTotal")
    baths = baths.get("value") if isinstance(baths, dict) else baths
    return {
        "title": title, "address": address, "unit": _text(prop.get("unitNumber"), 100),
        "location": _location(raw_address), "property_type": property_type,
        "listing_url": explicit_url or source, "listing_url_explicit": bool(explicit_url),
        "listing_id": str(identifier)[:200] if isinstance(identifier, (str, int)) else "",
        "description": _text(prop.get("description") or listing.get("description"), 8000),
        "area_sqft": _area(prop.get("floorSize")), "bedrooms": _number(rooms), "bathrooms": _number(baths),
        "posted_at": _date(listing.get("datePosted") or listing.get("datePublished")),
        "valid_through": valid_through, "available_from": _date(offer.get("availabilityStarts")), "listing_status": status, "status_basis": basis,
        **_price(offer),
    }


def extract_listings(parsed, source: str) -> list[dict]:
    """Extract concrete listings; do not merge units or prices from a collection."""
    nodes = list(_nodes(parsed))[:1000]
    ids = {}
    for node in nodes:
        if isinstance(node.get("@id"), str) and len(node) > len(ids.get(node["@id"], {})):
            ids[node["@id"]] = node
    listings, seen = [], set()
    for node in sorted(nodes, key=lambda n: "RealEstateListing" not in _kind(n)):
        kinds = _kind(node)
        if not ("RealEstateListing" in kinds or ((kinds & (PROPERTY_TYPES - {"Product", "Place"}) or node.get("address")) and node.get("offers")) or ("Offer" in kinds and node.get("itemOffered"))):
            continue
        listing = node
        if "Offer" in kinds:
            offers = [node]
            prop = _resolved(node.get("itemOffered"), ids)
            if not (_kind(prop) & PROPERTY_TYPES or prop.get("address")):
                continue
        else:
            entity = node.get("mainEntity") or node.get("about")
            prop = _resolved(entity, ids) or node
            offers = node.get("offers") or prop.get("offers") or []
            offers = offers if isinstance(offers, list) else [offers]
        # A property without an Offer remains discoverable but unpriced/unverified.
        for value in (offers or [{}])[:40]:
            offer = _resolved(value, ids)
            offered = _resolved(offer.get("itemOffered"), ids)
            item = offered or prop
            if not (_address(item.get("address")) or _text(item.get("name")) or "RealEstateListing" in kinds):
                continue
            metadata = _metadata(listing, item, offer, source)
            if not metadata["title"]:
                continue
            signature = json.dumps({key: value for key, value in metadata.items() if key not in {"title", "description", "posted_at", "status_basis", "listing_url_explicit"}}, sort_keys=True)
            if signature in seen:
                continue
            seen.add(signature)
            listings.append(metadata)
            if len(listings) >= 40:
                return listings
    return listings


def inspect_listing(url: str, cancel: threading.Event | None = None) -> dict:
    """One bounded fetch gives visible text and structured property records."""
    result = {"url": url, "title": "", "text": "", "listings": [], "listing_status": "unverified", "status_basis": "", "checked_at": now(), "requests": 0, "warnings": []}
    try:
        document = fetch_public_document(url, cancel=cancel, allowed_types=("text/html", "text/plain", "application/xhtml+xml", "application/ld+json"))
    except SourceUnavailable as exc:
        result["requests"] = 1 if exc.status_code else 0
        result["warnings"].append(str(exc))
        if exc.status_code in {404, 410}:
            result.update(listing_status="unavailable", status_basis=f"Listing URL returned HTTP {exc.status_code}; removal reason is unknown")
        return result
    result.update(url=document.url, requests=document.requests)
    parser = ListingParser()
    if "html" in document.content_type:
        visible = TextParser()
        visible.feed(document.text)
        result["text"] = re.sub(r"\s+", " ", " ".join(visible.parts)).strip()[:30000]
        parser.feed(document.text)
        result["title"] = re.sub(r"\s+", " ", " ".join(parser.title_parts)).strip()[:500]
        scripts = parser.scripts
    elif "json" in document.content_type:
        scripts = [document.text]
    else:
        result["text"], scripts = document.text[:30000], []
    for script in scripts:
        try:
            parsed = json.loads(script)
        except (ValueError, RecursionError):
            result["warnings"].append("Ignored invalid real-estate JSON-LD.")
            continue
        result["listings"].extend(extract_listings(parsed, document.url))
        if len(result["listings"]) >= 40:
            result["listings"] = result["listings"][:40]
            break
    result["status_basis"] = "Page is reachable; match one property and an explicit offer before confirming availability"
    return result


def matching_listing(opportunity: dict, listings: list[dict], *, allow_missing_unit: bool = False) -> dict | None:
    """Reject conflicting units, identities, and ambiguous multi-offer pages."""
    matches = []
    target_url = canonical_url(opportunity.get("listing_url", ""))
    def normalize(value):
        return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()
    for listing in listings:
        old_id, new_id = opportunity.get("listing_id", ""), listing.get("listing_id", "")
        if old_id and new_id and old_id.casefold() != new_id.casefold():
            continue
        old_unit, new_unit = normalize(opportunity.get("unit")), normalize(listing.get("unit"))
        if old_unit != new_unit and (old_unit or new_unit) and not (allow_missing_unit and not old_unit):
            continue
        transaction = listing.get("transaction", "unknown")
        if transaction != "unknown" and transaction != ("buy" if opportunity.get("mode", "").endswith("buy") else "rent"):
            continue
        address_match = bool(opportunity.get("address") and normalize(opportunity["address"]) == normalize(listing.get("address")))
        url_match = bool(target_url and listing.get("listing_url_explicit") and canonical_url(listing.get("listing_url", "")) == target_url)
        id_match = bool(old_id and new_id and old_id.casefold() == new_id.casefold())
        if opportunity.get("address") and listing.get("address") and not address_match:
            continue
        if address_match or url_match or id_match:
            if listing not in matches:
                matches.append(listing)
    return matches[0] if len(matches) == 1 else None


_UNIT_MARKER = re.compile(r"\b(?:unit|suite|apt\.?|apartment|flat|space)\s*#?\s*([a-z]\d{1,4}[a-z]?|\d{1,5}(?:-[a-z0-9]{1,4})?[a-z]?|[a-z])\b|#\s*(\d{1,5}[a-z]?)\b", re.I)
_STREET_ADDRESS = re.compile(r"\b\d{1,7}[a-z]?\s+[a-z0-9][^.!?;\n]{0,90}?\s(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct|way|place|pl|terrace|ter|parkway|pkwy)\b", re.I)


def unstructured_status(opportunity: dict, text: str) -> dict:
    """Require one identifiable property before interpreting visible status.

This intentionally leaves listing collections and ambiguous unit pages unknown.
Source quotes on a broker's contact page cannot validate a property listing.
"""
    def words(value):
        return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()
    def unit_code(value):
        value = re.sub(r"^(?:unit|suite|apt\.?|apartment|flat|space)\s*#?\s*", "", (value or "").strip(), flags=re.I)
        return value.removeprefix("#").strip().casefold()
    result = {"identity_matched": False, "listing_status": "unverified", "status_basis": "Exact property identity was not established"}
    address = words(opportunity.get("address"))
    visible = words(text)
    if len(address) < 8:
        return result
    match = re.search(r"(?<![a-z0-9])" + re.escape(address) + r"(?![a-z0-9])", visible)
    if not match:
        return result
    target_unit = unit_code(opportunity.get("unit"))
    mentioned_units = {unit_code(first or second) for first, second in _UNIT_MARKER.findall(text)}
    if mentioned_units and (not target_unit or mentioned_units != {target_unit}):
        result["status_basis"] = "Page contains an unspecified or different unit; availability needs review"
        return result
    if target_unit:
        adjacent = visible[match.end():match.end() + 90]
        if not re.search(r"(?<![a-z0-9])" + re.escape(words(target_unit)) + r"(?![a-z0-9])", adjacent):
            result["status_basis"] = "The specified unit was not found beside the address"
            return result
    addresses = {words(item) for item in _STREET_ADDRESS.findall(text)}
    collection_phrases = ("similar properties", "other available units", "available units", "available floor plans", "view all listings", "properties available", "units available", "select a unit")
    if len(addresses) > 1 or any(phrase in visible for phrase in collection_phrases):
        result["status_basis"] = "Listing collection or multiple properties; a specific offer is needed"
        return result
    result["identity_matched"] = True
    local = visible[max(0, match.start() - 100):match.end() + 400]
    buying = opportunity.get("mode", "").endswith("buy")
    opposite = ("for rent", "for lease", "now leasing") if buying else ("for sale",)
    if any(phrase in local for phrase in opposite):
        result["status_basis"] = "Visible availability describes a different transaction type"
        return result
    closed = ("no longer available", "not available", "not currently available", "this property is sold", "this property is leased", "listing has expired", "listing is withdrawn")
    active = (("available for sale",) if buying else ("available for rent", "available for lease", "now leasing")) + ("available now", "currently available", "active listing")
    if any(phrase in local for phrase in closed):
        result.update(listing_status="unavailable", status_basis="Visible status beside the same property says unavailable")
    elif any(phrase in local for phrase in active):
        result.update(listing_status="available", status_basis="Explicit availability beside the same property and unit")
    else:
        result["status_basis"] = "Exact property found, but current availability was not established"
    return result

"""One property service for the local GUI, CLI, and durable workers."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from . import files
from .config import AppError, data_root
from .models import (
    Opportunity,
    OpportunitySeed,
    SearchInput,
    SuppressionInput,
    canonical_url,
    domain_of,
    now,
    route_for,
)
from .quality import ready_reasons, sequence_issues
from .ranking import assess, date_age, default_diligence, identity_of, norm, rank_key
from .research import email_syntax
from .storage import Store

UNCONTACTED = {"new", "shortlisted"}
CONTACT_ACTIVITY = {"contacted", "replied", "tour_scheduled", "toured", "applied", "offered", "negotiating", "closed", "bounced"}
PROPERTY_ACTIVITY = CONTACT_ACTIVITY | {"rejected", "not_interested"}


def ident() -> str:
    return uuid.uuid4().hex[:16]


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def merge_dict(old: dict, patch: dict) -> dict:
    return {**old, **{k: merge_dict(old[k], v) if isinstance(v, dict) and isinstance(old.get(k), dict) else v for k, v in patch.items()}}


class Service:
    def __init__(self, root: Path | None = None):
        self.store = Store(root or data_root())

    def create_search(self, raw: dict) -> dict:
        search = SearchInput.model_validate(raw).model_dump()
        if search["settings"]["provider"] == "demo":
            search["demo"] = True
        elif search["demo"]:
            raise AppError("DEMO-PROVIDER", "Demo searches use the demo provider.")
        search.update(id=ident(), created_at=now(), updated_at=now(), schema_version=1, profile={}, profile_fingerprint="")
        self.store.save("searches", search)
        return search

    def assert_idle(self, search_id: str):
        if any(run["status"] in {"running", "queued"} for run in self.store.list("runs", search_id)):
            raise AppError("RUN-ACTIVE", "Pause the current run before editing this search.", 409)

    def client_text(self, search_id: str) -> str:
        """Original material only: generated profiles are not evidence for claims."""
        search = self.store.get("searches", search_id)
        client = search["client"]
        facts = {key: value for key, value in client.items() if key != "brief_text" and value not in ("", [], None)}
        parts = ["Client-supplied facts:\n" + json.dumps(facts, ensure_ascii=False), search["objective"], client["brief_text"], json.dumps(search["preferences"], ensure_ascii=False)]
        parts += [doc["text"] for doc in self.store.documents(search_id, include_text=True)]
        return "\n\n".join(part for part in parts if part)

    def _check_document_budget(self, search: dict, docs: list[dict]):
        if len(docs) > 4 or len(search["client"]["brief_text"]) + sum(len(d["text"]) for d in docs) > 60000:
            raise AppError("BRIEF-SIZE", "Use up to four documents and 60,000 characters across pasted text and documents.")

    def update_search(self, search_id: str, patch: dict) -> dict:
        self.assert_idle(search_id)
        old = self.store.get("searches", search_id)
        fields = set(SearchInput.model_fields)
        if set(patch) - fields:
            raise AppError("FIELD-UNKNOWN", "This search update includes an unknown field.")
        parsed = SearchInput.model_validate(merge_dict({key: old[key] for key in fields}, patch)).model_dump()
        if parsed["demo"] != old["demo"] or (parsed["settings"]["provider"] == "demo") != old["demo"]:
            raise AppError("DEMO-ISOLATION", "Create a separate search to switch between demo and real data.")
        if parsed['mode'] != old['mode'] and self.store.list('opportunities', search_id):
            raise AppError('MODE-LOCKED', 'Create a new search to change transaction type after properties have been added.')
        self._check_document_budget(parsed, self.store.documents(search_id, include_text=True))
        client_changed = parsed["client"] != old["client"] or parsed["objective"] != old["objective"]
        content_changed = client_changed or parsed["preferences"] != old["preferences"] or parsed["mode"] != old["mode"]
        cost_changed = parsed["cost_assumptions"] != old["cost_assumptions"]
        research_changed = route_for(parsed["settings"], "research") != route_for(old["settings"], "research") or parsed["settings"]["research_pages"] != old["settings"]["research_pages"]
        outreach_changed = route_for(parsed["settings"], "outreach") != route_for(old["settings"], "outreach")
        profile_changed = route_for(parsed["settings"], "profile") != route_for(old["settings"], "profile")
        search = old | parsed | {"updated_at": now()}
        if content_changed or profile_changed:
            search.update(profile={}, profile_fingerprint="")
        self.store.save("searches", search)
        if content_changed or research_changed:
            self.invalidate_research(search_id)
        elif outreach_changed or cost_changed:
            self.invalidate_drafts(search_id)
        self.refresh_scores(search_id)
        return self.store.get("searches", search_id)

    def invalidate_drafts(self, search_id: str):
        for opportunity in self.store.list("opportunities", search_id):
            opportunity.update(draft_fingerprint="", drafts=[], qa_issues=[], updated_at=now())
            if opportunity["status"] not in {"rejected", "suppressed"}:
                opportunity["status"] = "researched" if opportunity["research_status"] == "complete" else "new"
            self.store.save("opportunities", opportunity)

    def invalidate_research(self, search_id: str):
        for opportunity in self.store.list("opportunities", search_id):
            opportunity.update(research_status="pending", research_fingerprint="", draft_fingerprint="", drafts=[], qa_issues=[], match_components=[], requirement_checks=[], updated_at=now())
            if opportunity["status"] not in {"rejected", "suppressed"}:
                opportunity["status"] = "new"
            self.store.save("opportunities", opportunity)

    def refresh_scores(self, search_id: str):
        search, original = self.store.get("searches", search_id), self.client_text(search_id)
        for opportunity in self.store.list("opportunities", search_id):
            opportunity.update(assess(search, opportunity, original))
            self.store.save("opportunities", opportunity)

    def is_suppressed(self, opportunity: dict, entries: list[dict] | None = None) -> bool:
        """A domain exclusion blocks this source; an email exclusion only blocks contact."""
        if opportunity.get("status") == "suppressed":
            return True
        domain = opportunity.get("domain", "").casefold()
        return any(entry["kind"] == "domain" and domain and (domain == entry["value"] or domain.endswith("." + entry["value"])) for entry in (self.store.suppressions() if entries is None else entries))

    def is_email_suppressed(self, opportunity: dict, entries: list[dict] | None = None) -> bool:
        entries = self.store.suppressions() if entries is None else entries
        return self.is_suppressed(opportunity, entries) or any(entry["kind"] == "email" and opportunity.get("email", "").casefold() == entry["value"] for entry in entries)

    def opportunity_context(self, search_id: str) -> tuple:
        identities, emails, domains = {}, {}, {}
        for other in self.store.list("opportunities"):
            if other["search_id"] == search_id or other["workflow_status"] in UNCONTACTED:
                continue
            status = other["workflow_status"]
            if status in PROPERTY_ACTIVITY:
                identities.setdefault(other["identity"], status)
            if status in CONTACT_ACTIVITY:
                if other["email"]:
                    emails.setdefault(other["email"].casefold(), status)
                if other["domain"]:
                    domains.setdefault(other["domain"], status)
        return self.store.suppressions(), identities, emails, domains

    def decorate(self, search: dict, opportunity: dict, context: tuple | None = None) -> dict:
        opportunity = opportunity.copy()
        entries, identities, emails, domains = context or self.opportunity_context(search['id'])
        prior_contact = emails.get(opportunity['email'].casefold(), '')
        prior_property = identities.get(opportunity['identity'], '')
        outreach = ready_reasons(search | {'client_evidence': self.client_text(search['id'])}, opportunity, self.is_email_suppressed(opportunity, entries))
        if prior_contact:
            outreach.append('Prior contact with this email in another search: ' + prior_contact)
        if prior_property:
            outreach.append('Prior activity on this property: ' + prior_property)
        if opportunity['research_status'] != 'complete':
            outreach.append('Complete current research first')
        if opportunity.get('source_check') != 'matched':
            outreach.append('Check the exact property source')
        if opportunity['listing_status'] != 'available':
            outreach.append('Availability not confirmed')
        age = date_age(opportunity.get('last_checked_at', ''))
        if age is None or not -0.01 <= age <= 7:
            outreach.append('Refresh source checks (within seven days)')
        tour = []
        if search['demo']:
            tour.append('Fictional demo data')
        if opportunity['listing_status'] != 'available' or opportunity.get('source_check') != 'matched':
            tour.append('Confirm current property listing')
        if not opportunity.get('listing_url'):
            tour.append('Listing link needed')
        if age is None or not -0.01 <= age <= 7:
            tour.append('Refresh source checks (within seven days)')
        if opportunity['eligibility'] == 'blocked':
            tour.append('Conflicts with a required condition')
        if self.is_suppressed(opportunity, entries) or opportunity['status'] == 'rejected':
            tour.append('Excluded from this search')
        if opportunity['workflow_status'] in {'closed', 'rejected', 'not_interested', 'bounced'}:
            tour.append('Closed activity')
        opportunity.update(export_blockers=list(dict.fromkeys(outreach)), tour_blockers=tour, ready_to_contact=not outreach, ready_to_tour=not tour, prior_activity=prior_contact or prior_property)
        return opportunity

    def search_detail(self, search_id: str) -> dict:
        search = self.store.get('searches', search_id)
        context, original = self.opportunity_context(search_id), self.client_text(search_id)
        opportunities = []
        for row in self.store.list('opportunities', search_id):
            row.update(assess(search, row, original))
            opportunities.append(self.decorate(search, row, context))
        opportunities.sort(key=rank_key)
        for rank, row in enumerate(opportunities, 1):
            row['rank'] = rank
        counts = {'total': len(opportunities)}
        for key, check in {
            'available': lambda x: x['listing_status'] == 'available',
            'researched': lambda x: x['research_status'] == 'complete',
            'drafted': lambda x: bool(x['drafts']),
            'approved': lambda x: x['status'] == 'approved',
            'ready_contact': lambda x: x['ready_to_contact'],
            'ready_tour': lambda x: x['ready_to_tour'],
            'shortlisted': lambda x: x['workflow_status'] == 'shortlisted',
            'tours': lambda x: x['workflow_status'] in {'tour_scheduled', 'toured'},
        }.items():
            counts[key] = sum(check(row) for row in opportunities)
        outcomes = {}
        for row in opportunities:
            state = row['workflow_status']
            outcomes[state] = outcomes.get(state, 0) + 1
        return {'search': search, 'properties': opportunities, 'opportunities': opportunities, 'runs': sorted(self.store.list('runs', search_id), key=lambda x: x['created_at'], reverse=True), 'counts': counts, 'documents': self.store.documents(search_id), 'outcomes': outcomes}

    def upsert_seed(self, search_id: str, seed: OpportunitySeed) -> tuple[dict, bool]:
        self.store.get("searches", search_id)
        payload = seed.model_dump()
        try:
            identity = identity_of(payload)
        except ValueError as exc:
            raise AppError("OPPORTUNITY-URL", str(exc)) from exc
        domain = domain_of(payload["broker_url"] or payload["listing_url"])
        if payload["email"]:
            payload["email"] = payload["email"].casefold()
            if not email_syntax(payload["email"]):
                payload.update(email="", email_source="")
        aliases = {canonical_url(payload["listing_url"])} if payload["listing_url"] else set()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = [json.loads(row[0]) for row in db.execute("SELECT data FROM opportunities WHERE search_id=?", (search_id,))]
            opportunity = next((row for row in existing if row["identity"] == identity or (row["mode"] == payload["mode"] and row.get("unit", "").casefold() == payload.get("unit", "").casefold() and (not row["address"] or not payload["address"] or norm(row["address"]) == norm(payload["address"])) and aliases.intersection([canonical_url(row["listing_url"])]))), None)
            if opportunity:
                # Filling an unknown fact must not preserve approval for an older dossier.
                material_changed = False
                for key, value in payload.items():
                    if value not in ("", [], None, "unknown", "unverified") and opportunity.get(key) in ("", [], None, "unknown", "unverified"):
                        opportunity[key] = value
                        material_changed = material_changed or key not in {"source_urls", "signals", "source_provider"}
                if material_changed:
                    opportunity.update(research_status="pending", research_fingerprint="", draft_fingerprint="", drafts=[], qa_issues=[], source_check="unverified", last_checked_at="")
                    if opportunity["status"] not in {"rejected", "suppressed"}:
                        opportunity["status"] = "new"
                opportunity["source_urls"] = list(dict.fromkeys(opportunity["source_urls"] + payload["source_urls"]))[:25]
                opportunity["domain"] = domain_of(opportunity["broker_url"] or opportunity["listing_url"])
                opportunity["updated_at"] = now()
                created = False
            else:
                opportunity = Opportunity(**payload, id=ident(), search_id=search_id, identity=identity, domain=domain, created_at=now(), updated_at=now(), due_diligence=default_diligence(payload["mode"]), email_status="published" if payload["email"] and payload["email_source"] else "unknown" if payload["email"] else "missing").model_dump()
                created = True
            self.store.save_in(db, "opportunities", opportunity)
        if self.is_suppressed(opportunity):
            opportunity.update(status="suppressed", email_status="suppressed")
        elif self.is_email_suppressed(opportunity):
            opportunity["email_status"] = "suppressed"
        self.store.save("opportunities", opportunity)
        return opportunity, created

    @staticmethod
    def check_date(stamp: str, *, future: bool = True):
        try:
            checked = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=UTC)
            if not future and (checked - datetime.now(UTC)).total_seconds() > 300:
                raise ValueError("Future date")
        except (ValueError, TypeError) as exc:
            raise AppError("DATE-INVALID", "Use an ISO date such as 2026-10-23 or a timestamp with timezone.") from exc

    @staticmethod
    def check_verification_date(stamp: str):
        Service.check_date(stamp, future=False)

    def update_opportunity(self, opportunity_id: str, patch: dict) -> dict:
        old = self.store.get("opportunities", opportunity_id)
        self.assert_idle(old["search_id"])
        allowed = {"status", "drafts", "review_notes", "email", "contact_name", "contact_role", "email_status", "verification_source", "verification_at", "email_source", "contact_source", "workflow_status", "activity_at", "next_follow_up", "due_diligence", "costs", "price_amount", "price_basis", "currency", "area_sqft", "bedrooms", "bathrooms", "lease_type", "phone"}
        if set(patch) - allowed:
            raise AppError("FIELD-UNKNOWN", "This opportunity update includes an unknown field.")
        patch = dict(patch)
        if "costs" in patch:
            if not isinstance(patch["costs"], dict):
                raise AppError("INPUT-INVALID", "Cost updates must be an object.", 422)
            patch["costs"] = merge_dict(old["costs"], patch["costs"])
        changed = any(old.get(key) != value for key, value in patch.items() if key not in {"status", "review_notes", "workflow_status", "activity_at", "next_follow_up"})
        if changed and old["status"] == "approved" and patch.get("status") != "approved":
            old["status"] = "drafted" if old["drafts"] else "researched"
        if "email" in patch:
            patch["email"] = str(patch["email"]).strip().casefold()
            if patch["email"] and not email_syntax(patch["email"]):
                raise AppError("EMAIL-INVALID", "Enter a valid public professional email.")
            if patch["email"] != old["email"]:
                old.update(email_status="unknown", verification_source="", verification_at="", email_source="", email_checked_at="")
        for key in ("activity_at", "next_follow_up"):
            if patch.get(key):
                self.check_date(patch[key], future=key == "next_follow_up")
        if "workflow_status" in patch and patch["workflow_status"] != old["workflow_status"]:
            patch.setdefault("activity_at", now())
        factual = {'costs', 'price_amount', 'price_basis', 'currency', 'area_sqft', 'bedrooms', 'bathrooms', 'lease_type'}
        if any(key in patch and old.get(key) != (merge_dict(old[key], patch[key]) if key == 'costs' else patch[key]) for key in factual):
            if 'costs' in patch:
                patch['costs'] = merge_dict(old['costs'], patch['costs'])
            old.update(drafts=[], draft_fingerprint='', qa_issues=[])
            if patch.get('status') == 'approved':
                raise AppError('DRAFT-STALE', 'Regenerate inquiries after changing property facts.')
            if old['status'] not in {'rejected', 'suppressed'}:
                old['status'] = 'researched' if old['research_status'] == 'complete' else 'new'
        opportunity = Opportunity.model_validate(old | patch | {"updated_at": now()}).model_dump()
        opportunity["qa_issues"] = sequence_issues(opportunity, self.client_text(old["search_id"])) if opportunity["drafts"] else []
        if opportunity["email_status"] == "verified":
            if not opportunity["verification_source"] or not opportunity["verification_at"]:
                raise AppError("EMAIL-EVIDENCE", "Record the mailbox verifier and actual check date.")
            self.check_verification_date(opportunity["verification_at"])
        if self.is_email_suppressed(opportunity):
            opportunity["email_status"] = "suppressed"
        if opportunity["status"] == "approved":
            if self.is_suppressed(opportunity):
                raise AppError("OPPORTUNITY-SUPPRESSED", "This opportunity is excluded.")
            if opportunity["qa_issues"] or not opportunity["drafts"] or opportunity["research_status"] != "complete":
                raise AppError("DRAFT-QA", "Complete current research and resolve sequence checks before approval.")
        opportunity.update(assess(self.store.get("searches", old["search_id"]), opportunity, self.client_text(old["search_id"])))
        self.store.save("opportunities", opportunity)
        if opportunity["workflow_status"] == "bounced" and opportunity["email"]:
            self.suppress({"kind": "email", "value": opportunity["email"], "reason": opportunity["workflow_status"].replace("_", " ")})
            opportunity = self.store.get("opportunities", opportunity["id"])
        return self.decorate(self.store.get("searches", old["search_id"]), opportunity)

    def suppress(self, raw: dict) -> dict:
        entry = SuppressionInput.model_validate(raw).model_dump()
        value = entry["value"].casefold().strip()
        if entry["kind"] == "email":
            if not email_syntax(value):
                raise AppError("EMAIL-INVALID", "Use a complete email address.")
        else:
            value = domain_of(value)
            if not value or "." not in value:
                raise AppError("DOMAIN-INVALID", "Use a broker domain such as example.com.")
        entry.update(value=value, created_at=now())
        with self.store.connect() as db:
            db.execute("INSERT INTO suppressions VALUES (?,?,?,?) ON CONFLICT(kind,value) DO UPDATE SET reason=excluded.reason", (entry["kind"], value, entry["reason"], entry["created_at"]))
        entries = self.store.suppressions()
        for opportunity in self.store.list("opportunities"):
            if self.is_suppressed(opportunity, entries):
                opportunity.update(status="suppressed", email_status="suppressed", updated_at=now())
                self.store.save("opportunities", opportunity)
            elif self.is_email_suppressed(opportunity, entries):
                opportunity.update(email_status="suppressed", updated_at=now())
                self.store.save("opportunities", opportunity)
        return entry

    def upload_document(self, search_id: str, name: str, raw: bytes) -> dict:
        self.assert_idle(search_id)
        search = self.store.get("searches", search_id)
        docs = self.store.documents(search_id, include_text=True)
        digest = hashlib.sha256(raw).hexdigest()
        match = next((doc for doc in docs if doc["sha256"] == digest), None)
        if match:
            return {"id": match["id"], "name": match["name"], "characters": len(match["text"]), "duplicate": True}
        text = files.document_text(name, raw)
        self._check_document_budget(search, docs + [{"text": text}])
        doc_id, safe_name = ident(), Path(name.replace("\\", "/")).name[:200]
        with self.store.connect() as db:
            db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?)", (doc_id, search_id, safe_name, text, digest, now()))
        search.update(profile={}, profile_fingerprint="", updated_at=now())
        self.store.save("searches", search)
        self.invalidate_research(search_id)
        self.refresh_scores(search_id)
        return {"id": doc_id, "name": safe_name, "characters": len(text)}

    def delete_document(self, search_id: str, doc_id: str):
        self.assert_idle(search_id)
        search = self.store.get("searches", search_id)
        with self.store.connect() as db:
            changed = db.execute("DELETE FROM documents WHERE id=? AND search_id=?", (doc_id, search_id)).rowcount
        if not changed:
            raise AppError("NOT-FOUND", "Document not found.", 404)
        search.update(profile={}, profile_fingerprint="", updated_at=now())
        self.store.save("searches", search)
        self.invalidate_research(search_id)
        self.refresh_scores(search_id)

    def import_opportunities(self, search_id: str, name: str, raw: bytes) -> dict:
        self.assert_idle(search_id)
        self.store.get("searches", search_id)
        rows = files.read_rows(name, raw)
        aliases = {'property_title': 'title', 'property_url': 'listing_url', 'url': 'listing_url', 'website': 'listing_url', 'street_address': 'address', 'city': 'location', 'asking_price': 'price_amount', 'rent': 'price_amount', 'sqft': 'area_sqft', 'email_address': 'email', 'agent_name': 'contact_name', 'broker_name': 'contact_name'}
        count, duplicates, errors = 0, 0, []
        for index, row in enumerate(rows, 2):
            values = {aliases.get(k, k): value for k, value in row.items() if value}
            fields = {k: value for k, value in values.items() if k in OpportunitySeed.model_fields}
            for key in ("source_urls", "features", "signals", "costs"):
                value = fields.get(key)
                if value:
                    try:
                        fields[key] = json.loads(value) if value.startswith(("[", "{")) else [part.strip() for part in value.split(";") if part.strip()]
                    except ValueError:
                        fields[key] = []
            # Imported claims are useful discovery inputs, never live-check authority.
            fields.update(source_provider="import", listing_source="import", listing_status="unverified", last_checked_at="")
            fields.setdefault("mode", self.store.get("searches", search_id)["mode"])
            try:
                _, created = self.upsert_seed(search_id, OpportunitySeed.model_validate(fields))
                count += int(created)
                duplicates += int(not created)
            except (ValueError, ValidationError, AppError):
                if len(errors) < 50:
                    errors.append({"row": index, "message": "Check title, mode, listing URL or address, price basis, and field formats."})
        self.refresh_scores(search_id)
        return {"imported": count, "duplicates": duplicates, "rejected": len(rows) - count - duplicates, "errors": errors}

    def opportunities_by_email(self, search_id: str) -> dict[str, list[dict]]:
        grouped = {}
        for opportunity in self.store.list("opportunities", search_id):
            if opportunity["email"]:
                grouped.setdefault(opportunity["email"].casefold(), []).append(opportunity)
        return grouped

    def import_verification(self, search_id: str, name: str, raw: bytes, source: str = "") -> dict:
        self.assert_idle(search_id)
        self.store.get("searches", search_id)
        grouped = self.opportunities_by_email(search_id)
        mapped = {"valid": "verified", "deliverable": "verified", "verified": "verified", "invalid": "invalid", "undeliverable": "invalid", "catch_all": "catch_all", "accept_all": "catch_all", "risky": "risky", "unknown": "unknown"}
        updated, skipped = 0, 0
        for row in files.read_rows(name, raw):
            matches = grouped.get((row.get("email") or row.get("email_address") or "").casefold(), [])
            status = mapped.get(files.header(row.get("status") or row.get("email_status") or row.get("result")))
            provider, stamp = row.get("verification_source") or row.get("provider") or source, row.get("verification_at") or row.get("checked_at")
            if not matches or not status or not provider or not stamp:
                skipped += 1
                continue
            try:
                self.check_verification_date(stamp)
            except AppError:
                skipped += 1
                continue
            for opportunity in matches:
                if self.is_email_suppressed(opportunity):
                    skipped += 1
                    continue
                opportunity.update(email_status=status, verification_source=provider[:300], verification_at=stamp[:100], updated_at=now())
                self.store.save("opportunities", opportunity)
                updated += 1
            if status == "invalid":
                self.suppress({"kind": "email", "value": matches[0]["email"], "reason": "Verifier reported invalid"})
        return {"updated": updated, "skipped": skipped}

    def import_outcomes(self, search_id: str, name: str, raw: bytes) -> dict:
        self.assert_idle(search_id)
        self.store.get("searches", search_id)
        opportunities = self.store.list("opportunities", search_id)
        allowed = set(Opportunity.model_fields["workflow_status"].annotation.__args__)
        updated, skipped = 0, 0
        for row in files.read_rows(name, raw):
            status = files.header(row.get("workflow_status") or row.get("outcome") or row.get("status"))
            if status not in allowed:
                skipped += 1
                continue
            key = row.get("property_id") or row.get("opportunity_id") or row.get("id")
            url = row.get("listing_url") or row.get("property_url")
            email = (row.get("email") or "").casefold()
            try:
                if key:
                    matches = [x for x in opportunities if x["id"] == key]
                elif url:
                    target = canonical_url(url)
                    matches = [x for x in opportunities if target in {canonical_url(x[k]) for k in ("listing_url",) if x[k]}]
                else:
                    matches = [x for x in opportunities if email and x["email"].casefold() == email]
                    if len(matches) > 1 and status not in {"contacted", "replied", "bounced", "not_interested"}:
                        matches = []
                if not matches:
                    skipped += 1
                    continue
                patch = {"workflow_status": status, "activity_at": row.get("activity_at") or row.get("at") or now()}
                if row.get("next_follow_up"):
                    patch["next_follow_up"] = row["next_follow_up"]
                for match in matches:
                    self.update_opportunity(match["id"], patch)
                    updated += 1
            except (AppError, ValueError, ValidationError):
                skipped += 1
        return {"updated": updated, "skipped": skipped}

    def import_suppressions(self, name: str, raw: bytes) -> dict:
        count, skipped = 0, 0
        for row in files.read_rows(name, raw):
            kind = row.get("kind") or ("email" if row.get("email") else "domain")
            try:
                self.suppress({"kind": kind, "value": row.get("value") or row.get("email") or row.get("domain") or "", "reason": row.get("reason") or "Imported exclusion"})
                count += 1
            except (AppError, ValidationError):
                skipped += 1
        return {"imported": count, "skipped": skipped}

    def export(self, search_id: str, format: str = "csv", scope: str = "all") -> bytes:
        detail = self.search_detail(search_id)
        opportunities = detail["opportunities"]
        if scope == "tour":
            opportunities = [row for row in opportunities if row["ready_to_tour"]]
        elif scope == "outreach":
            unique = {}
            for row in opportunities:
                if row["ready_to_contact"]:
                    unique.setdefault(row["email"].casefold(), row)
            opportunities = list(unique.values())
        elif scope == "shortlist":
            opportunities = [row for row in opportunities if row["workflow_status"] == "shortlisted"]
        elif scope != "all":
            raise AppError("EXPORT-SCOPE", "Choose all, tour, outreach, or shortlist.")
        if scope != "all" and not opportunities:
            raise AppError("EXPORT-EMPTY", "No eligible opportunities yet. Review the checks or export all research.")
        return files.spreadsheet(detail["search"], opportunities, format)

    def create_demo(self, mode: str = 'residential_rent') -> dict:
        from .demo import search_input
        return self.create_search(search_input(mode))

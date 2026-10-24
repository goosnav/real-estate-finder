from __future__ import annotations

import re
from datetime import UTC, datetime

from .research import email_syntax


def words(text: str) -> list[str]:
    return re.findall(r"\b[\w]+(?:['’-][\w]+)*\b", text)


def sequence_issues(lead: dict, client_text: str | None = None) -> list[str]:
    drafts = lead.get("drafts", [])
    issues = []
    if len(drafts) != 3 or sorted(d.get("step") for d in drafts) != [1, 2, 3]:
        return ["A complete sequence needs emails 1, 2, and 3."]
    evidence_urls = set(lead.get("source_urls", [])) | {e["url"] for e in lead.get("evidence", [])}
    from .ranking import user_claims_supported
    claims = [claim for draft in drafts for claim in draft.get("user_claims", [])]
    allowed_claims = set(user_claims_supported(claims, client_text)) if client_text is not None else set(claims)
    bodies = set()
    for d in drafts:
        prefix = f"Email {d['step']}: "
        subject, body = d.get("subject", ""), d.get("body", "")
        if not 1 <= len(words(subject)) <= 5:
            issues.append(prefix + "use a subject of 1–5 words.")
        if re.match(r"\s*(re|fw|fwd)\s*:", subject, re.I):
            issues.append(prefix + "remove the simulated reply/forward prefix.")
        if "\n" in subject or "\r" in subject:
            issues.append(prefix + "subject must be one line.")
        limit = 90 if d["step"] == 1 else 65
        if not body or len(words(body)) > limit:
            issues.append(prefix + f"keep the body within {limit} words.")
        if any(len(words(sentence)) > 25 for sentence in re.split(r"[.!?]\s+", body)):
            issues.append(prefix + "shorten sentences over 25 words.")
        if body.count("?") > 1:
            issues.append(prefix + "ask one question.")
        if re.search(r"\{\{|\[\s*(name|company|insert|first.?name|your|offer)\b|<[^>]+>", body, re.I):
            issues.append(prefix + "replace the placeholder or HTML.")
        if re.search(r"\b(game.changer|revolutioni[sz]e|unlock your|synergy|touching base|just bumping|circle back|guaranteed|10x)\b", body, re.I):
            issues.append(prefix + "remove hype or empty follow-up language.")
        if len(re.findall(r"https?://", body)) > 1:
            issues.append(prefix + "use at most one link.")
        if not d.get("source_urls") or any(url not in evidence_urls for url in d.get("source_urls", [])):
            issues.append(prefix + "attach supporting dossier sources.")
        normalized = re.sub(r"\s+", " ", body).casefold()
        if normalized in bodies:
            issues.append(prefix + "add a distinct follow-up.")
        bodies.add(normalized)
        if any(claim not in allowed_claims for claim in d.get("user_claims", [])):
            issues.append(prefix + "personal claims must quote your original brief.")
    return issues



def client_signature(client: dict) -> str:
    return "\n".join(value for value in (client.get("name", ""), client.get("organization", ""), client.get("email", "")) if value)


def ready_reasons(search: dict, opportunity: dict, suppressed: bool = False) -> list[str]:
    reasons = []
    if search.get("demo"):
        reasons.append("Fictional demo data")
    if suppressed or opportunity["status"] == "suppressed":
        reasons.append("Do not contact")
    if opportunity["status"] != "approved":
        reasons.append("Approve the outreach first")
    if opportunity.get("eligibility") == "blocked":
        reasons.append("Conflicts with a required condition")
    if opportunity.get("fit_score", 0) < search["settings"]["min_fit"]:
        reasons.append("Below your minimum fit")
    if not email_syntax(opportunity.get("email", "")) or not opportunity.get("email_source"):
        reasons.append("Published business email and source needed")
    if opportunity.get("email_status") not in {"published", "mx_found", "verified"}:
        reasons.append("Review the contact email status")
    if opportunity.get("email_status") == "verified":
        try:
            checked = datetime.fromisoformat(opportunity["verification_at"].replace("Z", "+00:00"))
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=UTC)
            if not opportunity.get("verification_source") or not -300 <= (datetime.now(UTC) - checked).total_seconds() <= 30 * 86400:
                reasons.append("Refresh mailbox verification")
        except (ValueError, TypeError, KeyError):
            reasons.append("Verification date needed")
    if not search["client"]["name"] or not email_syntax(search["client"]["email"]):
        reasons.append("Add your name and reply email")
    if opportunity["workflow_status"] not in {"new", "shortlisted"}:
        reasons.append("Prior property or contact activity")
    reasons.extend(sequence_issues(opportunity, search.get("client_evidence")))
    return list(dict.fromkeys(reasons))

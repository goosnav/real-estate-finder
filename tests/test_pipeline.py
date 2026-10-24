from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from real_estate_finder import pipeline
from real_estate_finder.config import AppError
from real_estate_finder.models import Model, OpportunitySeed, ResearchResult, now
from real_estate_finder.pipeline import Context, Jobs, Stopped
from real_estate_finder.providers import AgentResult, ProviderError
from real_estate_finder.service import Service


class Payload(Model):
    ok: bool


@pytest.fixture
def make_service(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "inspect_providers", lambda: [{"id": name, "available": True, "message": ""} for name in ("claude", "codex")])
    def inspect_fixture(url, cancel=None):
        try:
            return {"url": url, "text": pipeline.fetch_public_text(url, cancel=cancel), "listings": [], "listing_status": "unverified", "status_basis": "Page is reachable", "warnings": [], "requests": 1}
        except pipeline.SourceUnavailable as exc:
            removed = exc.status_code in {404, 410}
            return {"url": url, "text": "", "listings": [], "listing_status": "unavailable" if removed else "unverified", "status_basis": f"Listing URL returned HTTP {exc.status_code}; removal reason is unknown" if removed else "", "warnings": [], "requests": 1}
    monkeypatch.setattr(pipeline.sources, "inspect_listing", inspect_fixture)
    count = [0]
    def create(*, settings=None, opportunities=2, brief="I need a two bedroom apartment in Denver with a monthly budget of 3000 USD."):
        count[0] += 1
        service = Service(tmp_path / str(count[0]))
        search = service.create_search({"name": "Regression search", "mode": "residential_rent", "objective": "Find two bedroom apartments.", "client": {"brief_text": brief}, "preferences": {"locations": ["Denver"], "property_types": ["apartment"]}, "settings": {"provider": "claude", "concurrency": 1, "max_cost_usd": 20, "min_fit": 0} | (settings or {})})
        for index in range(opportunities):
            website = f"https://property{index}.example.com"
            service.upsert_seed(search["id"], OpportunitySeed(mode="residential_rent", title=f"Property {index}", address=f"{100 + index} Example Street", location="Denver", listing_url=website + "/apartments/unit1", inquiry_url=website + "/contact", property_type="apartment", price_amount=2000, currency="USD", price_basis="month", bedrooms=2, email=f"team@property{index}.example.com", email_source=website + "/contact", source_urls=[website + "/apartments/unit1"]))
        return service, search
    return create


@pytest.fixture
def install_runner(monkeypatch):
    def install(callback):
        calls, lock = [], threading.Lock()
        class Runner:
            def __init__(self, provider, **kwargs):
                self.provider, self.options = provider, kwargs
            def run(self, prompt, schema, *, web, cancel):
                with lock:
                    record = {"index": len(calls), "provider": self.provider, "prompt": prompt, "schema": schema["title"], "web": web, "options": self.options}
                    calls.append(record)
                return callback(record, cancel)
        monkeypatch.setattr(pipeline, "AgentRunner", Runner)
        return calls
    return install


def queued_context(service, search):
    manager = Jobs(service)
    manager.launch = lambda job_id: None
    job = manager.create(search["id"], "research")
    return manager, Context(service, job["id"])


def research_payload(opportunity):
    seed = {key: opportunity[key] for key in OpportunitySeed.model_fields}
    source = opportunity["listing_url"]
    return ResearchResult(**(seed | {"listing_status": "available", "last_checked_at": now(), "evidence": [{"url": source, "quote": opportunity["address"] + " available now"}], "match_components": [{"criterion": "property", "match": "strong", "reason": "Matching apartment", "source_url": source}], "requirement_checks": [], "risks": [], "due_diligence": [], "comparables": [], "research_summary": "A two bedroom apartment.", "outreach_angle": "Ask about total rent and a tour."})).model_dump()


def research_data(service, record):
    opportunity = next(row for row in service.store.list("opportunities") if row["listing_url"] in record["prompt"])
    return AgentResult(research_payload(opportunity), input_tokens=100, output_tokens=50, cost_usd=0.1)


def test_concurrent_requests_never_exceed_call_limit(make_service, install_runner):
    service, search = make_service(settings={"max_agent_calls": 3, "concurrency": 4})
    _, ctx = queued_context(service, search)

    def complete(record, cancel):
        time.sleep(0.03)
        assert not cancel.is_set()
        return AgentResult({"ok": True}, cost_usd=0.1)

    calls = install_runner(complete)

    def request(_):
        try:
            return ctx.call("bounded", Payload)
        except Stopped:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(request, range(20)))
    job = service.store.get("runs", ctx.job_id)
    assert len(calls) == job["agent_calls"] == 3
    assert sum(result is not None for result in results) == 3
    assert job["active_calls"] == job["reserved_cost"] == 0
    assert job["cost_usd"] == pytest.approx(0.3)
    assert ctx.end_status == "budget_exhausted"


def test_cost_reservation_waits_without_cancelling_inflight_work(
    make_service, install_runner
):
    service, search = make_service(settings={"max_cost_usd": 1})
    _, ctx = queued_context(service, search)
    started, release = threading.Event(), threading.Event()

    def complete(record, cancel):
        if record["index"] == 0:
            started.set()
            assert release.wait(2)
        assert not cancel.is_set()
        return AgentResult({"ok": True}, cost_usd=0.2)

    calls = install_runner(complete)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ctx.call, "first", Payload)
        assert started.wait(1)
        second = pool.submit(ctx.call, "second", Payload)
        time.sleep(0.12)
        assert len(calls) == 1
        assert not ctx.stop.is_set()
        release.set()
        assert first.result(timeout=2) == second.result(timeout=2) == {"ok": True}
    assert [call["options"]["max_cost_usd"] for call in calls] == pytest.approx(
        [1, 0.8]
    )
    job = service.store.get("runs", ctx.job_id)
    assert job["cost_usd"] == pytest.approx(0.4)
    assert job["cost_unreported"] == job["reserved_cost"] == 0


def test_missing_cost_consumes_conservative_allowance(make_service, install_runner):
    service, search = make_service(settings={"max_cost_usd": 1})
    _, ctx = queued_context(service, search)
    calls = install_runner(lambda record, cancel: AgentResult({"ok": True}))
    assert ctx.call("first", Payload) == {"ok": True}
    with pytest.raises(Stopped):
        ctx.call("second", Payload)
    job = service.store.get("runs", ctx.job_id)
    assert len(calls) == 1
    assert job["cost_usd"] is None
    assert job["cost_unreported"] == 1
    assert job["cost_unreported_calls"] == 1
    assert job["active_calls"] == job["reserved_cost"] == 0


@pytest.mark.parametrize(
    "code", ["network_error", "invalid_response", "missing_result"]
)
def test_retryable_provider_codes_count_each_attempt_and_unknown_cost(
    make_service, install_runner, code
):
    service, search = make_service()
    _, ctx = queued_context(service, search)

    def complete(record, cancel):
        if record["index"] == 0:
            raise ProviderError(code, "Retry once.")
        return AgentResult({"ok": True}, cost_usd=0.2)

    calls = install_runner(complete)
    assert ctx.call("retry", Payload) == {"ok": True}
    job = service.store.get("runs", ctx.job_id)
    assert len(calls) == job["agent_calls"] == 2
    assert job["cost_usd"] == pytest.approx(0.2)
    assert job["cost_unreported"] == 2


@pytest.mark.parametrize(
    "code",
    [
        "auth_required",
        "not_installed",
        "unsupported_cli",
        "native_executable_required",
        "rate_limited",
        "launch_failed",
    ],
)
def test_fatal_provider_error_stops_search_before_other_opportunities(
    make_service, install_runner, code
):
    service, search = make_service(opportunities=8)

    def fail(record, cancel):
        raise ProviderError(code, "Provider needs setup.")

    calls = install_runner(fail)
    job = Jobs(service).create(search["id"], "research", background=False)
    assert job["status"] == "failed"
    assert "Provider needs setup" in job["error"]
    assert len(calls) == job["agent_calls"] == 1
    assert job["active_calls"] == job["reserved_cost"] == 0


def test_failed_property_marks_job_failed_and_resume_reuses_success(
    make_service, install_runner
):
    service, search = make_service()
    should_fail = [True]

    def complete(record, cancel):
        if "Property 1" in record["prompt"] and should_fail[0]:
            raise ProviderError("provider_failed", "Property request failed.")
        return research_data(service, record)

    calls = install_runner(complete)
    manager = Jobs(service)
    first = manager.create(search["id"], "research", background=False)
    assert first["status"] == "failed"
    assert first["run_error_count"] == 1
    assert first["stage_index"] == first["stage_count"] == 1
    assert len(calls) == 2
    should_fail[0] = False
    resumed = manager.control(first["id"], "resume", background=False)
    assert resumed["status"] == "completed"
    assert resumed["run_error_count"] == 0
    assert resumed["error_count"] == 1
    assert len(calls) == resumed["agent_calls"] == 3
    assert all(
        opportunity["research_status"] == "complete"
        for opportunity in service.store.list("opportunities")
    )


def test_pause_after_provider_result_saves_it_without_paid_duplicate(
    make_service, install_runner
):
    service, search = make_service()
    manager = Jobs(service)

    def complete(record, cancel):
        result = research_data(service, record)
        if record["index"] == 0:
            next(iter(manager.contexts.values())).halt("paused")
        return result

    calls = install_runner(complete)
    paused = manager.create(search["id"], "research", background=False)
    assert paused["status"] == "paused"
    assert len(calls) == 1
    assert (
        sum(
            opportunity["research_status"] == "complete"
            for opportunity in service.store.list("opportunities")
        )
        == 1
    )
    finished = manager.control(paused["id"], "resume", background=False)
    assert finished["status"] == "completed"
    assert len(calls) == finished["agent_calls"] == 2


def test_cancel_interrupts_active_call_and_retains_unreported_cost(
    make_service, install_runner
):
    service, search = make_service(settings={"max_cost_usd": 1})
    started = threading.Event()

    def wait_for_cancel(record, cancel):
        started.set()
        assert cancel.wait(2)
        raise ProviderError("cancelled", "Stopped.")

    calls = install_runner(wait_for_cancel)
    manager = Jobs(service)
    job = manager.create(search["id"], "research")
    assert started.wait(1)
    manager.control(job["id"], "cancel")
    manager.threads[job["id"]].join(timeout=2)
    assert not manager.threads[job["id"]].is_alive()
    final = service.store.get("runs", job["id"])
    assert final["status"] == "cancelled"
    assert final["cost_unreported"] == 1
    assert final["cost_unreported_calls"] == len(calls) == 1
    assert final["reserved_cost"] == final["active_calls"] == 0
    with pytest.raises(AppError):
        manager.control(job["id"], "resume")


def test_two_managers_cannot_resume_same_job_twice(make_service, monkeypatch):
    service, search = make_service()
    _, ctx = queued_context(service, search)
    service.store.mutate_job(
        ctx.job_id, lambda job: job.update(status="paused", worker_pid=None)
    )
    managers = [Jobs(Service(service.store.root)), Jobs(Service(service.store.root))]
    barrier, launches = threading.Barrier(2), []
    for manager in managers:
        monkeypatch.setattr(
            manager.service, "assert_idle", lambda search_id: barrier.wait(timeout=2)
        )
        monkeypatch.setattr(manager, "launch", lambda job_id: launches.append(job_id))

    def resume(manager):
        try:
            return manager.control(ctx.job_id, "resume")["status"]
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(resume, managers))
    assert sorted(states) == ["JOB-STATE", "queued"]
    assert launches == [ctx.job_id]


def test_dead_worker_recovery_preserves_reserved_allowance(make_service, monkeypatch):
    service, search = make_service()
    manager, ctx = queued_context(service, search)
    service.store.mutate_job(
        ctx.job_id,
        lambda job: job.update(
            status="running",
            worker_pid=99999999,
            active_calls=1,
            active_claude_calls=1,
            reserved_cost=1.5,
            cost_unreported=0.2,
            cost_unreported_calls=1,
        ),
    )

    def dead(pid, signal):
        raise ProcessLookupError()

    monkeypatch.setattr(pipeline.os, "kill", dead)
    manager.recover_interrupted()
    job = service.store.get("runs", ctx.job_id)
    assert job["status"] == "paused"
    assert job["cost_unreported"] == pytest.approx(1.7)
    assert job["cost_unreported_calls"] == 2
    assert job["active_calls"] == job["reserved_cost"] == 0


def test_resume_during_previous_thread_cleanup_does_not_orphan_job(
    make_service, install_runner
):
    service, search = make_service(opportunities=1)
    _, ctx = queued_context(service, search)
    service.store.mutate_job(
        ctx.job_id, lambda job: job.update(status="paused", worker_pid=None)
    )
    release = threading.Event()
    previous = threading.Thread(target=lambda: release.wait(2), daemon=True)
    previous.start()
    manager = Jobs(service)
    manager.threads[ctx.job_id] = previous
    calls = install_runner(lambda record, cancel: research_data(service, record))
    queued = manager.control(ctx.job_id, "resume")
    assert queued["status"] == "queued"
    assert calls == []
    release.set()
    manager.threads[ctx.job_id].join(timeout=2)
    assert service.store.get("runs", ctx.job_id)["status"] == "completed"
    assert len(calls) == 1


def test_two_managers_cannot_claim_one_queued_job_twice(make_service, install_runner):
    service, search = make_service(opportunities=1)
    _, ctx = queued_context(service, search)
    managers = [Jobs(Service(service.store.root)), Jobs(Service(service.store.root))]
    calls = install_runner(lambda record, cancel: research_data(service, record))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda manager: manager.run(ctx.job_id), managers))
    assert len(calls) == 1
    assert service.store.get("runs", ctx.job_id)["status"] == "completed"


def test_input_loading_failure_does_not_leave_permanently_running_job(
    make_service, monkeypatch
):
    service, search = make_service()

    def fail_context(*args):
        raise ValueError("Private failing input details")

    monkeypatch.setattr(pipeline, "Context", fail_context)
    job = Jobs(service).create(search["id"], "research", background=False)
    assert job["status"] == "failed"
    assert job["agent_calls"] == 0
    assert job["worker_pid"] is None
    assert "Private failing input" not in job["error"]


def profile_payload():
    return {"summary": "Two bedroom apartment near Denver.", "search_terms": ["Denver apartment"], "priorities": ["Two bedrooms"], "unknowns": [], "source_plan": ["Owner pages"]}


def sequence_payload(opportunity):
    return {"drafts": [{"step": step, "delay_days": days, "subject": "Apartment enquiry", "body": body, "source_urls": [opportunity["listing_url"]], "user_claims": []} for step, days, body in [(1, 0, "I saw your apartment listing. Is it available for a tour?"), (2, 5, "Could you confirm the total monthly rent and any required fees?"), (3, 12, "I will close the loop here. Who handles viewing requests for this property?")]]}


def test_profile_reads_document_end_once_and_is_cached(make_service, install_runner):
    service, search = make_service(opportunities=0)
    tail = "CONSTRAINT: I cannot relocate outside Colorado."
    service.upload_document(
        search["id"],
        "brief.txt",
        ("Property preferences. " * 500 + tail).encode(),
    )

    def complete(record, cancel):
        assert tail in record["prompt"]
        assert record["schema"] == "ProfileBrief" and record["web"] is False
        return AgentResult(profile_payload(), cost_usd=0.1)

    calls = install_runner(complete)
    manager = Jobs(service)
    first = manager.create(search["id"], "profile", background=False)
    second = manager.create(search["id"], "profile", background=False)
    assert first["status"] == second["status"] == "completed"
    assert first["agent_calls"] == 1 and second["agent_calls"] == 0
    assert service.store.get("searches", search["id"])["profile_fingerprint"]
    service.update_search(
        search["id"],
        {
            "settings": {
                "routes": {"profile": {"provider": "codex", "model": "profile-model"}}
            }
        },
    )
    third = manager.create(search["id"], "profile", background=False)
    assert third["status"] == "completed" and third["agent_calls"] == 1
    assert (
        calls[-1]["provider"] == "codex"
        and calls[-1]["options"]["model"] == "profile-model"
    )


@pytest.mark.parametrize(
    "change",
    ["objective", "preferences", "documents", "brief", "research_route", "pages"],
)
def test_standalone_draft_blocks_stale_research_and_pipeline_refreshes(
    make_service, install_runner, monkeypatch, change
):
    service, search = make_service(opportunities=1, settings={"target_results": 1})
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "mx_found")
    monkeypatch.setattr(
        pipeline,
        "fetch_public_text",
        lambda *args, **kwargs: (
            "100 Example Street available now team@property0.example.com"
        ),
    )

    def complete(record, cancel):
        if record["schema"] == "ProfileBrief":
            return AgentResult(profile_payload(), cost_usd=0.1)
        if record["schema"] == "SequenceResult":
            return AgentResult(
                sequence_payload(service.store.list("opportunities")[0]), cost_usd=0.1
            )
        return research_data(service, record)

    calls = install_runner(complete)
    manager = Jobs(service)
    assert (
        manager.create(search["id"], "research", background=False)["status"]
        == "completed"
    )
    if change == "documents":
        service.upload_document(
            search["id"], "scope.txt", b"I prefer an elevator."
        )
    else:
        patches = {
            "objective": {"objective": "Find apartments near parks."},
            "preferences": {"preferences": {"locations": ["Colorado"]}},
            "brief": {
                "client": {
                    "brief_text": "I need a two bedroom apartment in Denver with a monthly budget of 3000 USD. I also need parking."
                }
            },
            "research_route": {
                "settings": {
                    "routes": {
                        "research": {"provider": "codex", "model": "new-research-model"}
                    }
                }
            },
            "pages": {"settings": {"research_pages": 7}},
        }
        service.update_search(search["id"], patches[change])
    stale = manager.create(search["id"], "draft", background=False)
    assert stale["status"] == "failed" and stale["agent_calls"] == 0
    assert len(calls) == 1
    assert any(
        "RESEARCH-STALE" in event["message"]
        for event in service.store.events(stale["id"])
    )
    refreshed = manager.create(search["id"], "pipeline", background=False)
    assert refreshed["status"] == "completed"
    assert refreshed["agent_calls"] == 3
    assert [call["schema"] for call in calls].count("ResearchResult") == 2
    assert len(service.store.list("opportunities")[0]["drafts"]) == 3


def test_mixed_routes_separate_claude_allowance_from_codex_reported_cost(
    make_service, install_runner
):
    service, search = make_service(
        settings={
            "provider": "codex",
            "model": "default-model",
            "max_cost_usd": 1,
            "routes": {
                "profile": {"provider": "claude", "model": "small-profile"},
                "outreach": {"provider": "claude", "model": "writer"},
            },
        }
    )
    _, ctx = queued_context(service, search)
    calls = install_runner(
        lambda record, cancel: AgentResult(
            {"ok": True}, cost_usd=99 if record["provider"] == "codex" else 0.2
        )
    )
    ctx.call("research", Payload, task="research")
    ctx.call("profile", Payload, task="profile")
    ctx.call("outreach", Payload, task="outreach")
    saved = service.store.get("runs", ctx.job_id)
    assert [call["provider"] for call in calls] == ["codex", "claude", "claude"]
    assert [call["options"]["model"] for call in calls] == [
        "default-model",
        "small-profile",
        "writer",
    ]
    assert [call["options"]["max_cost_usd"] for call in calls[1:]] == pytest.approx(
        [1, 0.8]
    )
    assert saved["cost_usd"] == pytest.approx(99.4) and saved[
        "claude_cost_usd"
    ] == pytest.approx(0.4)
    assert [row["task"] for row in saved["usage"]] == [
        "research",
        "profile",
        "outreach",
    ]


def test_unpriced_codex_does_not_consume_claude_allowance(make_service, install_runner):
    service, search = make_service(
        settings={
            "provider": "codex",
            "max_cost_usd": 1,
            "routes": {"outreach": {"provider": "claude"}},
        }
    )
    _, ctx = queued_context(service, search)
    calls = install_runner(lambda record, cancel: AgentResult({"ok": True}))
    ctx.call("research", Payload)
    ctx.call("research again", Payload)
    ctx.call("outreach", Payload, task="outreach")
    saved = service.store.get("runs", ctx.job_id)
    assert len(calls) == 3 and saved["cost_unreported"] == 1
    assert saved["cost_unreported_calls"] == 1 and saved["unpriced_calls"] == 3


def test_preflight_only_requires_providers_for_selected_stages(
    make_service, monkeypatch, install_runner
):
    service, search = make_service(
        settings={"provider": "codex", "routes": {"outreach": {"provider": "claude"}}}
    )
    monkeypatch.setattr(
        pipeline,
        "inspect_providers",
        lambda: [
            {"id": "codex", "available": True},
            {"id": "claude", "available": False, "message": "Install Claude."},
        ],
    )
    install_runner(lambda record, cancel: research_data(service, record))
    manager = Jobs(service)
    assert (
        manager.create(search["id"], "research", background=False)["status"]
        == "completed"
    )
    with pytest.raises(AppError, match="Install Claude"):
        manager.create(search["id"], "pipeline", background=False)



def test_agent_discovery_cannot_claim_checked_provenance(make_service, install_runner):
    service, search = make_service(opportunities=0, settings={"target_results": 1, "discovery_queries": 1})
    seed = OpportunitySeed(mode="residential_rent", title="Apartment", listing_url="https://rent.example/101", source_urls=["https://rent.example/101"], source_provider="structured", listing_status="available", last_checked_at=now()).model_dump()
    install_runner(lambda record, cancel: AgentResult({"opportunities": [seed], "coverage_notes": "Found one."}, cost_usd=0.1))
    job = Jobs(service).create(search["id"], "discover", background=False)
    row = service.store.list("opportunities")[0]
    assert job["status"] == "completed"
    assert row["source_provider"] == "web"
    assert row["listing_status"] == "unverified" and row["last_checked_at"] == ""


def test_research_cannot_change_address_or_unit(make_service, install_runner):
    service, search = make_service(opportunities=1)
    def replace_property(record, cancel):
        data = research_payload(service.store.list("opportunities")[0])
        data["address"] = "999 Other Street"
        return AgentResult(data, cost_usd=0.1)
    install_runner(replace_property)
    job = Jobs(service).create(search["id"], "research", background=False)
    assert job["status"] == "failed"
    assert service.store.list("opportunities")[0]["address"] == "100 Example Street"


def test_web_research_cannot_certify_zoning(make_service, install_runner):
    service, search = make_service(opportunities=1)
    def certify(record, cancel):
        data = research_payload(service.store.list("opportunities")[0])
        data["due_diligence"] = [{"topic": "Zoning", "status": "confirmed", "question": "Is the use permitted?", "source_url": "https://planning.example/zoning"}]
        return AgentResult(data, cost_usd=0.1)
    install_runner(certify)
    job = Jobs(service).create(search["id"], "research", background=False)
    row = service.store.list("opportunities")[0]
    assert job["status"] == "completed"
    assert row["due_diligence"][0]["status"] == "source_found"
    assert row["listing_status"] == "unverified"
    assert row["evidence"][0]["checked_at"] == ""


def test_http200_alone_cannot_confirm_available_property(make_service, monkeypatch):
    service, search = make_service(opportunities=1)
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "mx_found")
    monkeypatch.setattr(pipeline, "fetch_public_text", lambda *a, **k: "Property details 100 Example Street Contact us")
    job = Jobs(service).create(search["id"], "verify", background=False)
    row = service.store.list("opportunities")[0]
    assert job["status"] == "completed"
    assert row["listing_status"] == "unverified"
    assert row["source_check"] == "matched"  # Identity alone is not availability.
    assert row["email_status"] == "unknown"


def test_availability_requires_matching_property_and_exact_email(make_service, monkeypatch):
    service, search = make_service(opportunities=1)
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "mx_found")
    monkeypatch.setattr(pipeline, "fetch_public_text", lambda *a, **k: "100 Example Street available now team@property0.example.com")
    job = Jobs(service).create(search["id"], "verify", background=False)
    row = service.store.list("opportunities")[0]
    assert job["status"] == "completed"
    assert row["listing_status"] == "available" and row["source_check"] == "matched"
    assert row["email_status"] == "mx_found"


@pytest.mark.parametrize(("status", "expected"), [(404, "unavailable"), (410, "unavailable"), (403, "unverified")])
def test_removed_listing_and_access_denied_have_different_status(make_service, monkeypatch, status, expected):
    service, search = make_service(opportunities=1)
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "unknown")
    def unavailable(*a, **k):
        raise pipeline.SourceUnavailable("Source unavailable", status_code=status)
    monkeypatch.setattr(pipeline, "fetch_public_text", unavailable)
    Jobs(service).create(search["id"], "verify", background=False)
    assert service.store.list("opportunities")[0]["listing_status"] == expected


def test_conflicting_structured_listing_does_not_borrow_other_property_status(make_service, monkeypatch):
    service, search = make_service(opportunities=1)
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "unknown")
    monkeypatch.setattr(pipeline, "fetch_public_text", lambda *a, **k: "100 Example Street available now")
    row = service.store.list("opportunities")[0]
    monkeypatch.setattr(pipeline.sources, "inspect_listing", lambda *a, **k: {"text": "100 Example Street available now", "listings": [{"listing_url": row["listing_url"], "listing_url_explicit": True, "address": "999 Other Street", "listing_status": "available", "price_amount": 1}], "warnings": []})
    Jobs(service).create(search["id"], "verify", background=False)
    row = service.store.list("opportunities")[0]
    assert row["listing_status"] == "unverified" and row["price_amount"] == 2000


def test_supplied_pages_respect_mode_and_no_duplicate_web_call(make_service, monkeypatch, install_runner):
    service, search = make_service(opportunities=0, settings={"target_results": 1, "seed_urls": ["https://listing.example/101"]})
    monkeypatch.setattr(pipeline.sources, "inspect_listing", lambda url, **k: {"listings": [{"title": "A home", "listing_url": url, "listing_url_explicit": True, "transaction": "rent", "price_amount": 2300, "currency": "USD", "price_basis": "month"}], "requests": 1, "warnings": []})
    calls = install_runner(lambda *a: pytest.fail("Direct page already met target"))
    job = Jobs(service).create(search["id"], "discover", background=False)
    assert job["status"] == "completed" and calls == []
    row = service.store.list("opportunities")[0]
    assert row["source_provider"] == "structured" and row["price_basis"] == "month"


def test_comparables_require_exact_checked_price_and_identity(make_service, monkeypatch):
    service, search = make_service(opportunities=1, settings={"research_pages": 3})
    row = service.store.list("opportunities")[0]
    comp_url = "https://comp.example/unit1"
    row["comparables"] = [{"title": "Comparable", "url": comp_url, "mode": "residential_rent", "property_type": "apartment", "location": "Denver", "price_amount": 2200, "currency": "USD", "price_basis": "month", "area_sqft": 1000, "bedrooms": 2, "observed_at": now(), "basis": "asking", "lease_type": "unknown", "rationale": "Same type"}]
    service.store.save("opportunities", row)
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "unknown")
    monkeypatch.setattr(pipeline, "fetch_public_text", lambda *a, **k: "Contact")
    comp = {"title": "Comparable", "listing_url": comp_url, "listing_url_explicit": True, "listing_status": "available", "transaction": "rent", "property_type": "apartment", "location": "Denver", "price_amount": 2200, "currency": "USD", "price_basis": "month", "area_sqft": 1000, "bedrooms": 2}
    monkeypatch.setattr(pipeline.sources, "inspect_listing", lambda url, **k: {"text": "Denver", "listings": [comp] if url == comp_url else [], "warnings": []})
    Jobs(service).create(search["id"], "verify", background=False)
    assert service.store.list("opportunities")[0]["verified_comparable_urls"] == [comp_url]
    comp["price_amount"] = 9999
    Jobs(service).create(search["id"], "verify", background=False)
    assert service.store.list("opportunities")[0]["verified_comparable_urls"] == []


@pytest.mark.parametrize("mode", ["residential_rent", "residential_buy", "commercial_lease", "commercial_buy"])
def test_all_demo_modes_complete_offline_with_saved_results(tmp_path, monkeypatch, mode):
    def offline(*a, **k):
        pytest.fail("A fictional demo must not use network or an installed CLI")
    monkeypatch.setattr(pipeline, "fetch_public_text", offline)
    monkeypatch.setattr(pipeline.sources, "inspect_listing", offline)
    monkeypatch.setattr(pipeline, "email_dns_status", offline)
    monkeypatch.setattr(pipeline, "inspect_providers", offline)
    service = Service(tmp_path / mode)
    search = service.create_demo(mode)
    job = Jobs(service).create(search["id"], "pipeline", background=False)
    assert job["status"] == "completed", service.store.events(job["id"])
    detail = service.search_detail(search["id"])
    assert detail["counts"]["total"] == 5
    assert len([row for row in detail["opportunities"] if row["listing_status"] == "unavailable"]) == 1
    assert any(len(row["drafts"]) == 3 for row in detail["opportunities"])
    assert all(not row["ready_to_contact"] for row in detail["opportunities"])
    assert job["agent_calls"] <= 12


def test_incidental_contact_quote_cannot_mark_property_source_matched(make_service, monkeypatch):
    service, search = make_service(opportunities=1)
    row = service.store.list("opportunities")[0]
    row["evidence"] = [{"url": row["email_source"], "title": "Contact", "quote": "Contact our team", "field": "email", "checked_at": ""}]
    service.store.save("opportunities", row)
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "mx_found")
    monkeypatch.setattr(pipeline, "fetch_public_text", lambda url, **k: "Contact our team team@property0.example.com" if url.endswith("contact") else "View all our properties")
    Jobs(service).create(search["id"], "verify", background=False)
    row = service.store.list("opportunities")[0]
    assert row["evidence"][0]["checked_at"]
    assert row["source_check"] == "reachable" and row["listing_status"] == "unverified"


def test_sale_text_does_not_confirm_rental_availability(make_service, monkeypatch):
    service, search = make_service(opportunities=1)
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "unknown")
    monkeypatch.setattr(pipeline, "fetch_public_text", lambda *a, **k: "100 Example Street available for sale")
    Jobs(service).create(search["id"], "verify", background=False)
    row = service.store.list("opportunities")[0]
    assert row["listing_status"] == "unverified"
    assert row["source_check"] == "matched"


def test_comparable_exact_address_unit_are_taken_from_checked_source(make_service, monkeypatch):
    service, search = make_service(opportunities=1, settings={"research_pages": 3})
    row = service.store.list("opportunities")[0]
    comp_url = "https://comp.example/unit2a"
    row["comparables"] = [{"title": "Comparable", "url": comp_url, "mode": "residential_rent", "address": "", "unit": "", "property_type": "apartment", "location": "Denver", "price_amount": 2200, "currency": "USD", "price_basis": "month", "area_sqft": 1000, "bedrooms": 2, "observed_at": "2000-01-01", "basis": "asking", "lease_type": "unknown", "rationale": "Same type"}]
    service.store.save("opportunities", row)
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "unknown")
    monkeypatch.setattr(pipeline, "fetch_public_text", lambda *a, **k: "Contact")
    comp = {"title": "Actual property title", "listing_url": comp_url, "listing_url_explicit": True, "listing_status": "available", "transaction": "rent", "address": "200 Actual Avenue, Denver, CO", "unit": "2A", "property_type": "apartment", "location": "Denver, CO", "price_amount": 2200, "currency": "USD", "price_basis": "month", "area_sqft": 1000, "bedrooms": 2}
    monkeypatch.setattr(pipeline.sources, "inspect_listing", lambda url, **k: {"text": "Property", "listings": [comp] if url == comp_url else [], "warnings": []})
    Jobs(service).create(search["id"], "verify", background=False)
    row = service.store.list("opportunities")[0]
    assert row["verified_comparable_urls"] == [comp_url]
    assert row["comparables"][0]["address"] == "200 Actual Avenue, Denver, CO"
    assert row["comparables"][0]["unit"] == "2A"
    assert row["comparables"][0]["title"] == "Actual property title"
    assert row["comparables"][0]["observed_at"] != "2000-01-01"


@pytest.mark.parametrize("new_price", [2000, 2400])
def test_standalone_verify_invalidates_approval_only_when_facts_change(make_service, monkeypatch, new_price):
    service, search = make_service(opportunities=1)
    row = service.store.list("opportunities")[0]
    row.update(research_status="complete", listing_status="available", source_check="matched", status="approved", drafts=sequence_payload(row)["drafts"], draft_fingerprint="saved-copy", qa_issues=[], last_checked_at="2000-01-01")
    service.store.save("opportunities", row)
    monkeypatch.setattr(pipeline, "email_dns_status", lambda email: "mx_found")
    monkeypatch.setattr(pipeline, "fetch_public_text", lambda *a, **k: "team@property0.example.com")
    checked = {"title": row["title"], "listing_url": row["listing_url"], "listing_url_explicit": True, "address": row["address"], "unit": row["unit"], "listing_status": "available", "transaction": "rent", "price_amount": new_price, "currency": "USD", "price_basis": "month"}
    monkeypatch.setattr(pipeline.sources, "inspect_listing", lambda *a, **k: {"text": "A listing", "listings": [checked], "warnings": []})
    job = Jobs(service).create(search["id"], "verify", background=False)
    assert job["status"] == "completed"
    saved = service.store.list("opportunities")[0]
    assert saved["last_checked_at"] != "2000-01-01"
    assert saved["price_amount"] == new_price
    if new_price == 2000:
        assert saved["status"] == "approved" and saved["draft_fingerprint"] == "saved-copy"
        assert len(saved["drafts"]) == 3
    else:
        assert saved["status"] == "researched" and saved["draft_fingerprint"] == ""
        assert saved["drafts"] == [] and saved["qa_issues"] == []

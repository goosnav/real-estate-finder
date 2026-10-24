"""Durable bounded real-estate search stages shared by the GUI and CLI.

Each agent task has its own provider/model route. Call and elapsed-time caps apply
to every provider; a separate conservative allowance applies only to Claude calls.
Completed work is saved before the next cancellation checkpoint.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from . import demo, prompts, sources
from .config import AppError
from .models import (
    DiscoveryResult,
    Opportunity,
    OpportunitySeed,
    ProfileBrief,
    ResearchResult,
    RunRequest,
    SequenceResult,
    canonical_url,
    domain_of,
    now,
    route_for,
    strict_schema,
)
from .providers import AgentResult, AgentRunner, ProviderError, inspect_providers
from .quality import sequence_issues
from .ranking import assess, deadline_passed
from .research import (
    SourceUnavailable,
    email_dns_status,
    email_syntax,
    fetch_public_text,
    normalized,
)
from .service import Service, fingerprint, ident

STAGES = ["profile", "discover", "research", "verify", "draft"]
TASKS = ["profile", "discovery", "research", "outreach"]
UNCONTACTED = {"new", "shortlisted"}


def parsed_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        value = value.strip().replace("Z", "+00:00")
        stamp = datetime.fromisoformat(value)
        return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def fresh_check(value: str, hours: int = 24) -> bool:
    checked = parsed_time(value)
    return bool(
        checked
        and datetime.now(UTC) - timedelta(hours=hours)
        <= checked
        <= datetime.now(UTC) + timedelta(minutes=5)
    )


def listing_expired(opportunity: dict) -> bool:
    return opportunity.get("listing_status") == "unavailable" or deadline_passed(
        opportunity.get("valid_through", "")
    )


def _tokens(value: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9+#]+", value.casefold()) if len(t) > 2}


def _material_listing_facts(opportunity: dict) -> dict:
    facts = {key: opportunity.get(key) for key in (
        "mode", "title", "address", "unit", "location", "property_type", "listing_url",
        "inquiry_url", "broker_url", "price_amount", "price_basis", "currency", "area_sqft",
        "bedrooms", "bathrooms", "lease_type", "costs", "posted_at", "available_from",
        "valid_through", "listing_status", "contact_name", "contact_role", "email", "email_source",
    )}
    facts["comparables"] = [{key: value for key, value in comp.items() if key != "observed_at"} for comp in opportunity.get("comparables", [])]
    facts["verified_comparable_urls"] = sorted(set(opportunity.get("verified_comparable_urls", [])))
    return facts


def _invalidate_changed_copy(opportunity: dict, previous: dict):
    if _material_listing_facts(opportunity) != previous:
        opportunity.update(drafts=[], draft_fingerprint="", qa_issues=[])
        if opportunity["status"] not in {"rejected", "suppressed"}:
            opportunity["status"] = "researched" if opportunity["research_status"] == "complete" else "new"


class Stopped(Exception):
    pass


class BudgetBusy(Exception):
    """Capacity is held by work in progress, not yet settled."""


class Context:
    def __init__(self, service: Service, job_id: str):
        self.service, self.store, self.job_id = service, service.store, job_id
        job = self.store.get("runs", job_id)
        self.owner_token = job.get("worker_token")
        self.search = self.store.get("searches", job["search_id"])
        self.documents = self.store.documents(self.search["id"], include_text=True)
        self.document_fingerprint = [(d["sha256"], d["name"]) for d in self.documents]
        self.client_text = service.client_text(self.search["id"])
        self.prompt_search = self.search | {"client_evidence": self.client_text}
        self.settings = self.search["settings"]
        if self.search.get("profile_fingerprint") != self.profile_fingerprint():
            self.prompt_search.pop("profile", None)
        self.stop, self.finished = threading.Event(), threading.Event()
        self.started = time.monotonic()
        self.previous_elapsed = job.get("elapsed_seconds", 0)
        self.end_status, self.end_error, self.failures = "completed", "", 0
        self.lock = threading.Lock()
        self.budget_condition = threading.Condition()
        snapshot = fingerprint(
            {
                "mode": self.search["mode"],
                "cost_assumptions": self.search["cost_assumptions"],
                "objective": self.search["objective"],
                "client": self.search["client"],
                "preferences": self.search["preferences"],
                "documents": self.document_fingerprint,
                "routes": {task: route_for(self.settings, task) for task in TASKS},
                "research_pages": self.settings["research_pages"],
            }
        )
        if job.get("input_fingerprint") and job["input_fingerprint"] != snapshot:
            self.store.event(
                job_id,
                "Inputs changed. Relevant saved work will be refreshed; matching results remain cached.",
            )
        self.mutate(
            lambda j: j.update(
                input_fingerprint=snapshot,
                run_error_count=0,
                cost_limit_usd=self.settings["max_cost_usd"]
                if any(route_for(self.settings, task)["provider"] == "claude" for task in TASKS)
                else None,
            )
        )

    def mutate(self, update):
        def guarded(job):
            if self.owner_token and job.get("worker_token") != self.owner_token:
                raise Stopped()
            update(job)

        return self.store.mutate_job(self.job_id, guarded)

    def halt(self, status: str, error: str = ""):
        with self.lock:
            if not self.stop.is_set():
                self.end_status, self.end_error = status, error
                self.stop.set()
        with self.budget_condition:
            self.budget_condition.notify_all()

    def check(self):
        if (
            self.previous_elapsed + time.monotonic() - self.started
            >= self.settings["max_minutes"] * 60
        ):
            self.halt("budget_exhausted", "Time limit reached. Increase the limit and resume.")
        if self.stop.is_set():
            raise Stopped()

    def monitor(self):
        last_heartbeat = self.started
        while not self.finished.wait(0.15):
            job = self.store.get("runs", self.job_id)
            if self.owner_token and job.get("worker_token") != self.owner_token:
                self.halt("cancelled", "Worker ownership changed.")
                return
            action = job.get("requested_action")
            if action in {"pause", "cancel"}:
                self.halt("paused" if action == "pause" else "cancelled")
            if (
                self.previous_elapsed + time.monotonic() - self.started
                >= self.settings["max_minutes"] * 60
            ):
                self.halt("budget_exhausted", "Time limit reached. Increase the limit and resume.")
            if time.monotonic() - last_heartbeat >= 1:
                elapsed = round(self.previous_elapsed + time.monotonic() - self.started, 2)
                try:
                    self.mutate(
                        lambda j, elapsed=elapsed: j.update(
                            elapsed_seconds=elapsed, heartbeat_at=now()
                        )
                    )
                except Stopped:
                    return
                last_heartbeat = time.monotonic()

    def stage(self, stage: str, total: int):
        self.check()
        self.mutate(
            lambda j: j.update(
                current_stage=stage,
                completed=0,
                total=total,
                current_item="",
                stage_index=STAGES.index(stage) + 1 if j["stage"] == "pipeline" else 1,
            )
        )
        self.store.event(
            self.job_id,
            {
                "profile": "Reading your search brief",
                "discover": "Finding properties and market signals",
                "research": "Researching each opportunity",
                "verify": "Checking listings, sources, and email domains",
                "draft": "Writing short outreach sequences",
            }[stage],
        )

    def progress(self, item: str = "", increment: int = 0):
        self.mutate(
            lambda j: j.update(
                completed=min(j["total"], j["completed"] + increment), current_item=item
            )
        )

    def record_failure(self):
        with self.lock:
            self.failures += 1
        self.mutate(
            lambda j: j.update(
                error_count=j.get("error_count", 0) + 1,
                run_error_count=j.get("run_error_count", 0) + 1,
            )
        )

    def profile_fingerprint(self) -> str:
        return fingerprint(
            [
                "property-brief-v1",
                self.search["mode"],
                self.search["objective"],
                self.search["client"],
                self.search["preferences"],
                self.document_fingerprint,
                route_for(self.settings, "profile"),
            ]
        )

    def research_fingerprint(self, opportunity: dict) -> str:
        return fingerprint(
            [
                "property-research-v1",
                self.search["mode"],
                self.search["objective"],
                self.search["client"],
                self.search["preferences"],
                self.document_fingerprint,
                {
                    k: opportunity.get(k)
                    for k in (
                        "mode",
                        "address",
                        "unit",
                        "title",
                        "listing_url",
                        "inquiry_url",
                        "listing_id",
                    )
                },
                route_for(self.settings, "research"),
                self.settings["research_pages"],
            ]
        )

    def reserve(self, provider: str) -> float:
        reservation = {"cost": 0.0}

        def update(job):
            active = job.get("active_calls", 0)
            if job["agent_calls"] >= self.settings["max_agent_calls"]:
                if active:
                    raise BudgetBusy()
                raise AppError("BUDGET-CALLS", "Agent-call limit reached. Increase it and resume.")
            if provider == "claude":
                left = (
                    self.settings["max_cost_usd"]
                    - job.get("claude_cost_usd", 0)
                    - job.get("cost_unreported", 0)
                    - job.get("reserved_cost", 0)
                )
                if left < 0.01:
                    if active:
                        raise BudgetBusy()
                    raise AppError(
                        "BUDGET-COST",
                        "Claude allowance is exhausted, including calls without reported cost. Review usage and increase the limit to resume.",
                    )
                reservation["cost"] = min(left, 2.0)
                job["reserved_cost"] = job.get("reserved_cost", 0) + reservation["cost"]
                job["active_claude_calls"] = job.get("active_claude_calls", 0) + 1
            job["active_calls"] = active + 1
            job["agent_calls"] += 1

        while True:
            self.check()
            with self.budget_condition:
                try:
                    self.mutate(update)
                    return reservation["cost"]
                except BudgetBusy:
                    self.budget_condition.wait(timeout=0.1)
                except AppError as exc:
                    self.halt("budget_exhausted", exc.message)
                    raise Stopped() from exc

    def settle(
        self,
        provider: str,
        task: str,
        model_name: str,
        reservation: float,
        result: AgentResult | None,
        uncertain: bool,
    ):
        def update(job):
            job["active_calls"] = max(0, job.get("active_calls", 0) - 1)
            if provider == "claude":
                job["active_claude_calls"] = max(0, job.get("active_claude_calls", 0) - 1)
                job["reserved_cost"] = max(0, job.get("reserved_cost", 0) - reservation)
            usage = {
                "task": task,
                "provider": provider,
                "model": model_name,
                "at": now(),
                "input_tokens": None,
                "output_tokens": None,
                "cost_usd": None,
            }
            if result is not None:
                for field in ("input_tokens", "output_tokens", "cost_usd"):
                    value = getattr(result, field)
                    usage[field] = value
                    if value is not None:
                        job[field] = (job.get(field) or 0) + value
                if provider == "claude" and result.cost_usd is not None:
                    job["claude_cost_usd"] = job.get("claude_cost_usd", 0) + result.cost_usd
            if uncertain and (result is None or result.cost_usd is None):
                job["unpriced_calls"] = job.get("unpriced_calls", 0) + 1
                if provider == "claude":
                    job["cost_unreported"] = job.get("cost_unreported", 0) + reservation
                    job["cost_unreported_calls"] = job.get("cost_unreported_calls", 0) + 1
            job.setdefault("usage", []).append(usage)

        with self.budget_condition:
            self.mutate(update)
            self.budget_condition.notify_all()

    def call(
        self,
        prompt: str,
        model,
        *,
        task: str = "research",
        web: bool = True,
        demo_data: dict | None = None,
    ) -> dict:
        self.check()
        route = route_for(self.settings, task)
        provider, model_name = route["provider"], route["model"]
        for attempt in range(2):
            self.check()
            reservation = self.reserve(provider)
            result, uncertain = None, provider != "demo"
            try:
                if provider == "demo":
                    if self.stop.wait(0.12):
                        raise Stopped()
                    result = AgentResult(demo_data or {})
                else:
                    runner = AgentRunner(
                        provider,
                        model=model_name,
                        timeout=min(
                            self.settings["agent_timeout_seconds"],
                            max(
                                1,
                                self.settings["max_minutes"] * 60
                                - self.previous_elapsed
                                - (time.monotonic() - self.started),
                            ),
                        ),
                        max_turns=self.settings["max_turns"],
                        max_cost_usd=reservation or 1.0,
                    )
                    result = runner.run(prompt, strict_schema(model), web=web, cancel=self.stop)
                # Save a finished result even if Pause arrived immediately afterward.
                return model.model_validate(result.data).model_dump()
            except ValidationError as exc:
                if attempt == 0:
                    self.store.event(
                        self.job_id,
                        "Agent result did not match the data contract. Retrying once.",
                        "warning",
                    )
                else:
                    raise AppError(
                        "AGENT-SCHEMA", "Agent returned invalid structured data after one retry."
                    ) from exc
            except ProviderError as exc:
                if exc.code in {
                    "not_installed",
                    "unsupported_cli",
                    "native_executable_required",
                    "launch_failed",
                    "unsupported_provider",
                    "invalid_limits",
                    "input_limit",
                }:
                    uncertain = False
                if self.stop.is_set() or exc.code == "cancelled":
                    if not self.stop.is_set():
                        self.halt("cancelled")
                    raise Stopped() from exc
                if exc.code in {
                    "auth_required",
                    "not_installed",
                    "unsupported_cli",
                    "native_executable_required",
                    "rate_limited",
                    "launch_failed",
                    "unsupported_provider",
                    "invalid_limits",
                    "input_limit",
                }:
                    self.halt("failed", exc.message)
                    raise Stopped() from exc
                if attempt == 0 and exc.code in {
                    "network_error",
                    "invalid_response",
                    "missing_result",
                }:
                    self.store.event(
                        self.job_id,
                        "Agent request failed. Retrying once within the call limit.",
                        "warning",
                    )
                else:
                    raise AppError(
                        "AGENT-" + exc.code.upper().replace("_", "-"), exc.message
                    ) from exc
            finally:
                self.settle(provider, task, model_name, reservation, result, uncertain)
        raise AppError("AGENT-OUTPUT", "No usable agent result.")


class Jobs:
    def __init__(self, service: Service):
        self.service, self.store = service, service.store
        self.threads: dict[str, threading.Thread] = {}
        self.contexts: dict[str, Context] = {}
        self.guard = threading.Lock()

    def recover_interrupted(self):
        for job in self.store.list("runs"):
            if job["status"] not in {"running", "queued"}:
                continue
            pid = job.get("worker_pid")
            alive = False
            if pid:
                try:
                    os.kill(pid, 0)
                    alive = True
                except PermissionError:
                    alive = True
                except (ProcessLookupError, OSError):
                    pass
            if not alive:
                recovered = []

                def recover(current, pid=pid, job=job, recovered=recovered):
                    if (
                        current["status"] not in {"running", "queued"}
                        or current.get("worker_pid") != pid
                        or current.get("worker_token") != job.get("worker_token")
                    ):
                        return
                    # A dead worker cannot report what its last request cost.
                    # Preserve its allowance rather than spending it twice.
                    current["cost_unreported"] = current.get("cost_unreported", 0) + current.get(
                        "reserved_cost", 0
                    )
                    current["cost_unreported_calls"] = current.get(
                        "cost_unreported_calls", 0
                    ) + current.get("active_claude_calls", 0)
                    current.update(
                        status="paused",
                        requested_action="",
                        reserved_cost=0,
                        active_calls=0,
                        active_claude_calls=0,
                        worker_pid=None,
                        worker_token=None,
                        current_item="Interrupted. Resume to continue.",
                        error="Previous worker stopped; completed work is saved. Unreported calls retain their cost allowance.",
                    )
                    recovered.append(True)

                self.store.mutate_job(job["id"], recover)
                if recovered:
                    self.store.event(
                        job["id"],
                        "Interrupted worker recovered. Resume continues from saved results; unreported cost remains reserved in the used allowance.",
                        "warning",
                    )

    def preflight(self, search: dict, stage: str):
        tasks = {
            "pipeline": ["profile", "discovery", "research", "outreach"],
            "profile": ["profile"],
            "discover": ["discovery"],
            "research": ["research"],
            "draft": ["outreach"],
            "verify": [],
        }[stage]
        required = {route_for(search["settings"], task)["provider"] for task in tasks} - {"demo"}
        if not required:
            return
        providers = {p["id"]: p for p in inspect_providers()}
        for name in sorted(required):
            provider = providers.get(name)
            if not provider or not provider.get("available"):
                raise AppError(
                    "PROVIDER-SETUP",
                    (provider or {}).get("message") or f"Set up {name} before this stage.",
                )

    def create(self, search_id: str, stage: str = "pipeline", *, background: bool = True) -> dict:
        RunRequest(stage=stage)
        search = self.store.get("searches", search_id)
        self.service.assert_idle(search_id)
        if stage in {"pipeline", "profile", "discover", "research"} and not (
            search["objective"]
            or search["preferences"]["locations"]
            or search["client"]["brief_text"]
            or search["settings"]["seed_urls"]
            or self.store.documents(search_id)
        ):
            raise AppError(
                "SEARCH-EMPTY", "Write an objective, add a location, or upload a property brief."
            )
        self.preflight(search, stage)
        claude = any(route_for(search["settings"], task)["provider"] == "claude" for task in TASKS)
        job = {
            "id": ident(),
            "search_id": search_id,
            "stage": stage,
            "status": "queued",
            "current_stage": "",
            "current_item": "Waiting",
            "completed": 0,
            "total": 0,
            "stage_index": 0,
            "stage_count": len(STAGES) if stage == "pipeline" else 1,
            "agent_calls": 0,
            "max_agent_calls": search["settings"]["max_agent_calls"],
            "input_tokens": None,
            "output_tokens": None,
            "cost_usd": None,
            "claude_cost_usd": 0.0,
            "cost_unreported": 0.0,
            "cost_unreported_calls": 0,
            "unpriced_calls": 0,
            "usage": [],
            "cost_limit_usd": search["settings"]["max_cost_usd"] if claude else None,
            "reserved_cost": 0,
            "active_calls": 0,
            "active_claude_calls": 0,
            "error": "",
            "error_count": 0,
            "run_error_count": 0,
            "elapsed_seconds": 0,
            "requested_action": "",
            "worker_pid": os.getpid(),
            "worker_token": None,
            "created_at": now(),
            "updated_at": now(),
        }
        try:
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if (
                    db.execute(
                        "SELECT COUNT(*) FROM runs WHERE status IN ('running','queued')"
                    ).fetchone()[0]
                    >= 2
                ):
                    raise AppError(
                        "JOB-LIMIT", "Two searches are already active. Pause one first.", 409
                    )
                self.store.save_in(db, "runs", job)
        except sqlite3.IntegrityError as exc:
            raise AppError("JOB-ACTIVE", "This search already has an active run.", 409) from exc
        if background:
            self.launch(job["id"])
        else:
            self.run(job["id"])
        return self.store.get("runs", job["id"])

    def launch(self, job_id: str):
        with self.guard:
            previous = self.threads.get(job_id)
            if (
                previous
                and previous.is_alive()
                and self.store.get("runs", job_id)["status"] != "queued"
            ):
                raise AppError("JOB-ACTIVE", "This job is already running.", 409)

            def start_after_cleanup():
                # Status becomes resumable just before the old thread exits.
                # A rapid Resume must not orphan the newly queued row or let
                # old cleanup remove the replacement worker's context.
                if previous and previous.is_alive():
                    previous.join()
                self.run(job_id)

            thread = threading.Thread(
                target=start_after_cleanup, daemon=True, name="real-estate-finder-" + job_id
            )
            self.threads[job_id] = thread
            thread.start()

    def control(self, job_id: str, action: str, *, background: bool = True) -> dict:
        job = self.store.get("runs", job_id)
        if action == "resume":
            if job["status"] not in {"paused", "failed", "budget_exhausted"}:
                raise AppError(
                    "JOB-STATE", "Only paused, failed, or budget-limited jobs can resume.", 409
                )
            self.service.assert_idle(job["search_id"])
            search = self.store.get("searches", job["search_id"])
            self.preflight(search, job["stage"])

            def resume(current):
                # Recheck inside the write transaction. Two CLI processes may
                # both have observed the same paused row before either writes.
                if current["status"] not in {"paused", "failed", "budget_exhausted"}:
                    raise AppError("JOB-STATE", "This job has already resumed.", 409)
                if sum(j["status"] in {"running", "queued"} for j in self.store.list("runs")) >= 2:
                    raise AppError(
                        "JOB-LIMIT", "Two searches are already active. Pause one first.", 409
                    )
                current.update(
                    status="queued",
                    requested_action="",
                    error="",
                    max_agent_calls=search["settings"]["max_agent_calls"],
                    worker_pid=os.getpid(),
                    worker_token=None,
                )

            try:
                self.store.mutate_job(job_id, resume)
            except sqlite3.IntegrityError as exc:
                raise AppError("JOB-ACTIVE", "Another job is active for this search.", 409) from exc
            if background:
                self.launch(job_id)
            else:
                self.run(job_id)
        elif action in {"pause", "cancel"}:
            if job["status"] in {"running", "queued"}:

                def request_action(current):
                    if current["status"] in {"running", "queued"}:
                        current.update(
                            requested_action=action,
                            current_item="Pausing…" if action == "pause" else "Stopping…",
                        )

                self.store.mutate_job(job_id, request_action)
                context = self.contexts.get(job_id)
                if context:
                    context.halt("paused" if action == "pause" else "cancelled")
            elif action == "cancel" and job["status"] in {"paused", "failed", "budget_exhausted"}:

                def cancel_saved(current):
                    if current["status"] in {"paused", "failed", "budget_exhausted"}:
                        current.update(status="cancelled", requested_action="")
                    elif current["status"] in {"running", "queued"}:
                        current.update(requested_action="cancel", current_item="Stopping…")

                self.store.mutate_job(job_id, cancel_saved)
            else:
                raise AppError("JOB-STATE", "This job is not running.", 409)
        else:
            raise AppError("JOB-ACTION", "Choose pause, resume, or cancel.")
        return self.store.get("runs", job_id)

    def shutdown(self):
        for job_id, context in list(self.contexts.items()):
            self.store.mutate_job(job_id, lambda j: j.update(requested_action="pause"))
            context.halt("paused")
        for thread in list(self.threads.values()):
            thread.join(timeout=12)

    def run(self, job_id: str):
        # Claim under a transaction so a repeated CLI/UI resume cannot run twice.
        def claim(job):
            if job["status"] != "queued":
                raise AppError("JOB-ACTIVE", "This job is already claimed.", 409)
            job.update(
                status="running", worker_pid=os.getpid(), worker_token=ident(), heartbeat_at=now()
            )

        try:
            claimed = self.store.mutate_job(job_id, claim)
        except AppError as exc:
            if exc.code == "JOB-ACTIVE":
                return  # Another launcher already claimed the same row.
            raise
        try:
            context = Context(self.service, job_id)
        except Exception:

            def fail_initialization(current):
                if current.get("worker_token") == claimed.get("worker_token"):
                    current.update(
                        status="failed",
                        worker_pid=None,
                        worker_token=None,
                        error="Could not load job inputs. Check the saved search and resume.",
                        current_item="Needs attention",
                    )

            self.store.mutate_job(job_id, fail_initialization)
            self.store.event(
                job_id,
                "JOB-INPUTS: Could not load saved inputs. No agent request was started.",
                "error",
            )
            return
        self.contexts[job_id] = context
        monitor = threading.Thread(target=context.monitor, daemon=True)
        monitor.start()
        self.store.event(job_id, "Job started. Results are saved after each opportunity.")
        job = self.store.get("runs", job_id)
        try:
            if job.get("requested_action") in {"pause", "cancel"}:
                context.halt("paused" if job["requested_action"] == "pause" else "cancelled")
            stages = STAGES if job["stage"] == "pipeline" else [job["stage"]]
            for stage in stages:
                context.check()
                if stage == "profile":
                    self.profile(context)
                elif stage == "discover":
                    self.discover(context)
                else:
                    self.opportunities(context, stage)
            context.check()
            if context.failures:
                context.halt(
                    "failed",
                    f"{context.failures} opportunity steps need attention. Resume retries incomplete work and keeps saved results.",
                )
        except Stopped:
            pass
        except AppError as exc:
            context.halt("failed", exc.message)
            self.store.event(job_id, exc.code + ": " + exc.message, "error")
        except Exception:
            context.halt(
                "failed",
                "The job stopped unexpectedly. Saved results are intact; retry this stage.",
            )
            self.store.event(
                job_id, "JOB-ERROR: Unexpected failure. Saved results are intact.", "error"
            )
        except BaseException:
            context.halt("paused", "Worker interrupted. Completed results are saved.")
            raise
        finally:
            context.finished.set()
            monitor.join(timeout=1)
            elapsed = round(context.previous_elapsed + time.monotonic() - context.started, 2)

            def finish(current):
                if current.get("worker_token") != context.owner_token:
                    return
                current["cost_unreported"] = current.get("cost_unreported", 0) + current.get(
                    "reserved_cost", 0
                )
                current["cost_unreported_calls"] = current.get(
                    "cost_unreported_calls", 0
                ) + current.get("active_claude_calls", 0)
                current.update(
                    status=context.end_status,
                    error=context.end_error,
                    elapsed_seconds=elapsed,
                    requested_action="",
                    reserved_cost=0,
                    active_calls=0,
                    active_claude_calls=0,
                    worker_pid=None,
                    worker_token=None,
                    current_item={
                        "completed": "Complete",
                        "paused": "Paused",
                        "cancelled": "Cancelled",
                        "budget_exhausted": "Limit reached",
                        "failed": "Needs attention",
                    }[context.end_status],
                )

            self.store.mutate_job(job_id, finish)
            self.store.event(
                job_id,
                context.end_error
                or (
                    "Completed with saved results."
                    if context.end_status == "completed"
                    else context.end_status.capitalize()
                ),
            )
            self.contexts.pop(job_id, None)

    def profile(self, ctx: Context):
        ctx.stage("profile", 1)
        key = ctx.profile_fingerprint()
        profile = ctx.search.get("profile")
        if not profile or ctx.search.get("profile_fingerprint") != key:
            ctx.progress("Reading your brief and preferences")
            profile = ctx.call(
                prompts.profile(ctx.search, ctx.documents),
                ProfileBrief,
                task="profile",
                web=False,
                demo_data=demo.profile_result(ctx.search) if ctx.search["demo"] else None,
            )
            latest = self.store.get("searches", ctx.search["id"])
            latest.update(profile=profile, profile_fingerprint=key, updated_at=now())
            self.store.save("searches", latest)
            ctx.search.update(profile=profile, profile_fingerprint=key)
        ctx.prompt_search.update(profile=profile, profile_fingerprint=key)
        ctx.progress(increment=1)

    def _retain(self, ctx: Context, rows: list[dict], remaining: int, *, authoritative: bool = False) -> int:
        added = 0
        for raw in rows:
            if added >= remaining:
                break
            row = dict(raw)
            if row.get("mode") != ctx.search["mode"]:
                continue
            if not authoritative:
                row.update(source_provider="web", last_checked_at="", listing_status="unverified")
            if not row.get("source_urls") or not row.get("listing_url"):
                continue
            if not row.get("email_source"):
                row["email"] = ""
            if not row.get("contact_source"):
                row["contact_name"] = row["contact_role"] = row["phone"] = ""
            _, created = self.service.upsert_seed(ctx.search["id"], OpportunitySeed.model_validate(row))
            added += int(created)
        return added

    def discover(self, ctx: Context):
        plan = prompts.queries(ctx.prompt_search)
        seed_urls = [] if ctx.search["demo"] else ctx.settings["seed_urls"]
        ctx.stage("discover", len(seed_urls) + len(plan))
        for url in seed_urls:
            ctx.check()
            remaining = ctx.settings["target_results"] - len(self.store.list("opportunities", ctx.search["id"]))
            if remaining <= 0:
                break
            key = fingerprint(["structured-property-v1", url, ctx.search["mode"]])
            if self.store.done(ctx.job_id, "discover", key):
                ctx.progress(increment=1)
                continue
            ctx.progress("Reading a supplied listing page")
            inspection = sources.inspect_listing(url, cancel=ctx.stop)
            rows = []
            for listing in inspection["listings"]:
                if listing.get("transaction") not in {"unknown", "buy" if ctx.search["mode"].endswith("buy") else "rent"}:
                    continue
                # A multi-property page needs distinct property URLs; never combine units.
                if len(inspection["listings"]) > 1 and not listing.get("listing_url_explicit"):
                    continue
                row = {k: v for k, v in listing.items() if k in OpportunitySeed.model_fields}
                row.update(mode=ctx.search["mode"], source_provider="structured", source_urls=[url, listing["listing_url"]], last_checked_at=now())
                try:
                    rows.append(OpportunitySeed.model_validate(row).model_dump())
                except ValidationError:
                    self.store.event(ctx.job_id, "A structured listing needs manual review; web discovery will continue.", "warning")
            added = self._retain(ctx, rows, remaining, authoritative=True)
            if inspection.get("requests") and not inspection.get("warnings"):
                self.store.checkpoint(ctx.job_id, "discover", key)
            self.store.event(ctx.job_id, f"Added {added} properties from a supplied page.")
            for warning in inspection.get("warnings", []):
                self.store.event(ctx.job_id, str(warning)[:300], "warning")
            ctx.progress(increment=1)
        empty_batches = 0
        for batch_index, query in enumerate(plan, 1):
            ctx.check()
            existing = self.store.list("opportunities", ctx.search["id"])
            remaining = ctx.settings["target_results"] - len(existing)
            if remaining <= 0:
                self.store.event(ctx.job_id, "Discovery target reached.")
                break
            key = fingerprint([ctx.search["mode"], ctx.search["objective"], ctx.search["preferences"], ctx.document_fingerprint, route_for(ctx.settings, "discovery"), query])
            if self.store.done(ctx.job_id, "discover", key):
                ctx.progress(increment=1)
                continue
            ctx.progress("Discovery batch " + str(batch_index))
            known = [item.get("listing_url") or item["identity"] for item in existing]
            result = ctx.call(
                prompts.discovery(ctx.prompt_search, query, known, min(15, remaining)),
                DiscoveryResult, task="discovery",
                demo_data=demo.discovery_result(ctx.search, min(15, remaining), known) if ctx.search["demo"] else None,
            )
            added = self._retain(ctx, result["opportunities"], remaining, authoritative=ctx.search["demo"])
            self.store.checkpoint(ctx.job_id, "discover", key)
            self.store.event(ctx.job_id, f"Added {added} properties. " + result["coverage_notes"][:650])
            ctx.progress(increment=1)
            empty_batches = empty_batches + 1 if added == 0 else 0
            if empty_batches >= 2:
                self.store.event(ctx.job_id, "Two batches found no new properties. Discovery stopped to preserve budget.", "warning")
                break
        count = len(self.store.list("opportunities", ctx.search["id"]))
        if count < ctx.settings["target_results"]:
            self.store.event(ctx.job_id, f"Found {count} of {ctx.settings['target_results']} requested properties. Try a wider search or more discovery queries.", "warning")

    def opportunities(self, ctx: Context, stage: str):
        rows = [
            row
            for row in self.store.list("opportunities", ctx.search["id"])
            if row["status"] not in {"rejected", "suppressed"}
            and not self.service.is_suppressed(row)
        ]
        if stage == "draft":
            if any(
                row["workflow_status"] in UNCONTACTED
                and (
                    (
                        row["research_status"] == "complete"
                        and row["research_fingerprint"] != ctx.research_fingerprint(row)
                    )
                    or (
                        row["research_status"] == "pending"
                        and (row["research_summary"] or row["evidence"])
                    )
                )
                for row in rows
            ):
                raise AppError(
                    "RESEARCH-STALE",
                    "Research inputs changed. Run Research again before Draft, or run the full pipeline.",
                )
            # Recompute deterministic scores so changed weights/elapsed time cannot
            # turn an old score into an unnoticed drafting decision.
            self.service.refresh_scores(ctx.search["id"])
            rows = [self.store.get("opportunities", row["id"]) for row in rows]
            rows = [
                row
                for row in rows
                if row["research_status"] == "complete"
                and row["fit_score"] >= ctx.settings["min_fit"]
                and row["eligibility"] != "blocked"
                and row["workflow_status"] in UNCONTACTED
                and not self.service.is_email_suppressed(row)
                and not listing_expired(row)
                and (ctx.search["demo"] or fresh_check(row["last_checked_at"]))
            ]
        ctx.stage(stage, len(rows))

        def process(row):
            label = row["title"]
            try:
                ctx.check()
                ctx.progress(label)
                getattr(self, stage)(ctx, row)
                ctx.check()
                ctx.progress(label, increment=1)
            except Stopped:
                return
            except (AppError, ValidationError) as exc:
                if stage == "research":
                    latest = self.store.get("opportunities", row["id"])
                    latest.update(research_status="error", updated_at=now())
                    self.store.save("opportunities", latest)
                message = (
                    exc.message
                    if isinstance(exc, AppError)
                    else "Agent result did not match the expected record."
                )
                self.store.event(ctx.job_id, label + ": " + message, "error")
                ctx.record_failure()
                ctx.progress(label, increment=1)

        with ThreadPoolExecutor(max_workers=ctx.settings["concurrency"]) as pool:
            futures = [pool.submit(process, row) for row in rows]
            try:
                for future in as_completed(futures):
                    future.result()
            except BaseException:
                ctx.halt(
                    "failed", "An opportunity step stopped unexpectedly. Completed work is saved."
                )
                for future in futures:
                    future.cancel()
                raise
        ctx.check()

    def research(self, ctx: Context, opportunity: dict):
        key = ctx.research_fingerprint(opportunity)
        if opportunity["research_status"] == "complete" and opportunity["research_fingerprint"] == key and (
            self.store.done(ctx.job_id, "research", opportunity["id"] + key)
            or fresh_check(opportunity["last_checked_at"])
        ):
            return
        result = ctx.call(
            prompts.research(ctx.prompt_search, opportunity), ResearchResult, task="research",
            demo_data=demo.research_result(opportunity) if ctx.search["demo"] else None,
        )
        for field in ("listing_url", "address", "unit", "title", "listing_id"):
            if not result[field]:
                result[field] = opportunity[field]
        if result["mode"] != opportunity["mode"]:
            raise AppError("RESEARCH-IDENTITY", "Research changed the transaction type. Keep separate listings as separate rows.")
        for field in ("listing_id", "address", "unit"):
            before, after = opportunity[field], result[field]
            if before and after and normalized(before) != normalized(after):
                raise AppError("RESEARCH-IDENTITY", "Research resolved to a different property or unit. Review the original listing.")
        if opportunity["listing_url"] and canonical_url(opportunity["listing_url"]) != canonical_url(result["listing_url"]):
            raise AppError("RESEARCH-IDENTITY", "Research changed the listing link. Add another property as a separate row.")
        if not result["email_source"]:
            result["email"] = ""
        if not result["contact_source"]:
            result["contact_name"] = result["contact_role"] = result["phone"] = ""
        result["email"] = result["email"].casefold()
        if result["email"] and not email_syntax(result["email"]):
            result["email"] = result["email_source"] = ""
        # Web research cannot certify inspections, ownership, zoning, or finance.
        for item in result["due_diligence"]:
            if item["status"] == "confirmed":
                item["status"] = "source_found" if item["source_url"] else "unresolved"
            if item["status"] == "source_found" and not item["source_url"]:
                item["status"] = "unresolved"
        previous_email = opportunity["email"]
        result["source_provider"] = opportunity["source_provider"]
        opportunity.update(result)
        opportunity["domain"] = domain_of(opportunity["broker_url"] or opportunity["listing_url"])
        opportunity["source_urls"] = list(dict.fromkeys(result["source_urls"] + [e["url"] for e in result["evidence"]]))[:25]
        for evidence in opportunity["evidence"]:
            evidence["checked_at"] = ""  # Set only by the independent source check.
        opportunity.update(
            research_status="complete", research_fingerprint=ctx.research_fingerprint(opportunity),
            draft_fingerprint="", drafts=[], qa_issues=[], status="researched", updated_at=now(),
            last_checked_at="", source_check="unverified", verified_comparable_urls=[],
            listing_status="unverified",
        )
        opportunity.update(assess(ctx.search, opportunity, ctx.client_text))
        if previous_email != opportunity["email"] or opportunity["email_status"] != "verified":
            opportunity.update(email_status="unknown" if opportunity["email"] else "missing", verification_source="", verification_at="")
        if self.service.is_suppressed(opportunity):
            opportunity.update(status="suppressed", email_status="suppressed")
        elif self.service.is_email_suppressed(opportunity):
            opportunity["email_status"] = "suppressed"
        self.store.save("opportunities", Opportunity.model_validate(opportunity).model_dump())
        self.store.checkpoint(ctx.job_id, "research", opportunity["id"] + opportunity["research_fingerprint"])
        self.store.event(ctx.job_id, f"Researched {opportunity['title']} · fit {opportunity['fit_score']}/100")

    def verify(self, ctx: Context, opportunity: dict):
        previous_facts = _material_listing_facts(opportunity)
        key = fingerprint([opportunity["email"], opportunity["email_source"], opportunity["research_fingerprint"], opportunity["evidence"], opportunity["comparables"], opportunity["listing_url"], now()[:10]])
        if self.store.done(ctx.job_id, "verify", opportunity["id"] + key):
            return
        if ctx.search["demo"]:
            opportunity.update(email_status="published" if opportunity["email"] else "missing", email_checked_at="", last_checked_at=now())
            fixture = next((row for row in demo.properties(ctx.search["mode"]) if row["listing_url"] == opportunity["listing_url"]), None)
            if fixture:
                opportunity.update(listing_status=fixture["listing_status"], source_check="closed" if fixture["listing_status"] == "unavailable" else "matched")
            opportunity["verified_comparable_urls"] = [comp["url"] for comp in opportunity["comparables"] if comp["url"].startswith("https://sample-comps.example/")]
            opportunity.update(assess(ctx.search, opportunity, ctx.client_text))
            # Demo examples remain visibly fictional and cannot enter ready exports.
            _invalidate_changed_copy(opportunity, previous_facts)
            self.store.save("opportunities", opportunity)
            self.store.checkpoint(ctx.job_id, "verify", opportunity["id"] + key)
            return
        ctx.check()
        if self.service.is_email_suppressed(opportunity):
            opportunity["email_status"] = "suppressed"
        elif opportunity["email_status"] != "verified":
            opportunity["email_status"] = email_dns_status(opportunity["email"])
        opportunity["email_checked_at"] = now()
        urls = list(dict.fromkeys(
            [opportunity["listing_url"], opportunity["email_source"]]
            + [comp["url"] for comp in opportunity["comparables"]]
            + [e["url"] for e in opportunity["evidence"]]
            + [opportunity["broker_url"]]
        ))
        checked, inspections = {}, {}
        structured_urls = {opportunity["listing_url"]} | {comp["url"] for comp in opportunity["comparables"]}
        for url in [u for u in urls if u][:ctx.settings["research_pages"]]:
            ctx.check()
            if url in structured_urls:
                inspection = sources.inspect_listing(url, cancel=ctx.stop)
                inspections[url] = inspection
                checked[url] = inspection.get("text") or None
            else:
                try:
                    checked[url] = fetch_public_text(url, cancel=ctx.stop)
                except SourceUnavailable:
                    checked[url] = None
        risks = [risk for risk in opportunity["risks"] if not risk.startswith("Source check:")]
        inspection = inspections.get(opportunity["listing_url"])
        listing = sources.matching_listing(opportunity, inspection.get("listings", [])) if inspection else None
        if inspection:
            if listing:
                for field in ("posted_at", "valid_through", "area_sqft", "bedrooms", "bathrooms", "price_amount", "currency", "price_basis"):
                    if listing.get(field) not in ("", None, "unknown"):
                        opportunity[field] = listing[field]
            elif inspection.get("listings"):
                risks.append("Source check: structured metadata did not identify one matching property; review units and prices manually.")
            risks.extend("Source check: " + str(warning)[:300] for warning in inspection.get("warnings", []))
        if opportunity["email"] and opportunity["email_source"]:
            text = checked.get(opportunity["email_source"])
            if not text or opportunity["email"].casefold() not in normalized(text):
                risks.append("Source check: exact email was not found on the cited accessible page.")
                if opportunity["email_status"] not in {"verified", "suppressed"}:
                    opportunity["email_status"] = "unknown"
            elif opportunity["email_status"] == "unknown":
                opportunity["email_status"] = "published"
        matches = 0
        for evidence in opportunity["evidence"]:
            text = checked.get(evidence["url"])
            matched = bool(text and evidence["quote"] and normalized(evidence["quote"]) in normalized(text))
            evidence["checked_at"] = now() if matched else ""
            matches += int(matched)
        # Source matching means the exact property was independently identified.
        # An incidental quote on an email/contact page must not satisfy it.
        visible = sources.unstructured_status(opportunity, checked.get(opportunity["listing_url"]) or "")
        if inspection and inspection.get("listings"):
            visible = {"identity_matched": False, "listing_status": "unverified", "status_basis": "Structured property identity needs review"}
        opportunity["source_check"] = (
            "matched" if listing or visible["identity_matched"]
            else "reachable" if any(checked.values()) else "unavailable"
        )
        removed = bool(inspection and inspection.get("listing_status") == "unavailable" and re.match(r"Listing URL returned HTTP (404|410)\b", inspection.get("status_basis", "")))
        if removed or (listing and listing["listing_status"] == "unavailable") or deadline_passed(opportunity.get("valid_through", "")):
            opportunity.update(listing_status="unavailable", source_check="closed")
        elif listing and listing["listing_status"] == "available":
            opportunity.update(listing_status="available", source_check="matched")
        elif visible["listing_status"] == "unavailable":
            opportunity.update(listing_status="unavailable", source_check="closed")
        elif visible["listing_status"] == "available":
            opportunity.update(listing_status="available", source_check="matched")
        else:
            opportunity["listing_status"] = "unverified"
            risks.append("Source check: " + visible["status_basis"] + ".")
        verified_comps = []
        for comp in opportunity["comparables"]:
            comp_inspection = inspections.get(comp["url"])
            if not comp_inspection or comp["basis"] != "asking":
                continue
            comp_listing = sources.matching_listing({"mode": comp["mode"], "listing_url": comp["url"], "title": comp["title"], "address": comp.get("address", ""), "unit": comp.get("unit", "")}, comp_inspection.get("listings", []), allow_missing_unit=True)
            if not comp_listing or comp_listing.get("listing_status") != "available":
                continue
            same_price = comp_listing.get("price_amount") == comp["price_amount"] and comp_listing.get("currency") == comp["currency"] and comp_listing.get("price_basis") == comp["price_basis"]
            same_area = comp["area_sqft"] is None or comp_listing.get("area_sqft") is not None and abs(comp_listing["area_sqft"] - comp["area_sqft"]) <= max(1, comp["area_sqft"] * 0.01)
            same_rooms = comp["bedrooms"] is None or comp_listing.get("bedrooms") == comp["bedrooms"]
            same_type = not comp["property_type"] or normalized(comp_listing.get("property_type", "")) == normalized(comp["property_type"])
            same_location = sources.same_locality(comp["location"], comp_listing.get("location", ""))
            lease_visible = comp["lease_type"] == "unknown" or normalized(comp["lease_type"].replace("_", " ")) in normalized(comp_inspection.get("text", ""))
            if same_price and same_area and same_rooms and same_type and same_location and lease_visible:
                verified_comps.append(comp["url"])
                comp["observed_at"] = now()
                comp["address"] = comp_listing.get("address", "")[:500]
                comp["unit"] = comp_listing.get("unit", "")[:150]
                if comp_listing.get("title"):
                    comp["title"] = comp_listing["title"][:300]
        if opportunity["comparables"] and len(verified_comps) < len(opportunity["comparables"]):
            risks.append("Source check: some comparable prices or property details remain unverified; they do not count as deal evidence.")
        if opportunity["evidence"]:
            risks.append(f"Source check: {matches}/{len(opportunity['evidence'])} evidence excerpts independently matched.")
        opportunity.update(verified_comparable_urls=verified_comps, risks=risks[:20], updated_at=now(), last_checked_at=now())
        opportunity.update(assess(ctx.search, opportunity, ctx.client_text))
        if self.service.is_suppressed(opportunity):
            opportunity.update(status="suppressed", email_status="suppressed")
        elif self.service.is_email_suppressed(opportunity):
            opportunity["email_status"] = "suppressed"
        _invalidate_changed_copy(opportunity, previous_facts)
        ctx.check()
        self.store.save("opportunities", opportunity)
        self.store.checkpoint(ctx.job_id, "verify", opportunity["id"] + key)

    def draft(self, ctx: Context, opportunity: dict):
        dossier = {field: opportunity.get(field) for field in (
            "title", "mode", "address", "unit", "listing_url", "contact_name", "contact_role",
            "description", "research_summary", "evidence", "match_components", "risks", "outreach_angle",
            "signals", "requirement_checks", "due_diligence", "estimate", "deal", "listing_status", "source_check",
        )}
        key = fingerprint([ctx.search["client"], ctx.search["mode"], ctx.search["objective"], ctx.search["cost_assumptions"], opportunity["research_fingerprint"], dossier, ctx.document_fingerprint, route_for(ctx.settings, "outreach")])
        if opportunity["draft_fingerprint"] == key and opportunity["drafts"]:
            return
        result = ctx.call(
            prompts.sequence(ctx.prompt_search, opportunity), SequenceResult, task="outreach", web=False,
            demo_data=demo.sequence_result(opportunity) if ctx.search["demo"] else None,
        )
        opportunity.update(drafts=sorted(result["drafts"], key=lambda draft: draft["step"]), draft_fingerprint=key, status="drafted", updated_at=now())
        opportunity["qa_issues"] = sequence_issues(opportunity, client_text=ctx.client_text)
        if self.service.is_suppressed(opportunity):
            opportunity.update(status="suppressed", email_status="suppressed")
        elif self.service.is_email_suppressed(opportunity):
            opportunity["email_status"] = "suppressed"
        self.store.save("opportunities", Opportunity.model_validate(opportunity).model_dump())
        self.store.event(ctx.job_id, f"Drafted {opportunity['title']} · {len(opportunity['qa_issues'])} copy checks")

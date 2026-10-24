# Architecture

Real Estate Finder is a local Python modular monolith. The browser and Typer CLI call the same application service. SQLite stores search criteria, extracted document text, property evidence, drafts, run progress and workflow history. Installed Codex or Claude Code CLIs perform bounded agent tasks. There is no email transport, tour booking, payment, or offer-submission adapter.

## Components

| Module | Responsibility |
|---|---|
| `models.py` | Strict typed inputs, property modes, cost bases, research and draft contracts |
| `costs.py` | Unit normalization, fixed-rate financing scenarios, unknown-cost accounting and comparable filters |
| `ranking.py` | Explainable fit/coverage, deterministic budget/size conflicts, identity and diligence defaults |
| `quality.py` | Inquiry quality and export readiness |
| `service.py` | CRUD, invalidation, imports/exports, review, workflow history and suppressions |
| `storage.py` / `files.py` | SQLite transactions and bounded file extraction/export |
| `pipeline.py` | Durable stage orchestration, caches, checkpoints, reservations, pause/resume and ownership |
| `providers.py` | Restricted native Codex/Claude subprocess adapters, schema validation and sanitized errors |
| `sources.py` / `research.py` | Independent public-page checks, bounded extraction and source/contact verification |
| `api.py` / `cli.py` | Loopback HTTP and terminal adapters to the shared service |
| `app/static/` | Static, accessible, responsive workspace; no Node build or external asset CDN |
| `start.py` / `app/launcher/` | Server lifecycle and managed-runtime protocol |

## Search and property records

A search fixes one transaction mode: residential rent/buy or commercial lease/buy. It includes a plain-language objective, optional client identity and needs brief, explicit preferences, cost assumptions and execution routes/budgets. Generated profile text is context; it cannot create a user fact or financial credential.

Each property has an address and unit, source identity, price with period/basis/currency, physical specifications, listing costs and availability. Apartment units remain separate even when they share a building or domain. The service preserves existing evidence and workflow history when merging an identity. Discovery/import does not grant trusted verification or human approval.

Evidence records retain URL, field, quote and timestamp. Dated market signals can guide discovery but do not establish availability or value. Research includes comparisons, required-condition checks, risk notes, diligence questions and an inquiry angle. The app recomputes scores and cost estimates from these inputs; it does not trust a model-supplied overall score.

## Cost and ranking decisions

Price normalization supports monthly/annual and square-foot monthly/annual rent. A buying price requires total-price basis. The calculator keeps known recurring cost, complete monthly estimate, known initial cash, complete initial cash, effective concession estimate, individual line items, assumptions and missing fields separate.

Financing uses a fixed-rate amortizing scenario. A missing interest rate stays missing; an explicit zero rate uses principal divided by number of payments. Cash purchases do not create a mortgage payment. Tax/insurance/association and other expenses use explicit user assumptions first, then listed amounts where supported. Neither a blank cost nor an unknown commercial rent structure silently becomes zero.

Fit uses weighted, evidence-backed property/location/requirements/timing matches and deterministic cost comparisons. Known budget or size conflicts remain blockers regardless of the score. Coverage is separate from suitability. Ranking places blocked rows after non-blocked rows, then orders by fit, confidence and available deal score.

The deal metric needs at least three compatible, current, independently checked asking-price comparables. It filters currency, mode, exact normalized comparison unit, property type, location, area band, and applicable lease structure/bedroom count. It does not mix closed sale prices with asking prices or calculate an appraisal. Unqualified comparable evidence leaves the score null.

## Agent stages

1. **Profile:** one call turns original needs into a useful brief and source plan. No web tools.
2. **Discover:** bounded batches find candidate properties and useful source URLs. Duplicates merge by property identity.
3. **Research:** one bounded call per property evaluates evidence, costs, fit, risks, comparables and diligence.
4. **Verify:** independent public-source checks match the intended property, availability, relevant evidence and contact source. A successful HTTP response alone is not confirmation.
5. **Draft:** one offline call per eligible property creates a three-message inquiry sequence from saved research. Quality checks and human review follow.

Model routes are independent per task. Call/time limits apply across providers; Claude-specific cost reservation is tracked separately. Each attempt is counted before execution. Cached profile/research/draft work is reused only when relevant input/provider/model fingerprints still match. An outreach route change invalidates drafts without unnecessarily repeating property research.

## Durable runs

Workers claim runs through SQLite transactions and ownership tokens. One active run per search and a global guard prevent uncontrolled duplicate work. Pause/cancel events propagate to provider subprocesses and bounded network fetches. Results are saved before the next checkpoint. Recovery marks abandoned runs recoverable and preserves conservatively reserved unreported cost.

The GUI polls real run state and events. A progress bar represents completed items for the current stage, not a fabricated overall percentage or estimated time. Both interfaces can inspect the same durable runs.

## Review and export

Research/review status and property workflow status are distinct. A user can shortlist a property, record a tour or offer outcome, and separately approve a draft. The application does not perform those real-world actions.

Exports have four scopes: full research, shortlist, source-checked tour candidates, and reviewed outreach. Outreach applies contact quality, recency, human-review, suppression and prior-contact guards and deduplicates mailboxes. A tour list still requires user review of costs, suitability and diligence. No export is a transaction authorization.

## Local launch and delivery

The static GUI and API bind to loopback; mutations require a per-launch token and same-origin checks. App data lives outside the repository and extracted release. Native launchers resolve the adjacent mutable `app/` payload, open a setup page immediately, prepare a pinned external runtime and redirect only when the app is ready.

The five launcher images cover six build targets: universal macOS, Windows x64/ARM64 and Linux x86_64/ARM64. Changing application source/static assets does not rebuild an image. The initial images are unsigned. Build and source-test evidence does not claim the full native clean-system acceptance matrix. See [Install](INSTALL.md) and [Security](SECURITY.md).

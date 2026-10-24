# Local HTTP contract

Real Estate Finder serves one local user on `127.0.0.1`. The browser and CLI use the same service and SQLite workspace. The API is not a hosted or multi-user service.

Read `GET /api/session` from the same origin; keep its token in memory and send `X-Session-Token` for mutations. Host, Origin and cross-site fetch checks protect the loopback boundary. Never put a token in a URL. `GET /api/openapi.json` returns the FastAPI schema.

| Method | Path | Operation |
|---|---|---|
| GET | `/health/ready` | App identity, version and readiness |
| GET | `/api/session` | Token, data path, installed provider capabilities |
| GET/POST | `/api/searches` | List or create searches |
| GET/PATCH | `/api/searches/{id}` | Detail or nested input/settings update |
| POST | `/api/searches/{id}/documents` | Multipart `file`: extract needs/supporting text |
| DELETE | `/api/searches/{id}/documents/{document_id}` | Remove a document and invalidate dependent work |
| POST | `/api/searches/{id}/import` | Multipart property CSV/XLSX |
| POST | `/api/searches/{id}/verification/import` | Multipart verifier CSV and optional `source` |
| POST | `/api/searches/{id}/outcomes/import` | Multipart workflow tracking CSV |
| POST | `/api/searches/{id}/runs` | `{ "stage": "pipeline" }` or one stage; returns `{run}` |
| GET | `/api/runs/{id}` | `{run, events}` |
| POST | `/api/runs/{id}/pause` | Stop active work and preserve results |
| POST | `/api/runs/{id}/resume` | Resume with retained counters/caches |
| POST | `/api/runs/{id}/cancel` | Stop without scheduling more work |
| PATCH | `/api/properties/{id}` | Edit a property, review, drafts or workflow tracking |
| PATCH | `/api/opportunities/{id}` | Alias for the shared internal opportunity contract |
| GET | `/api/searches/{id}/export?format=csv&scope=all` | CSV/XLSX; `all`, `shortlist`, `outreach`, `tour` |
| GET/POST | `/api/suppressions` | Global email/domain exclusions |
| POST | `/api/suppressions/import` | Multipart exclusion CSV |
| POST | `/api/demo` | Create and run fictional data; optional `?mode=commercial_lease` |
| POST | `/api/quit` | Graceful local app/worker shutdown |

## Inputs

`SearchInput` contains `name`, `mode`, `objective`, `client`, `preferences`, `cost_assumptions`, `settings`, and `demo`.

Modes: `residential_rent`, `residential_buy`, `commercial_lease`, `commercial_buy`.

The client contains optional name/email/phone/organization and `brief_text`. Preferences include locations, property types, three-letter currency, monthly/asking-price budgets, cash available, minimum/maximum area in square feet, bedrooms/bathrooms, intended use, must-haves, exclusions, move date and maximum listing age. Cost assumptions hold financing type, down payment, rate, term and individual recurring/upfront amounts. Unknown amounts use `null`; zero means explicitly zero.

```json
{
  "name": "Small workshop",
  "mode": "commercial_lease",
  "objective": "Find usable light-industrial space with loading access.",
  "preferences": {
    "locations": ["Dayton, OH"],
    "budget_monthly_max": 4500,
    "min_area_sqft": 1500,
    "intended_use": "Light fabrication and storage"
  },
  "settings": {
    "provider": "codex",
    "target_results": 25,
    "routes": {"research": {"provider": "claude", "model": ""}}
  }
}
```

Each of `routes.profile`, `discovery`, `research` and `outreach` accepts a provider (`inherit`, `codex`, `claude`) and model string. If the route changes provider and leaves model blank, that provider's default is used. It does not inherit a model name intended for another provider.

PATCH recursively merges nested objects and validates the full resulting input. Edits that conflict with active work return `409`; pause first. Content and relevant route edits invalidate dependent research/drafts. Demo and live searches remain separate. Exact types and bounds are available from OpenAPI or `python app/run.py schema search --json`.

## Properties and review

Search detail returns `search`, `properties`, the compatibility alias `opportunities`, `runs`, `counts`, `documents`, and `outcomes`. Rows are ranked and retain separate fit, confidence, deal, cost estimate, sources, blockers and review status.

A property has a mode, title, address/unit/location, source URLs, price/basis/currency, area and optional bedrooms/bathrooms, listed charges, lease type, dates, availability, contacts, evidence, comparables, diligence and drafts. A property import cannot make its own source checks or review trusted. JSON schema name `property` gives the import contract.

`workflow_status` is one of `new`, `shortlisted`, `contacted`, `replied`, `tour_scheduled`, `toured`, `applied`, `offered`, `negotiating`, `closed`, `rejected`, `not_interested`, `bounced`. This is separate from research/review `status`. Tracking records a user's action; it does not perform that action. Follow-up dates and notes remain local.

Every draft sequence has three messages with `step`, `delay_days`, `subject`, `body`, `source_urls`, `user_claims`, and `angle`. Review approval cannot send mail. Outreach export requires current evidence, acceptable contact status, human approval and no applicable suppression/history blocker. Demo rows never become eligible for live outreach export. The `tour` scope includes source-matched, recently checked available properties without known required-condition conflicts. It is a candidate inspection list; unresolved costs and diligence still need review. Empty filtered exports return `EXPORT-EMPTY`.

Verifier CSV fields are `email`, `status`, `verification_source`, `verification_at`. Outcomes use a property ID or a listing URL to identify the specific property; do not infer a property action from a shared broker mailbox.

## Runs and errors

Stages: `pipeline`, `profile`, `discover`, `research`, `verify`, `draft`. States: `queued`, `running`, `paused`, `completed`, `failed`, `budget_exhausted`, `cancelled`. `completed` and `total` measure the current stage; `stage_index`/`stage_count` show pipeline position. They are not a predicted global ETA.

Run usage records reported provider/model/task and optional token/cost values. Missing cost reports are unpriced, not zero. Call, elapsed-time, concurrency, timeout and response limits bound all providers. The dollar allowance applies to Claude calls supported by that adapter; it is not a guaranteed combined dollar cap for mixed providers.

Errors use `{"error":{"code":"STABLE-CODE","message":"Actionable message"}}`. Raw documents, secrets and provider stderr are not echoed. Uploads are limited to 8 MB and supporting text is bounded separately. No endpoint sends messages, books a tour, submits an application, places an offer, pays a fee, or exports provider credentials.

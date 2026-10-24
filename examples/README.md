# Import and configuration examples

All properties, people, organizations and `.example` domains in these files are fictional. They are format examples; they are not available listings or verified contacts. Replace them before a live search. Use **Demo** for a full offline run.

- `search-settings.json` starts a commercial lease search. Enter a real location, needs, contact details if wanted, provider models, budget and cost assumptions. Creating a search does not start an agent. A blank cost/rate remains unknown.
- `properties.csv` shows two separate residential rental units, including JSON in the `costs` column. Import into a residential rent search. Each row needs a title and either a listing URL or address plus location.
- `commercial-properties.csv` shows annual per-square-foot rent and operating expenses separately. Create a commercial lease search before importing it.
- `outcomes.csv` identifies an exact property using its exported `property_id` or listing URL. `workflow_status` records a user action, such as `shortlisted`, `contacted`, `tour_scheduled`, `toured`, `applied`, `offered`, `negotiating`, or `closed`. It does not perform that action.
- `verification.csv` requires the actual verifier and check timestamp. Replace the demonstration date and status with real results. MX routing alone does not establish a verified mailbox.
- `suppressions.csv` excludes a mailbox or a whole domain globally. Rejecting one property does not exclude its entire broker.

Optional properties use blanks for unknown values. A literal zero is an affirmative zero amount. `price_basis` accepts `total`, `month`, `year`, `sqft_year`, `sqft_month`, or `unknown`; do not infer the basis from a price alone. Square-foot rates require `area_sqft` for monthly totals. Currency is kept distinct; the app does not assume exchange rates.

Imports strip claims of trusted source verification. Inspect returned imported, duplicate, rejected and skipped counts; a partial import is not an all-row success. Run research/verification before treating imported claims as current. The CLI's `schema property --json` provides exact enums and field limits.

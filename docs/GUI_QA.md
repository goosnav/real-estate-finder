# GUI verification

The app was exercised against its actual FastAPI/SQLite service in Chromium on Linux. Verification used isolated local data and explicitly fictional demo properties. No messages, inquiries, applications, offers, or payments were sent.

## Design

The interface applies [`goosnav-web-design`](https://github.com/goosnav/goosnav-skill-pack/tree/main/goosnav-web-design): white and black structure, bold system sans headings, serif explanations, tabular monospace values, ruled comparisons, square controls, blue actions, and yellow attention markers. No font or image service is required.

The main workspace has **Brief**, **Properties**, **Inquiries**, and **Export**. The property inspector keeps **Costs**, **Fit & checks**, **Evidence**, **Inquiry**, and **Activity** together. Search-wide cost assumptions explicitly override listing values when entered; blank fields preserve an unknown or use the listing value. Unknown totals remain visible as a known subtotal plus unresolved costs.

## Exercised workflows

| Workflow | Result |
|---|---|
| Empty workspace, new search, and help | Functional empty state, create form, CLI setup, provider status, and demo selection |
| All four search modes | Residential rent/buy and commercial lease/buy created, saved, and reloaded with the correct conditional fields |
| Agent routing | Independent research and writing providers/models persisted after reload |
| Four complete offline demos | Five fictional properties per mode; available and unavailable listings remained distinct |
| Progress | Real stage counts, item counts, call totals, elapsed time, activity log, and concise screen-reader status updates |
| Repeat workflow | Completed cached work was reused; repeat demo reported zero new agent calls |
| Type lock | Search mode became disabled after properties existed, with a concise new-search instruction |
| Document handling | TXT upload, character count, persistence, and removal |
| Listing import | CSV imported a property into the current search |
| Cost editing | Per-property asking price change saved and recalculated the estimate |
| Comparable value | Three fictional matched comparables produced a deal score of 76 and a clearly labeled 13% below-median comparison; insufficient evidence remained unscored |
| Fit explanation | Rendered earned points and weights, known/unknown state, reasons, sources, and correctly filled bars |
| Due diligence | A manual confirmation saved and persisted |
| Activity | Tour status, notes, and follow-up controls worked |
| Inquiry review | Three drafts displayed; subject edits, review, and approval saved |
| Exports | Full CSV and shortlisted Excel files downloaded; fictional outreach export stayed disabled |
| Invalid input | An invalid area range retained input, marked the field, and showed a correction; corrected values saved |
| Cash scenario | Inapplicable hidden financing fields were disabled and did not block saving |
| Keyboard | Native controls, dialog close, arrow-key detail tabs, and visible focus worked |
| Responsive layouts | Desktop at 1440px, mobile at 390px, and narrow 320px; no page or dialog overflow |
| Long inquiry text | Mobile draft textareas expanded to fit their complete contents |
| Zoom | Main actions remained reachable with 200% content zoom |
| Quit | The actual GUI quit action closed the local service and displayed saved-work confirmation |

No JavaScript page errors occurred during the completed workflow checks. Browser automation used the real application endpoints; there was no mocked API or simulated progress.

## Screenshots

- [Property comparison](screenshots/properties.png)
- [Search brief](screenshots/brief.png)
- [Cost inspector](screenshots/property-costs.png)
- [Fit and checks](screenshots/property-fit.png)
- [Comparable evidence](screenshots/property-comparables.png)
- [Source evidence](screenshots/evidence.png)
- [Inquiry review](screenshots/inquiry.png)
- [Export](screenshots/export.png)
- [Task model routes](screenshots/model-routes.png)
- [Measured progress](screenshots/progress.png)
- [Mobile comparison](screenshots/mobile.png)
- [Mobile inquiry](screenshots/mobile-inquiry.png)
- [Narrow brief](screenshots/mobile-brief.png)
- [Empty workspace](screenshots/empty.png)

All depicted addresses, companies, contacts, listing claims, and costs are fictional demonstration data. Their `.example` links are illustrations.

## Limits of this verification

These checks verify the implemented local workflow, interface, persistence, and exports. They do not establish live discovery coverage, listing accuracy, provider model quality, or production research cost. No authenticated Codex or Claude Code CLI was available on the verification host. Native launcher and operating-system acceptance are tracked separately in [installation guidance](INSTALL.md).

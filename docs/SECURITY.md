# Security and data boundaries

Real Estate Finder is local research and inquiry-preparation software. Search documents, financial assumptions, property evidence, contacts and drafts are stored on the user's computer. Live tasks send selected inputs to the configured Codex or Claude Code provider. The app has no email, phone, payment, application-submission, offer or tour-booking transport.

## Private inputs and credentials

The SQLite workspace stores extracted supporting text, search criteria, cost assumptions, source evidence, contact details, drafts and workflow history. Original uploads are read in memory; extracted text and a content hash are stored. Removing a document invalidates dependent work. Prior generated records and user-created exports can still contain supplied information.

App data lives under the OS-specific `RealEstateFinder` directory, separate from Job Finder, Auto Outbound and program source. Source/CLI users can select a workspace with `--data-dir` or `REAL_ESTATE_FINDER_DATA`. The app attempts restrictive directory/database permissions where supported. It does not encrypt its database; protect local files and backups using the operating-system controls appropriate to your device.

Authentication stays with the installed provider CLI. The app does not copy its credential database or store an extra provider API key. The provider process runs as the current OS user with that provider's normal authentication environment. The brief task receives user-provided context without web tools; research uses bounded relevant context. Search prompts prohibit private identity, financing or contact details in public search queries. Prompt instructions are not a data-loss-prevention guarantee for a third-party model.

## Loopback API

The server binds to loopback. Requests check Host and Origin, reject cross-site fetches, and require a random in-memory session token for mutations. Responses disable caching and add framing, referrer, content-type and content-security headers. Validation errors identify the field without echoing private input; unexpected failures use a sanitized stable error.

These controls resist requests from unrelated websites. They are not an authentication boundary against another process running as the same OS user. A hosted or public-network deployment would require a different authentication and authorization design.

## Agent processes

Providers execute explicit argument arrays with `shell=False` in isolated temporary working directories. User criteria, document text, model names and prompts do not become shell commands. Native executable capability checks reject unsupported isolation/output flags; Windows batch wrappers are not accepted.

Codex uses a read-only sandbox, ephemeral state, strict configuration and disabled shell/apps/plugins/hooks/memory features. Claude uses safe mode, no session persistence, a strict empty MCP configuration and an explicit tool allowlist. Research enables web tools; profile and drafting disable web access. Input/output sizes, per-call duration, cancellation and process-tree cleanup are bounded. Raw provider stderr is not returned to the browser.

Uploaded files, pages and model output remain untrusted. Typed contracts, independent source checks, exact-source context and human review reduce risk, but cannot guarantee a model will correctly interpret every adversarial page. Injected text cannot call a sending/payment adapter because no such adapter exists.

## Public sources

The independent fetcher accepts public HTTP(S) URLs without credentials. It resolves and rejects non-public IP addresses, validates redirects, and connects to a checked IP while preserving TLS hostname validation. Byte, redirect, content-type, timeout and cancellation limits bound reads.

Blocked, private, paywalled or inaccessible pages remain unavailable. The app does not bypass logins, CAPTCHAs, access controls, or licensed-data requirements. No private MLS or paid feed is bundled. Public listing content can be wrong or stale; current availability requires an exact-property match rather than HTTP 200 alone. Dated news or a development plan is not a verified listing.

## Property and contact integrity

- Fit and deal scores are separate. Neither certifies legal use, condition, ownership, safety, financing, or market value.
- Listed charges, user assumptions and unknown costs remain separate. Unknown NNN/CAM, interest, tax or other charges are not silently set to zero.
- Deal comparison requires compatible checked asking-price evidence. Closed sales and asking prices are not mixed.
- Imported/model-produced rows cannot grant their own trusted source checks or human approval.
- User facts in an inquiry must be supported by the supplied material. Drafts must not invent preapproval, cash readiness, business credentials or an offer.
- A published email or DNS MX record is not a verified mailbox. Unreadable source pages leave uncertainty unless a valid independent verification is present.
- Explicit email/domain exclusions and prior contact history guard outreach. A shared broker mailbox is not enough to identify a particular property's outcome.

Objective property requirements are appropriate ranking inputs. The prompts exclude protected personal traits and demographic neighborhood steering. Diligence questions point to the relevant owner, qualified professional or public authority; an agent's opinion does not resolve them.

## Files and exports

Each upload is bounded at 8 MB. Supporting material is limited to four documents and 60,000 extracted/pasted characters. PDF extraction rejects encrypted/excessive-page documents; scanned PDFs need OCR before upload. Office archive expansion, spreadsheet rows and columns are bounded. File names do not become arbitrary filesystem paths.

CSV/XLSX output neutralizes spreadsheet formula prefixes. Evidence and context are retained for review. Files may contain private search, contact and financial information. Moving an export to an email, CRM or other tool is a separate user action.

## Budgets and recovery

Every attempted provider call consumes the call allowance. Total run time, per-call deadlines, page count, concurrency, output size and cancellation further bound work. Claude reserves a conservative allowance before calls, retaining an interrupted/unreported reservation. Codex lacks an equivalent enforceable dollar cap in this integration; unreported usage is not displayed as zero spend.

Atomic SQLite worker ownership avoids duplicate claims. Pause, cancel and Quit preserve completed work. Resumable runs reuse only valid caches; a cancelled run is not silently restarted. Local backups use SQLite's consistent backup API.

## Native launchers

The Go supervisor uses OS locks, an unguessable setup capability, same-origin retries, pinned-tool checksums, an explicit release whitelist and an external runtime fingerprint. A failed runtime update preserves an earlier ready environment. A browser-open failure writes the exact local address to `logs/OPEN_THIS_URL.txt`.

Initial images are unsigned. Cross-compilation and Linux headless-browser checks do not establish full native desktop acceptance for macOS, Windows or ARM Linux. Signing/notarization and the complete native failure/recovery matrix remain separate work. See [Install](INSTALL.md).

When reporting a public issue, provide the version, stable error code and reproduction steps, and remove private documents, financial details, contacts, tokens and provider output.

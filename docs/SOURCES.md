# Sources, evidence, and discovery

Real Estate Finder uses your installed, authenticated Codex or Claude Code CLI for bounded web research. It does not include MLS credentials, paid property data, a browser-login scraper, or a promise of complete market coverage. Research covers public pages the selected CLI can access. You can add specific listing URLs and import a property CSV to supplement web discovery.

## Source strategy

Use the Search screen to choose one transaction mode and describe an objective. The agent creates a brief, searches in small batches, then researches individual properties. Useful sources include:

| Source | Useful evidence | What remains to check |
|---|---|---|
| Owner, landlord, property manager, or listing broker | A specific unit, published asking terms, contact details, current availability | Authority to offer it, total charges, actual condition, written terms |
| Public listing portals | Candidate properties, addresses, price changes, linked brokers | Original listing, unit identity, stale or duplicate ads, access restrictions |
| Local government planning and property records | Parcel identity, zoning maps, permits, public assessments | Whether a specific intended use is approved; assessments are not sale prices |
| Official transport and infrastructure plans | A relevant planned change with a dated source | Approval stage, delivery timing, whether it helps the user's stated objective |
| Business, development, and local news | Expansion, relocation, vacancy, new supply, incentive leads | Publication date versus event date; a news signal is not an available property |
| Comparable listing pages | Asking prices for similar, current properties | Same transaction, area/unit basis, currency, property type, location, and lease structure |

Prompt instructions request source links and short evidence excerpts, keep listing facts separate from assumptions, and leave unsupported fields unknown. They do not permit invented prices, contacts, availability, or personal qualifications. Web text and uploaded files are treated as untrusted data, not instructions to execute.

## Direct page checks

A separate bounded HTTP client checks public source pages. It validates DNS addresses, pins the accepted public address for a request, validates TLS, limits redirects, bytes, and elapsed time, rejects private networks, and supports cancellation. It does not execute page JavaScript. Some legitimate sites require scripts or block this kind of access; those fields remain unverified.

The parser reads [Schema.org RealEstateListing](https://schema.org/RealEstateListing), [Offer](https://schema.org/Offer), and typed property JSON-LD. These are publisher assertions, not independent certification. It handles bounded nested graphs and common property references, keeps multiple offers separate, and refuses to choose between conflicting records. Square metres are converted to square feet only when the source explicitly identifies the area unit. A per-area rent retains its month/year basis; an unspecified period remains unknown.

A page returning HTTP 200 is only reachable. Availability requires one matching property offer with explicit availability, or explicit availability text beside the same property identity. HTTP 404/410 marks a listing unavailable without claiming why it disappeared; access denied, rate limiting, and server errors remain unknown. An expired validity date applies only to the matching property, and a date-only deadline includes the entire stated date.

The default independent-check allowance is six pages per property: its listing, its published contact page, then comparable pages and other evidence within that limit. You can change this bound in Agent settings.

The app records which evidence excerpts match accessible page text and when it checked them. A matched property source requires the exact property identity; a quote from an unrelated broker contact page does not satisfy that check. Unstructured listing collections and pages containing different units remain unverified. Explicit sale availability cannot confirm a rental listing, or the reverse. It checks published email addresses against the cited page and DNS where possible. An MX result shows the domain receives mail; it does not verify that a particular mailbox exists or that anyone wants unsolicited messages. No email is sent.

## Comparable evidence

A model-proposed comparable does not become verified simply because it includes a URL. Independent checks must match a specific available property, asking amount, currency, price basis, relevant area/bedrooms/type/location, and any claimed lease structure. Pages outside the source-page budget remain unverified. The checked property address and unit are retained when available so copies on different portals do not masquerade as independent comparables. A closed-sale claim is not automatically verified from an asking-price offer.

The deal indicator uses only qualified, independently checked comparables and clearly reports an insufficient evidence result when that set is too small. It is an asking-price comparison, not an appraisal, investment return, price prediction, or guaranteed bargain. Listings, concessions, condition, and fees can make two apparently similar properties materially different.

## Costs and due diligence

The following primary sources informed the questions and cost separation in this app. The references provide general context; location-specific rules and the actual written terms must be checked for each property.

- [CFPB: principal and interest versus total monthly payment](https://www.consumerfinance.gov/ask-cfpb/on-a-mortgage-whats-the-difference-between-my-principal-and-interest-payment-and-my-total-monthly-payment-en-1941/) explains that taxes, property insurance, mortgage insurance, and separate association fees can add to principal and interest. The app keeps these costs and user assumptions visible rather than treating a mortgage calculation as a total ownership quote.
- [Nareit: triple net lease](https://www.reit.com/glossary/triple-net-lease) describes operating costs beyond base rent. The app preserves lease type and operating-expense basis; missing NNN/CAM charges remain unknown.
- [SBA: choosing a business location](https://www.sba.gov/business-guide/launch-your-business/pick-your-business-location) recommends checking local zoning and permits for the intended business. An agent can locate sources and prepare questions; a marketing description does not prove permission to operate.
- [NYC Small Business Services: Commercial Leasing Guide](https://www.nyc.gov/assets/sbs/downloads/pdf/about/reports/commercial-lease-guide-accessible.pdf) identifies issues such as base and additional rent, permitted use, utilities, deposit, alterations, guarantees, and renewal terms. Its legal details are specific to New York City; the app uses these as questions to resolve, not rules applied to every location.
- [FTC: rental listing scams](https://consumer.ftc.gov/consumer-alerts/2023/08/you-got-apartment-spot-rental-listing-scams-you-pay) describes copied listings and advises verifying the management company and seeing the property. The app preserves original links and contact evidence and treats an unusually low price as something to investigate.
- [HUD: fair housing rights and obligations](https://www.hud.gov/program_offices/fair_housing_equal_opp/fair_housing_rights_and_obligations) describes protected characteristics in housing. This product scores stated property requirements and costs, not neighborhood demographics or protected personal traits. Accessibility requirements may be expressed as concrete property features.

Source review: 2026-10-24 UTC. Public-source verification can establish that a page says something; it cannot certify title, condition, zoning compliance, inspection results, affordability, financing approval, or a counterparty's identity. Those remain explicit follow-up questions.

## Reproducibility

The included demo is fictional and uses reserved example domains. Automated tests use controlled public-page fixtures, including conflicting units, unknown price periods, expired offers, missing pages, and unavailable email evidence. These fixtures test behavior without spending your model budget or contacting real owners. A live search still requires the selected CLI and access to its web tools.

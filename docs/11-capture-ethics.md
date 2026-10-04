# 11 — Capture ethics & compliance policy

WebShot operates in a domain with real misuse potential. This policy is part
of the product, not an afterthought; docs and README must state it plainly.

## Principles

1. **Authorized-view only.** WebShot captures what the *signed-in user's own
   browser is authorized to render*. The protected-viewer path renders pages to
   the authorized viewer and reassembles them; it never requests the protected
   native file, never manipulates permissions, never inspects or exfiltrates
   credentials. Features that would bypass access controls, DRM, IRM, or
   CAPTCHAs are on the permanent never-list (08 §never-list) and PRs adding
   them are rejected on policy, not feasibility. A capture is still a copy of
   what the user can see, much as printing each page would be: if a document's
   owner has blocked downloading, printing or copying, treat that block as
   covering WebShot too. WebShot does not detect such a block for you.
2. **Single-document, human-initiated.** WebShot is not a crawler. It fetches
   the page the user asked for (plus that page's own subresources, as any
   browser does). It sends no synthetic traffic beyond one page load per
   invocation, so crawl-rate ethics largely do not apply; batch mode (roadmap)
   must add per-host pacing before shipping. **That trigger is volume**, and it
   is a different test from principle 3's: a bounded batch paces itself without
   thereby becoming a crawler.

   `webshot journey` (docs/13) is the first bounded batch in the code. It
   captures the modules one learning platform lists for a single journey, so it
   makes more than one page load per invocation, and it paces itself: at least
   `--min-interval` seconds (1.0 by default) between opening one module and the
   next, with the achieved intervals reported.
3. **robots.txt stance (documented, deliberate):** robots.txt governs
   *crawlers*. A user-initiated capture through a real browser is
   agent-of-the-user activity, equivalent to the user printing the page. v3
   therefore does not consult robots.txt.

   **The stance flips for _crawling_, not for volume.** docs/01's non-goal draws
   the line this principle rests on: *"Batch input (a list of URLs) is in scope;
   link discovery and frontier management are not."* The trigger is that second
   half. A feature that discovers its own targets — following links outward,
   managing a frontier — is a crawler, and robots.txt compliance becomes its
   default. A feature that captures a bounded set the user named, or that one
   authenticated application lists as its own contents, is not — however many
   pages that set holds.

   This wording used to say the stance flips "if batch/crawl features ever
   ship", welding together two categories docs/01 keeps apart. Read literally it
   makes the journey walker of docs/13 consult robots.txt, and on an
   authenticated LMS — whose robots.txt addresses crawlers that can never reach
   the content — that would refuse a user their own completed training record.
   Principle 1 governs that case instead: WebShot captures what the signed-in
   user's own browser is authorized to render. docs/13 had already drawn this
   line for the walker ("bounded enumeration, not spidering ... the non-goal in
   docs/01 stands; per-host pacing from docs/11 §2 applies"); this principle had
   not caught up.
4. **Terms-of-service responsibility sits with the user.** Docs must say:
   captured content remains governed by the source's terms and copyright;
   WebShot adds provenance (source URL, timestamp, checksums) precisely so
   downstream use can be audited. WebShot never strips attribution. `clean`
   mode hides overlays such as cookie notices and sign-up prompts, much as a
   browser's reader view does; that makes a page readable, and it is not
   permission. A site's terms still decide what may be captured and kept.
5. **Privacy by default.** Credentials and session state never enter outputs
   (spec §6.1); password values are redacted (§6.2); bundles record what
   was captured and when, but no browsing history beyond the requested page;
   auth profiles are local, owner-only, and per-trust-boundary.
6. **PII awareness, not PII laundering.** Captured pages may contain personal
   data. The manifest's provenance fields make the data's origin and capture
   time explicit so retention policies can be applied by the operator. A PII
   redaction pass is explicitly out of scope for v3 (it creates false
   confidence); if ever added it ships as opt-in with documented limits.
7. **OCR honesty.** Machine-recognized text is labeled as OCR in artifacts,
   with confidence values preserved, and the source pixels always ship
   alongside so claims can be verified against the image.

8. **Bundles at rest are sensitive artifacts.** A bundle of authenticated or
   protected content is as sensitive as the content itself. The default
   `output/` directory ships gitignored, docs say to treat bundles under the
   same handling rules as their source documents, and the manifest's auth-mode
   field exists so downstream tooling can flag authenticated captures.

## Enforcement hooks

- CONTRIBUTING.md links this policy, and the PR template's checklist includes
  "does not weaken the capture ethics policy (docs/11-capture-ethics.md)"
- The never-list is tested where testable (e.g., protected path makes no
  request to the native-file endpoint — asserted against recorded fixtures).
- SECURITY.md covers responsible disclosure for cases where WebShot
  could be made to over-capture.

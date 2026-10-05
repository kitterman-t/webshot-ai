# 10 — Requirements traceability matrix

Every goal maps to spec sections, implementation phases, and the tests/gates
that prove it. A goal with an empty cell is a planning bug.

| Goal | Spec (04) | Architecture (02) | Phase (05) | Verified by (06/09) |
|---|---|---|---|---|
| G1 Delegate commodity functionality | §Components refs throughout | bridge modules table | P1 (PDF), P2 (extract), P3 (adapters/discovery/OCR engines) | LOC budget check per phase PR (revised in P1-7 and P3-6 — the tables record which revisions are accounting and which are work); spike report S3–S6; grep gate: no bespoke OCR-sandwich/chunker/parsers remain; `tests/test_footprint.py` also proves the *replaced* dependencies (bleach, reportlab) are gone from the environment and the lock |
| G2 Preserve every v2.2 capability | §1.1 flag table | CaptureResult contract | P0 (golden baseline), P2.4 (parity), P2.5 (`--legacy-bundle`) | golden corpus job; parity harness thresholds; CLI compat tests |
| G3 Interoperable outputs | §2 outputs; chunk schema | Data contracts | P0.4 (schemas), P2 | JSON Schema validation job; `content.json` loads in docling-core (S3); chunks carry `doc_items` (S4) |
| G4 Agent-usable (MCP) | §1.3, §5.9, §6.8 | `mcp_server/` package (policy · bridge · results · cli — docs/09 P4-2) | P4.1 | MCP client harness in CI: a session captures a fixture URL and reads markdown + chunks without touching the filesystem (`tests/test_mcp_server.py`), `webshot mcp` proven over a real stdio subprocess, the tool set checked against the frozen contract, and one denied case per §6.8 rule |
| G5 Accessibility proven | `--validate-pdf`; exit 7 | render/post | P1.1, P1.6, P5.1 | S1 (StructTreeRoot/MarkInfo/outline asserted); veraPDF CI job (non-blocking → blocking); `--validate-pdf=strict` reaches exit 7 through a real invocation, and its QA report carries the veraPDF findings rather than only the message (docs/09 P5-2) |
| G6 Permissive license tree | §6.9 | — | P0.3, P5.6 | pip-licenses CI gate; ADR-0006; re-run against the final `uv.lock` at the release — 156 packages, none denied, project identified as `webshot 3.0.0` |
| G7 One-pip-install usability | §1.2 doctor; §1.1 `--config`; §4 footprint | extras layout; `settings.py` | P0.1, P0.5, P4.2, P4.4 | doctor tests incl. Ghostscript-version + Docker-daemon checks (09 §env); no-torch assertion in CI (S3); settings precedence and explicit-path-only tests (`tests/test_settings.py`); the docs site builds `--strict` as a blocking CI job, with the CLI reference generated from the parser and a drift test; **the built wheel is installed into an empty virtualenv and used from outside the checkout** (`tools/release/wheel_check.py`, a gate in `release.yml` — docs/09 P5-6), which is the only check that tests what a user installs rather than what the repository contains |

Security requirements (spec §6) → tests:

| Requirement | Proven by |
|---|---|
| §6.1 no credentials in bundles | unit test: bundle tree scanned for storage-state/profile paths after fixture run with auth configured |
| §6.2 password redaction | password-form fixture; assert the password value is absent from all artifacts **AND the redacted form-field record (`[REDACTED PASSWORD]`) is present** — absence-only would pass vacuously if form extraction silently vanished (red-team C6) |
| §6.3 profile permissions | unit tests on profile creation (POSIX mode under a permissive umask; Windows DACL, created and repaired) + refusal path (MCP on both platforms; the CLI on Windows) in `tests/test_winacl.py` and `tests/test_mcp_policy.py`; the Windows half on a profile Chromium wrote in `windows-smoke.yml`. *The creation test this row named did not exist until docs/09 P10-25* |
| §6.4 protected-viewer invariants | contract tests on recorded viewer fixtures; code review checklist |
| §6.5 sanitization | hypothesis property tests over **both** nh3 allowlists — the DOM snapshot and rendered local documents — plus pinned adversarial cases per allowlist (`tests/test_sanitizer.py`, Phases 2 and 3) |
| §6.6 defusedxml | XXE and entity-expansion fixtures refused before any converter reads the file; a plain SVG DOCTYPE still accepted; malformed XML still rendered, because the pass is a security gate and not a validity one (`tests/test_source_adapters.py`, Phase 3) |
| §6.7 size guard | 100 MB refusal for plain files, plus archive limits read from the central directory — expanded size, member count, zip-slip paths, symlink members — so a bomb is refused without being decompressed (`tests/test_source_adapters.py`, Phase 3) |
| §6.8 MCP guardrails | one denied-case test per rule, at two levels: as plain function calls (`tests/test_mcp_policy.py` — absolute/`..`/symlink traversal, `~` non-expansion, every private and link-local range, a name that resolves inward, a redirect that does, credential-shaped `auth_profile` values, an unprepared or world-readable profile) and again through a real client session (`tests/test_mcp_server.py` — the same refusals over the wire, plus: the `capture` schema carries no output or credential parameter, an undeclared argument is rejected, a capture whose `final_url` failed the policy leaves the output root empty, and concurrent captures serialize) |
| §6.9 license gate | CI job |
| §6.10 no config discovery | `tests/test_settings.py`: a `webshot.toml` in the working directory is ignored, and only `--config`/`WEBSHOT_CONFIG` are read |

Invariants (spec §2.2) → tests: atomicity (kill-during-publish test leaves no
partial dir); PDF pre-publication validation (corrupted-PDF fixture); protected
page-count invariant (mismatch fixture must exit 5); OCR-degradation warning
(tesseract removed from PATH in a doctor/degradation test).

Exit codes (spec §3) → tests, completed in Phase 5.1 (`tests/test_exit_codes.py`):

| Code | Proven by |
|---|---|
| 0 | a real capture of a fixture; the QA report carries `exit_code: 0` and **no** `error` key |
| 2 | `--pdfa` on the web path, `--require-ocr --no-ocr`, a destination the lock cannot be created beside, and a second run against a held output path; the report's `options` is null when the options are what failed |
| 3 | a loopback server that never answers, under `--timeout 2` |
| 4 | a loopback server that answers 401, and a redirect from one loopback origin to a sign-in page on another (`tests/test_landing.py`, spec §5 item 4) |
| 5 | the protected path's page-count and sequence checks, driven with a planted mismatch, and an empty page under `--require-content`, end to end, with nothing published (spec §5 item 14) |
| 6 | `--require-ocr` with Tesseract off PATH; refused before the browser opens, so no PDF is produced |
| 7 | `validate_pdf()` against a truncated file, and `--validate-pdf=strict` end to end with veraPDF's verdict injected |
| 8 | a failure injected at the real publication swap (`publish_ai_bundle`), with the rollback and the report real — no command-line route remains, because the lock refuses an impossible destination up front (docs/09 P5-10) |
| 9 | `PLAYWRIGHT_BROWSERS_PATH` pointing at an empty directory |
| never 1 | an AST scan asserting no module raises the unclassified base class, and `classify()` returning something other than 1 for every exception shape |
| §5.1 lock | a second run against a held output path exits 2, and against a shared `--ai-bundle-dir` too; **a real second process** is excluded and the kernel releases the lock when it dies; a 250-byte output name still gets a lock |

Every one of those cases also asserts that `--report` wrote its file and that
the file's `exit_code` equals the process's, which is what makes the field mean
something (docs/09 P5-1).

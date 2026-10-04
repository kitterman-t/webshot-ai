# 09 — Validation spike report

**Date:** 2026-08-19 · **Method:** every load-bearing technical assumption in
the plan was exercised empirically — real installs in an isolated venv, run
against this repo's own fixtures on the maintainer's machine (macOS, Python
3.12/3.13). This converts the plan from "well-researched" to "verified design."

## Reading this log

This is the findings log kept while WebShot v3 was built, in a private
repository, carried over with its entries and numbering intact. It records what
was measured, which assumption it confirmed or overturned, and what changed as
a result.

- **S1 to S9** are the validation spikes run against the plan before any code
  was written (the table below).
- **Pn-m** is finding *m* of series *n*. P0 to P5 follow the phases of
  [05-implementation-plan.md](05-implementation-plan.md); later series are
  named for the work that produced them. Code comments, tests and the changelog
  cite these numbers (P7-13, say) instead of repeating the story. P9 and P11 to
  P13 live in [13-lms-capture-proposal.md](13-lms-capture-proposal.md), beside
  the design they amend. Other gaps, P15 and P17 among them, are entries about
  material outside this repository that were removed; the rest keep their
  numbers so that citations stay valid.
- **References into the private repository do not resolve here.** `#71` or
  "PR #9" is one of its pull requests, a long number such as 36350676728 is one
  of its GitHub Actions runs, and a short hash is one of its commits. Entries
  about CI describe its setup at the time, which included self-hosted runners;
  the runner scripts and the runner go-live checklist (docs/12) those entries
  mention are not part of this copy. CI here runs on GitHub-hosted runners only
  ([06 §Runners](06-quality-and-testing.md#runners)).

Good entries to start with:

- **S7**: converting the tagged web PDF to PDF/A stripped its accessibility
  tags, so web-path PDF/A was dropped before it was built.
- **P2-1**: Playwright's accessibility snapshot leaked password values that
  every other artifact redacted, a v2.2 bug found by adding a fixture page with
  a password in it.
- **P4-11**: `http://0177.0.0.1/` reached loopback through the MCP network
  guard, because two IPv4 parsers disagree about a leading zero.
- **P4-13**: the same guard checked the URL an agent asked for, and not what
  the page then fetched.
- **P7-13**: a value recorded in the goldens depended on the directory the
  checkout happened to sit in.
- **P8-0**: the four shapes behind the defects in a backlog of 94 unread review
  comments.
- **P10-18**: an AI code reviewer ended its session without reviewing, and the
  check around it reported success.
- **P10-24**: the weekly Windows run was green for six weeks and never ran a
  capture.

## Verdict summary

| # | Assumption under test | Result | Consequence |
|---|---|---|---|
| S1 | Playwright Python emits tagged PDFs with outlines | **CONFIRMED** — params are exactly `tagged=True, outline=True` (playwright 1.61); output has `StructTreeRoot` + `MarkInfo` + heading-derived outline; control PDF has neither | Phase 1.1 de-risked; spec updated with exact args |
| S2 | pypdf can embed associated files with `AFRelationship` | **CONFIRMED with glue needed** — `EmbeddedFile._create_new` + `associated_file_relationship`/`subtype`/`description` work and persist (values must be `NameObject`/`TextStringObject`); tags/outline survive the rewrite. **Gap:** pypdf does not populate the document-level catalog `/AF` array — ~5 lines of custom glue required (constant exists: `CatalogDictionary.AF`) | ADR-0007 amended; glue added to render/post budget |
| S3 | docling-slim gives the HTML backend without torch at reasonable weight | **CONFIRMED with two caveats** — no torch; fixture converts in 0.04 s; markdown/JSON/table→dataframe/`self_ref`s all work; `PictureDescriptionData` exists for OCR annotations. Caveats: (a) real venv weight ≈ **197 MB** (not the advertised ~50 MB base); (b) top-level `DocumentConverter` eagerly imports the PDF backend (`pypdfium2`) and **crashes on slim installs** — the bridge must use `HTMLDocumentBackend` directly (which is the better design anyway) | 03-components corrected; bridge design mandated in 02-architecture |
| S4 | HybridChunker works offline (Phase 2.3 decision gate) | **REFUTED** — default HybridChunker downloads a HuggingFace tokenizer (fails under `HF_HUB_OFFLINE=1`) and `docling-core[chunking]` adds ≈ 140 MB (transformers, tree-sitter). **HierarchicalChunker** (no extra, no tokenizer) works offline and yields chunks with `headings` + `doc_items` provenance. **chonkie** (RecursiveChunker, default `tokenizer="character"`) works offline for token-budget splitting | **Gate resolved early:** default = HierarchicalChunker + chonkie size-splitting; HybridChunker demoted to optional extra. ADR-0008 updated |
| S5 | nh3 / Trafilatura / MarkItDown behave as planned | **CONFIRMED** — nh3 strips `<script>`, event handlers, `javascript:` URLs, keeps `img`/`alt`; Trafilatura extracts title + main content from the fixture; MarkItDown converts fixture CSV→markdown table and JSON→fenced block | Phase 3 de-risked |
| S6 | The protected-path composition (img2pdf → OCRmyPDF) actually works | **CONFIRMED** — two pixel-only PNG pages → img2pdf (lossless, 59 KB) → `ocrmypdf.ocr(sidecar=…, output_type="pdfa")` in 2.1 s → both OCR sentinels found via pypdf text extraction on the correct pages; sidecar contains both; PDF/A metadata present | Phase 1.2 de-risked end-to-end |
| S7 | ADR-0010 open question: does PDF/A conversion strip Chromium's tags? | **RISK CONFIRMED** — running the tagged web PDF through `ocrmypdf(skip_text=True, output_type="pdfa")` **removed `StructTreeRoot` and `MarkInfo`** (outline survived); OCRmyPDF itself warns it cannot rebuild structural markup. Text extraction also degraded on the converted file | **Decision:** web-path `--pdfa` will NOT ship in v3. Protected path only (image pages have no tags to lose). ADR-0010 finalized |

### S8 — Appendix tag survival through pypdf merge (added after red-team review)

**REFUTED:** appending the Chromium-tagged appendix to the image PDF with
`PdfWriter.append` drops the appendix's `StructTreeRoot`/`MarkInfo` (verified
both merge directions; text stays extractable, bookmarks unaffected). The
"appendix is tagged text" claim was removed from 02 and Phase 1 acceptance —
the appendix is *authored* from tagged HTML for quality, but the merged PDF is
not a tagged PDF.

### S9 — v2 CLI surface introspection (added after red-team review)

`build_parser()` exposes **39 options**; the spec's original hand-written
table listed a nonexistent flag (`--custom-css`; real flag is `--css`) and
omitted 14 real ones. The table in 04 §1.1 is now generated from the parser
and CI enforces completeness. Bonus finding: v2 already emits tagged+outline
PDFs by default (`--no-tagged-pdf`/`--no-outline` opt-outs exist), so Phase
1.1 verifies rather than introduces.

## Environment findings (feed `webshot doctor`)

- Tesseract 5.5.1 ✓ · Ghostscript **10.06.0 — flagged by OCRmyPDF as having
  JPEG-corrupting bugs; recommend ≥ 10.7** → doctor must check the version, not
  just presence.
- Java absent → veraPDF only viable via Docker locally; Docker binary present
  but daemon not running at spike time → doctor should report daemon state.
  The veraPDF gate remains CI-first by design.
- Project venv already at pypdf 6.14.2 / playwright 1.61 — no upgrades needed
  for Phase 1.

## Corrections applied to the plan as a result

1. 03-components: docling-slim real-weight note + `DocumentConverter` import
   trap; chunking decision recorded.
2. ADR-0008: chunker gate resolved (HierarchicalChunker + chonkie; chonkie
   added as a dependency, HybridChunker optional).
3. ADR-0010: web-path PDF/A rejected with evidence; §Open questions #3 closed.
4. ADR-0007: catalog `/AF` glue documented as required custom code.
5. 04-spec: `doctor` gains Ghostscript version and Docker-daemon checks; exact
   `page.pdf` argument names recorded.
6. 05-implementation-plan: open questions #2 and #3 marked resolved.
7. (post red-team) 02/05: LOC accounting reconciled with 02 canonical; 04:
   flag table regenerated from the parser, exit-code migration note, edge-case
   section added; ADR-0007 rationale rewritten (pikepdf #463 is resolved;
   pypdf chosen because v2 already proves it); protected-path PDF/A re-specced
   as A-3 with a final-composed veraPDF gate; tesseract stays subprocess
   (pytesseract was never actually a dependency).

## Residual unverified assumptions (accepted, with owners)

- ~~veraPDF actual pass/fail profile on our PDFs~~ **RESOLVED in Phase 1
  (P1-3, P1-4):** the final composed `--pdfa` deliverable is PDF/A-3B
  conformant, 146 rules passed, after two embedded-file defects were fixed.
- docling HTML backend fidelity on *hostile* real-world DOMs (fixture corpus
  expansion in Phase 0/2 covers this; parity harness is the backstop).
- MCP SDK v2 server ergonomics (Phase 4; SDK is official and MIT — shape risk
  only).
- OCR quality on non-Latin scripts (roadmap item; engine abstraction keeps
  options open).


---

## Phase 0 implementation corrections (2026-08-19)

Phase 0 executed the plan against the real code. Five places where reality
differed from the written plan, and what was done about each. The pattern is
the same one S9 found: a document that describes code is only true if
something checks it.

### P0-1 — The module map had no home for the orchestrator

02's module table lists every stage but not the code that *sequences* them,
and `convert_url_to_pdf` is 300 lines of exactly that. Three further modules
that Phase 0 needs were also absent: `doctor`, the v2 bundle builder that
Phases 1–3 hollow out, and the version constant that three modules were
duplicating.

**Applied:** 02's module map gains `pipeline.py`, `doctor.py`,
`bundle/build.py`, and `version.py`, with budgets, and its canonical total
rises by 505 to ≈ 3,525 (05's rollup follows, as that table requires). This is
an accounting correction, not new work: the code existed in the monolith and
had no row. The G1 claim — *which* lines remain — is unaffected, since none of
these modules implement OCR layering, extraction, chunking, format parsing, or
sanitization.

### P0-2 — A golden yardstick needs two profiles, not one

06's raster policy assumes pixel bytes are the only environment-dependent
artifact. They are not: OCR text is a function of the pixels *and* the
Tesseract build, and it flows into `content.json`, `content.md`,
`content.txt`, `chunks.jsonl`, the PDF text layer, and every character count
derived from them. A single byte-exact policy would have made the golden CI
job fail on any runner that is not the maintainer's laptop.

**Applied:** the harness compares on one of two profiles and says which —
`strict` (everything, including recognized words) when the environment matches
`tests/golden/environment.json`, `portable` (OCR text, OCR-derived counts and
the browser build masked) otherwise. On both, OCR is proven by per-case
**sentinels**: strings that exist only inside a canvas or a page image and can
therefore only reach a bundle through recognition. 06 updated.

### P0-3 — The protected-viewer fixture 06 referred to did not exist

06's fixture corpus lists "a simulated protected-viewer document (exists)".
`tests/fixtures/` contained seven files, none of them a viewer document, so
the protected path — the highest-risk code in the repository — had no
end-to-end coverage at all.

**Applied:** `tests/fixtures/viewer/page-{001,002}.png`, two page images
written for this repository, drive a golden case that exercises OCR, the
searchable PDF, the transcript appendix, the associated-file embeds, the
page-count invariant, and the bundle. This is the *assembly* half; the DOM
fixtures for the *capture* half remain Phase 1.7's job.

### P0-4 — WebShot has no license of its own

ADR-0006 governs what WebShot may depend on and is silent about what WebShot
*is*. The repository has never contained a LICENSE file, so `pyproject.toml`
declares no license and the license gate reports the package itself as
UNKNOWN.

**Applied:** ADR-0006 gains a scope note; 05's open questions gain the
decision, which blocks publication (Phase 5) and nothing before it.

### P0-5 — Two CI jobs are better run as tests

06 lists the schema check and the CLI-contract check as separate CI jobs. Both
are pure functions of the repository contents with no environment needs, so
they run inside pytest instead — which also makes them available locally,
where the drift they catch is actually introduced.

**Applied:** `tests/test_contracts.py` and `tests/test_cli_contract.py`; 06's
CI table notes where they live.

### P0-6 — The golden job runs on the platform the goldens were recorded on

06 puts the golden corpus job on Linux. The goldens record Chromium's own text
layout — page count, page breaks, per-page extracted text — and layout depends
on which fonts the platform has. A Linux runner would therefore disagree with a
macOS recording for a reason that is not a regression, and the blocking job
would be red from its first run.

**Applied:** the corpus job runs on `macos-latest`, the recording platform,
where the only expected difference is the Tesseract build — which the portable
profile already handles. Recording a second, Linux baseline (goldens per
environment) is the way to get Linux coverage back, and needs someone able to
record on Linux; until then the tests matrix still covers Linux for everything
that is not layout-dependent.

### Environment at the end of Phase 0

Playwright 1.62.0 / Chromium 151, Tesseract 5.5.3, Ghostscript 10.07.1 (the
upgrade the spike recommended is done), pypdf 6.16.1, CPython 3.12.14 pinned
by `.python-version` and `uv.lock`. `webshot doctor` reports all of it.

---

## Phase 1 implementation corrections (2026-08-19)

Phase 1 replaced the protected path's hand-built OCR sandwich with img2pdf +
OCRmyPDF, moved the transcript appendix onto the RENDER stage, and put a real
veraPDF verdict behind `--pdfa`. Eight places where reality differed from the
plan, and what was done about each.

### P1-1 — OCRmyPDF 17.x does not support Python 3.10

04's non-functional table required Python ≥ 3.10 and the CI matrix tested it.
**Every** release in the OCRmyPDF 17 major declares `requires-python >= 3.11`
(checked against PyPI metadata for 17.0.1 through 17.10.0), and 03 pins the 17.x
major deliberately.

The alternative was a marker-gated dependency
(`ocrmypdf; python_version >= "3.11"`), which would have left `--protected-viewer`
— a core capture path, not an extra — silently broken on a Python the project
claims to support. A tool whose headline feature is missing on a supported
interpreter is worse than a tool with a higher floor.

**Applied:** `requires-python = ">=3.11"`, the 3.10 classifier dropped, ruff
`target-version` and mypy `python_version` raised, the CI matrix moved to
{3.11, 3.12, 3.13}, and 04 §4 / 01 / README updated. Python 3.10 reaches
end-of-life in October 2026.

### P1-2 — OCRmyPDF and img2pdf are core, not the `pdfa` extra

Phase 0's `pyproject.toml` stub filed both under `pdfa = []` with the comment
"Phase 1.5 — OCRmyPDF + img2pdf protected-path PDF/A". But the protected path
uses OCRmyPDF for the *invisible text layer*, which every protected capture
gets; PDF/A is one argument to the same call (`output_type='pdfa-3'` instead of
`'pdf'`). Behind an extra, `--protected-viewer` would fail on a default install.

**Applied:** both are direct runtime dependencies. The `pdfa` extra is retained
as an empty alias so `webshot[pdfa]` keeps resolving, with a comment saying why.

### P1-3 — pypdf's associated files are not PDF/A-3 valid as written

S2 recorded that pypdf does not write the catalog `/AF` array and costed the
glue at ~5 lines. Running veraPDF over the composed file found two further
defects in the same area, both of which v2 also had:

- **`/UF` is missing.** ISO 19005-3 clause 6.8 requires a file specification to
  carry both `/F` and `/UF`. `add_attachment` writes only `/F`.
- **The MIME `/Subtype` was double-escaped.** v2 built the name as
  `NameObject("/application#2Fjson")`, pre-escaping the slash that pypdf then
  escapes again on write; veraPDF reads back the literal
  `application#2Fjson`, which does not match `^[-\w+\.]+/[-\w+\.]+$`. The fix is
  to hand pypdf the unescaped `NameObject("/application/json")` and let it do the
  encoding.

Together these were 8 failed checks across 2 rules — the only thing standing
between the composed file and conformance.

**Applied:** `_embed_associated_files` sets `alternative_name` (`/UF`) and the
unescaped subtype alongside the `/AF` array; ADR-0007 amended; a unit test
asserts all three on a real composed file.

### P1-4 — the composed `--pdfa` file IS conformant; the fallback was not needed

02 pre-authorized a fallback ("if composition breaks conformance, the `--pdfa`
variant ships without appendix/embeds"). With P1-3 fixed it does not:

```
composed 5 pages (2 source + 3 appendix), 5 embedded files, declared PDF/A-3B
validator: verapdf · flavour: 3b
PASS: the final composed file is 3b conformant (146 rules passed)
```

One construction detail is load-bearing and worth recording, because getting it
wrong fails silently: the composition **clones the writer from the OCRmyPDF
output** (`PdfWriter(clone_from=…)`) and appends the appendix to it. Building a
fresh `PdfWriter` and appending both documents — the obvious way — produces a
file that looks identical and is not PDF/A at all, because the catalog's XMP
identification and `/OutputIntents` belong to the source catalog and are not
carried by `append`. Nothing warns; only veraPDF notices.

**Applied:** 02's fallback stays documented as the contingency it is, marked not
taken. ADR-0010 records the verdict.

### P1-5 — Java is present but veraPDF was not; the local story is degradation

The spike recorded "Java absent". `/usr/bin/java` does exist on this machine —
it is macOS's stub, which reports "Unable to locate a Java Runtime" — so the
conclusion was right for the wrong reason. veraPDF was installed for this phase
(`brew install verapdf`, which brings its own JDK) to get the baseline above.
Docker's daemon remains typically stopped locally.

**Applied:** `--validate-pdf` degrades to a warning when neither a `verapdf`
binary nor a *running* Docker daemon is found — `docker` on `PATH` is not
enough, and `webshot doctor` now reports the daemon rather than the binary. The
CI job (`tools/verapdf/check.py`, non-blocking) is where the gate has teeth,
because a runner always has both.

### P1-6 — the viewer DOM fixtures are authored, not exported

1.7 asked for "sanitized/redacted DOM snapshots of the real SharePoint viewer".
Producing one needs an authenticated tenant session, which this environment does
not have — and a redaction mistake in a capture tool's own repository is exactly
the failure 04 §6 exists to prevent.

**Applied:** `tests/fixtures/viewer/dom/` reproduces the accessibility-tree
contract instead — the scrollable container labelled with the file name, the
`Page N.` page elements, the `Page N of M` counter, and the toolbar controls the
landscape path drives by accessible name. To make the fixtures test something
rather than themselves, every selector moved out of the JavaScript into named
constants in `protected/viewer.py`, and the tests import *those*.

The limit is stated in the fixtures' README and repeated here: they catch drift
in WebShot, not drift in Microsoft's viewer. Only a run against a live tenant
can tell you the real DOM changed, and that stays a maintainer task —
re-capture, re-redact, replace the fixtures, update `SUPPORTED_VIEWERS`.

### P1-7 — the protected path shrank by a third, not by half

05 predicted `protected_viewer.py` ≈ 1,163 → ≈ 500 LOC, and 02 budgeted
`protected/assemble.py` at 120. Measured in code lines (blank lines, comments
and docstrings excluded), against the v2.2 equivalent:

| Module | before | after |
|---|---:|---:|
| `protected/assemble.py` | 834 | 385 |
| `protected/viewer.py` | 244 | 263 |
| `protected/appendix.py` | — | 75 |
| **protected package total** | **1,078** | **723** |

The bespoke drawing code is gone: line wrapping, page breaking, invisible-text
positioning, link rectangles and outline bookkeeping — every one of which was
arithmetic WebShot had no business doing — are now Chromium's, img2pdf's,
OCRmyPDF's and pypdf's. What did *not* disappear is the composition's contract:
the invariant checks, the atomic publication, and the sequencing. 02's 120-line
budget describes only "pack → OCR layer → appendix merge → embeds" and, exactly
as in P0-1, had no row for the code that orders and verifies them.

Two moves also made the module map true rather than aspirational: the
protected-viewer **bundle** writing went to `bundle/build.py` (which 02 already
described as the bundle writer) and the **page OCR pass** to `ocr/tesseract.py`
(which 02 already described as the TSV word-output engine). Neither is new code;
both were living in `assemble.py` because v2's monolith put them there.

**Applied:** 02's module map gains `protected/appendix.py` and
`validate/verapdf.py`, and the `protected/assemble.py` budget is corrected to
250 with the reason. Whole-tree code lines: 3,758 → 4,138, the increase being
net-new capability (the veraPDF bridge, the PDF/A path, the exit-code taxonomy,
the viewer contract) rather than retained problem-solving.

### P1-8 — the golden harness needed two changes, and one of them is a lesson

**Tagging is now asserted, not recorded.** 1.1 asks for evidence that
`page.pdf(tagged=True, outline=True)` does what it claims. Recording
`StructTreeRoot` into the snapshot would have made it a value someone could
re-record away. Each case now *declares* whether its PDF must be tagged and
bookmarked, and `check.py` and `record.py` both verify it against the file — so
the assertion holds on every profile, and `record.py` refuses to record a case
whose PDF is not what the case says it is.

**The PDF's text layer became OCR output, which the portable profile has to
mask.** It used to be drawn from WebShot's own TSV pass, so the existing
fragment-scrubbing covered it. It is now OCRmyPDF's own reading of the same
pixels, which need not agree line-for-line with the TSV pass — meaning the CI
runner's Tesseract build could produce a diff that is not a regression. The
portable profile now blanks OCR-derived page text while keeping the page
markers, so page count and page order are still compared, and the words stay
proven by the sentinels (which are checked before masking, on both profiles).
The appendix's page count is masked for the same reason: Chromium paginates it
from recognized text. The **page-count invariant is asserted in code on every
run**, so masking it in the snapshot loses nothing.

---

## Phase 2 implementation corrections (2026-08-19)

Phase 2 swapped the bespoke extraction for docling behind one bridge, with the
parity harness built and calibrated first. Eight places where reality differed
from the plan, and what was done about each.

### P2-1 — The aria snapshot leaked password values (v2.2 bug, caught by the corpus audit)

The corpus audit added the password-form fixture the fidelity mapping
required, and its first recording failed §6.2 immediately: Playwright's aria
snapshot reads the *live* DOM and reported the password textbox's value into
`accessibility.yaml`. v2.2 always had this hole — the block/clone extraction
redacts as it reads, but nothing scrubbed the snapshot, and no fixture
exercised a password field until now. This is the whole argument for
corpus-audit-before-swap: parity cannot measure what the corpus does not
contain, and neither can a security test.

**Applied:** `_redact_password_values` pulls the values once, scrubs them from
the snapshot (raw and escaped spellings), and never lets them leave the
function. `tests/test_redaction.py` pins §6.2 both ways on the recorded
goldens — value absent everywhere, redacted record present. One documented
exemption: the `source/` copy is the user's own input file preserved
byte-for-byte (02 §Bundle table), so a password that was already *in* the
input is not a capture leak.

### P2-2 — "HierarchicalChunker needs no extra" held only by accident

S4's spike venv also installed HybridChunker's stack to refute it, so
`huggingface_hub` was importable when HierarchicalChunker was pronounced
extra-free. On a clean slim install, docling-core's chunker package cannot
even be imported: its `__init__` eagerly imports `tree_sitter` and
`huggingface_hub` for chunkers WebShot never instantiates, on every
docling-core version back to 2.70.

**Applied:** both join the dependency tree as small, permissive,
imports-only additions (nothing downloads — the offline guarantee is
unchanged), commented as such in `pyproject.toml`, with `webshot doctor`'s
new Extraction check reporting the whole stack. The alternative — pinning a
docling-core old enough to predate the imports — does not exist.

### P2-3 — Four backend behaviors the bridge corrects (and tests pin)

Adoption against the pinned versions found four behaviors the plan's
"sanitized HTML → DoclingDocument" arrow glossed over:

1. **The `<title>` tag becomes a second title item.** The bridge feeds the
   backend a title-less copy; the page's own headings are the record, and a
   page with no `<h1>` must not gain one from metadata.
2. **`<table><caption>` text is dropped.** The bridge re-attaches captions as
   caption items linked from their tables — nothing vanishes silently.
3. **Everything before the first heading lands on the furniture layer**,
   which serializers and the chunker skip. Right for header/nav/footer
   chrome; wrong for a lede or an adapter page's descriptor paragraph, both
   of which v2 recorded. Furniture text that did not come from
   header/nav/footer/aside is reflowed to the body layer.
4. **Doctags crashes on docling's own labels**: the backend detects
   `CodeLanguageLabel.JSON`, and the doctags token set cannot spell it. Such
   items are downgraded to `unknown` for that export only; `content.json`
   keeps the detected language.

### P2-4 — Pictures carry no image URIs; the asset mapping is positional

02 said "harvested visuals map to PictureItems" as though a join key existed.
The backend records no `ImageRef` for ordinary `src` values, so the bridge
matches pictures to assets by document order. The count check guards the
mapping — the backend only ever *skips or merges* elements the snapshot
parser counts, so equal counts prove the sets match — and a mismatch skips
the attachment with a manifest warning rather than guessing. (The first
implementation raised exit 8 on mismatch; the review's probes showed docling
legitimately skips an `<img>` inside `<pre>` and flattens nested tables, so a
hard failure would kill captures of ordinary pages — P2-9.) Risk register R16.

### P2-5 — The chunk `locator` is a content.json anchor, not a DOM anchor

02's chunk schema promised a "DOM-anchor locator usable against
content.json/content.html". A CSS path is not derivable from the docling
model, so the locator is the chunk's leading DocItem `self_ref` — a stable
JSON-Pointer-style anchor into `content.json`, which is also what `doc_items`
already are. 02 amended; the migration guide says to anchor into
content.json.

### P2-6 — docling-core deprecates `annotations` with no replacement for text

2.92 warns that `PictureItem.annotations` is deprecated in favour of `meta`,
but `PictureMeta` has no field for descriptive text and the ecosystem
serializers still read annotations (the markdown export prints them — which
is how content.md keeps OCR text with no hand-written serializer). The bridge
uses annotations deliberately and suppresses the warning at the call site;
revisit when `meta` grows a description field.

### P2-7 — v2's own text basis was contaminated by injected OCR

The first full parity run failed markdown-report at 71.9% text coverage — and
the missing tokens were Tesseract's. v2 injected OCR spans into the live DOM
(for the PDF's text layer), and any block whose element contained a visual
recorded that span inside its own innerText, so the frozen baselines carry
recognition output inside paragraph text. The parity basis excludes OCR by
definition; the v2 reader now scrubs the injection pattern. The mutation
self-test then caught the scrub's blind spot in `drop-longest-paragraph`
(the longest *raw* paragraph was the contaminated one, which scrubs to
nothing) — the judge caught a bug in its own calibration, which is the point
of having one.

### P2-8 — One docling bridge, not three; the block JS stays until v3.1

02's module map marked `extract/exports.py` and `extract/chunks.py` as
docling bridges. CONTRIBUTING rule 1 says one module per component, so
docling imports live only in `extract/docling_bridge.py`; exports.py writes
files from plain strings and grids, and chunks.py is *chonkie's* bridge
(strings in, strings out). The in-browser block extraction also survives the
phase — not as extraction, but as the capture-side recorder for the fidelity
records docling does not model (form fields, embedded media) and as
`--legacy-bundle`'s feeder; the v2 serializers live verbatim in
`extract/legacy.py` until both die at v3.1. 02's module map updated to match.

### P2-9 — The review's probes corrected the phase's own guard, and restored dropped metadata

The `/code-review max` pass over the finished phase confirmed two defects with
executable probes and fixed them before the PR:

1. **The alignment guard was too eager to fail.** Its first form raised exit 8
   whenever docling's picture/table count differed from the snapshot parser's.
   Probing the pinned backend showed ordinary pages trigger that legitimately:
   an `<img>` inside `<pre>` produces no PictureItem, and a table nested in a
   table cell flattens to one TableItem. Failing the capture over an
   attachment contradicts the failure philosophy (docs/02: degrade loudly,
   never silently, never disproportionately) — a mismatch now skips the
   attachment with a manifest warning, nothing is guessed, and the complete
   OCR records remain in assets.json. Both shapes are pinned as bridge
   contract tests.
2. **v2's page metadata had been dropped entirely.** `content.json.metadata`
   (description, author, keywords, canonicalUrl, publishedTime) had no
   format-3 home, and no parity metric measured it — a G2 regression the
   100%-green parity report could not see. It now lives in the manifest as
   `page_metadata` (same record shape), the parity harness gained a
   `page_metadata` metric with planted-defect calibration on both formats,
   and the addition is a visible parity.toml diff in the strengthening
   direction.


---

## Phase 3 implementation corrections (2026-08-20)

Phase 3 routed every non-Markdown local format through MarkItDown, put
`--auto-selector` and manifest metadata on Trafilatura, finished the move off
bleach, and formalized the OCR engine seam. Seven places where reality
differed from the plan, and what was done about each.

### P3-1 — MarkItDown loses tables two ways, and both are silent

Adoption against the pinned 0.1.7 found two behaviors the plan's "route
through MarkItDown" arrow assumed away, and neither raises anything:

1. **There is no TSV converter.** `CsvConverter` accepts `.csv` and
   `text/csv` only and reads with a comma delimiter; a `.tsv` file falls
   through to the plain-text converter, so a table WebShot used to render
   arrives as a wall of tab characters. The bridge re-delimits with the
   standard library's `csv` module — a transcode, not a parse — so the CSV
   converter applies.
2. **Its Markdown table writer does not escape the delimiter it emits.**
   Cells are joined with `" | "` verbatim, so a cell containing `|` splits
   its row into extra columns and a cell containing a newline splits the row
   in two. The bridge escapes both on the way in (`\|` and `<br>`), which
   Markdown and HTML turn back into exactly the original cell.

Both are pinned by `tests/test_source_adapters.py`. A third behavior is
carried rather than corrected: a CSV *inside* a `.zip` is converted by
MarkItDown's own recursion, below the bridge, so it is subject to defect 2.
Fixing that would mean re-implementing the zip converter, which is the code
this phase exists to delete.

### P3-2 — MarkItDown puts an ONNX runtime in the default install

`markitdown` requires `magika~=0.6`, Google's learned file-type detector,
which requires `onnxruntime` and `numpy`. Nothing in WebShot asks for it:
docs/03 §9 keeps MIME sniffing custom, and the bridge tells MarkItDown what
each file is rather than letting it guess. It is nonetheless a hard
requirement of the base package, so `pip install webshot` now carries an ONNX
runtime it never invokes.

**Applied:** recorded here and in 03's license summary rather than worked
around — vendoring or patching MarkItDown to drop magika would forfeit the
replaceability the bridge exists to protect. `tests/test_footprint.py` still
holds the line that matters (spec §4: no torch, no GPU stack), and the new
`test_extras_do_not_leak_into_the_default_install` keeps the two Phase 3
extras out of it. The upside, unplanned: `webshot[ocr-rapid]` needs no second
runtime, because that one is already there.

### P3-3 — Trafilatura invents a publication date, by default

`extract_metadata` defaults to `extensive=True`, which lets htmldate *infer* a
date when the page declares none. The corpus fixture, which contains no date
anywhere, was assigned `2026-01-01`. Two things are wrong with recording that:
a capture is an act of record and may not invent provenance, and an inferred
value drifts with the calendar, so the goldens would have started failing on
their own.

**Applied:** `extensive=False`. Declared dates are still read (an
`article:published_time` fixture proves it); an undeclared one stays empty.
`tests/test_discovery.py` pins both directions.

### P3-4 — RapidOCR ships its models, and narrates every one of them

Two findings from adopting it behind the extra:

- **Offline, genuinely.** Unlike most OCR packages, `rapidocr` 3.9.2 ships its
  three ONNX models inside the wheel (they are listed with hashes in the
  distribution's `RECORD`), so recognition needs no download. That is what
  makes the acceptance test — a real recognition against a real fixture —
  legal under the no-network rule for tests.
- **It logs at INFO with its own stderr handler**, announcing each model as it
  loads. Setting the logger's level does not hold: every one of its modules
  constructs `Logger("RapidOCR")` on first use and that constructor calls
  `setLevel` again, so the next component to start puts the level back. A
  filter on the logger does hold, because nothing upstream removes filters.

### P3-5 — The license gate could not read a choice of licenses

Trafilatura reaches `tld`, which is offered as
`MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later`. The gate matched denied tokens
against the whole string, saw `GPL-2.0-only`, and refused a package ADR-0006
permits: an SPDX `OR` is the licensor offering terms, and WebShot may simply
take the LGPL (or MPL) ones the policy names.

**Applied:** the gate splits on the SPDX disjunction operator and denies a
package only when *every* alternative is refused; `AND` is deliberately not
split, so one refused conjunct still refuses the whole. Two guards keep the
change from becoming a hole: only the uppercase `OR` is an operator (license
*names* contain the lowercase word), and a value that is long or multi-line is
never split at all — several packages ship their full license *text* as
metadata, and nearly every license body contains "EXPRESS OR IMPLIED", which
would otherwise split a GPL body into a fragment that names no license and
reads as permitted. `tests/test_license_gate.py` pins that case explicitly.
The gate also now prints which acceptable terms exist for each dual-licensed
package, so the election is visible rather than implied.

### P3-6 — "The adapter is the bridge" was an accounting error

02's module map called `acquire/adapters.py` the MarkItDown bridge and gave it
80 LOC. Most of what that module holds is not glue: the format table, the
100 MB guard, the XML defusing, archive safety, the wrapper-page template, the
Markdown rendering, and the nh3 allowlist are all WebShot's own policy, and
docs/03 §9 says so explicitly.

**Applied:** `acquire/markitdown_bridge.py` is the bridge (≈ 90 LOC, the only
markitdown importer, satisfying CONTRIBUTING rule 1 cleanly); `adapters.py`
stays custom and is rebudgeted 80 → 360, which is what it measures. The phase
deleted the bespoke parsers it was budgeted to delete — CSV, JSON and JSONL
rendering, ≈ 75 lines — and ≈ 195 arrived in their place: the format-routing
table, the archive-safety and XML guards spec §6 requires, five new input
formats, and the docstrings that record why each remaining piece is not
delegated. Across the phase 02's canonical total rises ≈ 4,085 → ≈ 4,555, and
05's rollup follows, as that table requires. G1 is unaffected — it measures
whether custom code still *implements format parsing*, and none does — but the
increase is real work rather than accounting, which the two earlier revisions
were, so both tables say which kind this is.

### P3-7 — Task 3.5 existed in three documents and in no plan row

02's module map budgets `ocr/engine.py` and `ocr/rapid.py`, 03 §5 names
RapidOCR as the optional engine, and 04 §1.1 promises
`--ocr-engine tesseract|rapid` with `--ocr-psm` warned and ignored. Phase 3's
task list had rows 3.1–3.4 and no row for any of it — the same unowned-work
gap P0-1 found for the orchestrator.

**Applied:** 05 gains task 3.5. Two scope decisions came with it:

- **The seam is asset recognition, not page recognition.** The protected
  path's searchable PDF is built by OCRmyPDF (which is Tesseract) and its
  `ocr/page-NNN.tsv` records are Tesseract's word-box format; honouring a
  second engine there would mean fabricating TSV or publishing a text layer
  from one engine and coordinates from another. `--ocr-engine rapid` is
  therefore warned and ignored on that path — the treatment `--legacy-bundle`
  already gets there — rather than refused.
- **`--ocr-psm` is reported as ignored only when it was asked for.** argparse
  cannot otherwise distinguish a passed `--ocr-psm 11` from its own default,
  and a warning that fires when nothing was requested is noise while one that
  stays silent when something was is a lie. The default is a marked `int`
  subclass, which argparse replaces with a plain `int` for anything passed.

### P3-8 — Two allowlists are two policies, not two importers

Phase 2 declared `capture/snapshot.py` "nh3's one module". Phase 3 needs a
second allowlist — a rendered Markdown document and a captured DOM legitimately
differ in what may survive — and the first implementation simply called
`nh3.clean` again from `acquire/adapters.py`. That is a CONTRIBUTING rule 1
violation with a visible cost, found by the phase's own cleanup review: spec
§6.5's URL-scheme set was written out twice, verbatim, in two files. A MUST
with two definitions is a MUST that can drift by half.

**Applied:** `webshot/sanitize.py` owns the one `nh3.clean` call and takes a
`Policy` — tags, attributes, generic prefixes — as data. The two allowlists sit
in the stages that own them and are passed in; the scheme rule is defined once.
`webshot doctor`'s importability probe goes through the owner too, so the rule
holds under grep with no exceptions.

### Environment at the end of Phase 3

markitdown 0.1.7 and trafilatura 2.2.0 — the exact versions spike S5 verified,
with no drift to record — plus rapidocr 3.9.2 and onnxruntime 1.29.0 behind
`[ocr-rapid]`, and mammoth 1.11.0 / python-pptx 1.0.2 / openpyxl 3.1.5 behind
`[office]`. bleach is gone from `pyproject.toml`, from `uv.lock`, and from the
tree. The license gate passes with every extra installed: 131 packages, none
denied.

---

## Phase 4 implementation corrections (2026-08-20)

Phase 4 is the interfaces phase — MCP server, docs site, QA report v2,
configuration — and its corrections are of two kinds: facts about the adopted
SDK that the plan had recorded from a distance, and specification items that
were written down and then owned by nobody.

### P4-1 — The MCP SDK's v2 line renamed the server class and grew a stack

**Recorded fact (docs/03, ADR-0009):** "the official Python SDK is MIT and
stable (v2 line)".

**What is actually installed:** `mcp` **2.0.0**, MIT, with `mcp-types` 2.0.0 as
a companion distribution. Two things the plan did not say:

- The ergonomic server class is **`MCPServer`**, imported from
  `mcp.server.mcpserver`. It is what the v1 line called `FastMCP`; the old name
  is gone rather than aliased, so any v1 snippet is a rewrite, not a rename.
- The SDK depends on `starlette`, `uvicorn`, `httpx2`, `sse-starlette`,
  `python-multipart`, `pyjwt[crypto]`, and `opentelemetry-api` — a whole HTTP
  server stack, for transports v3.0 does not ship. That is why `mcp` is an
  **extra** rather than a core dependency: `webshot doctor` and every capture
  must keep working on an install that never asked for an MCP server. The
  license gate passes with it installed (156 packages, none denied).

Two behaviors are worth pinning because the guardrails depend on them:

1. **Unknown tool arguments are ignored, not rejected.** An argument the tool's
   signature does not declare is dropped during validation and the call
   proceeds. For a forgiving protocol that is the right default; for a surface
   whose security claim is "no credential-bearing parameters" it is not, because
   an agent sending `storage_state=…` would be told nothing and would believe it
   had authenticated. WebShot therefore installs a `ServerMiddleware` that reads
   each tool's declared parameters back off its own registered schema and
   refuses a call carrying anything else.
2. **A middleware that raises surfaces differently per transport.** Raising
   `MCPError` reaches an in-process client as an exception inside an anyio
   `ExceptionGroup` and a stdio client as a JSON-RPC error. Returning a
   `CallToolResult(is_error=True)` reaches both identically, and matches how
   every other refusal in this server arrives — as text the model reads. The
   middleware returns rather than raises.

`Context.report_progress` is a no-op when the caller sent no progress token, so
progress reporting needs no capability check.

### P4-2 — One `mcp_server.py` could not hold the guardrails

02's module map budgeted `webshot/mcp_server.py` at 250 LOC, "official `mcp`
SDK; tools per 04-spec.md". The tool wiring is about that size. The guardrails
are not: six refusals, each with traversal, network, credential, and readiness
rules, plus the staging that makes the redirect rule a refusal to publish.

Split into a package: `mcp_server/policy.py` (the refusals, importing nothing
upstream), `mcp_server/server.py` (the SDK bridge — the only file that imports
`mcp`), `mcp_server/results.py` (the published result models), and
`mcp_server/cli.py` (the subcommand) — joined in P4-9 by `service.py`, once it
became clear how much of the "bridge" never touched the SDK. ≈ 1,515 LOC across
the five, six times the budget, and the honest reading is that the budget priced
the tools and not the boundary.

The split is not bookkeeping. `policy.py` being protocol-free is what lets
`tests/test_mcp_policy.py` prove every refusal as a plain function call, and
`server.py` being the sole importer is what keeps CONTRIBUTING's bridge rule
true. 02's module map is updated; the LOC rollup moves by +1,265 for this row alone.

### P4-3 — `--config` was specified in three places and owned by no plan row

04 §1.1 lists `--config FILE` with a precedence rule. 02's module map gives
`config.py` "pydantic options model; TOML config file; env (`WEBSHOT_*`)".
`config.py`'s own docstring says a later phase will do it. No task in 05 owned
it — the same gap P0-1 found for the orchestrator and P3-7 for the OCR seam.

Recorded as **plan amendment 4.4**, sequenced *before* 4.1 because `[mcp]`
settings live in the config file.

Two implementation facts came out of building it:

- **Precedence needs to know which flags were named**, and a parsed argparse
  namespace cannot say: `--scale 0.9` and argparse's own default 0.9 are
  indistinguishable in the result. Resolution therefore re-parses the same argv
  against a parser whose defaults are all `None`; anything still `None` was never
  on the command line. Without this, a config file would silently win an
  argument the user thought they had made.
- **Discovery is a security decision, so §6 gained a tenth requirement.** WebShot
  is routinely run against a directory of material it did not produce. Reading
  `./webshot.toml` would let that directory choose the user agent, the injected
  stylesheet, or — once 4.1 landed — the MCP server's filesystem roots. The file
  is loaded only from `--config` or `WEBSHOT_CONFIG`, and the refusal has its own
  test.

### P4-4 — §6.8 was one sentence; the MCP threat model needs six rules

04 §6.8 said "MCP: no credential-bearing parameters; filesystem access confined
to configured roots", and §1.3 added a `file://` clause. That is two of the six
rules an agent-driven capture surface actually needs, and it does not say why
the surface differs from the CLI at all.

The difference is the whole point: a person typing `webshot https://…` chose the
URL; an agent calling `capture` may be acting on text a page WebShot captured ten
seconds earlier. §6.8 is now enumerated a–f (roots, output root, network policy,
credentials, profile readiness, serialization), §5.9 gains the operational
consequences, and §1.3 states the rule a *client* must apply: returned page
content is untrusted data, never instructions.

Three of the six needed a design decision rather than a check:

- **The network rule has to run twice.** Checking the source URL is not enough,
  because a public URL can redirect to `http://169.254.169.254/`. Re-checking
  `final_url` after the capture only means something if nothing has been
  published yet, so an MCP capture is staged inside the output root and moved
  into place only after the check passes. This is also what makes the
  "output root only" rule structural: the destination is computed, never passed.
- **The refusal set is deliberately wider than the enumeration.** §6.8's list
  (RFC1918, 127/8, 169.254/16, ::1, fd00::/8) omits carrier-grade NAT,
  IPv4-mapped IPv6, multicast, and the reserved blocks. A default that errs
  toward refusal can be opened by an operator who knows their network; one that
  errs toward reachability cannot be closed after the fact.
- **What the rule cannot cover is stated rather than implied.** WebShot resolves
  the name, then the browser resolves it again; a name whose answer changes in
  between (DNS rebinding) is outside this check. Documented in §6.8 and in the
  MCP guide, because a guardrail whose limits are unstated reads as a stronger
  claim than it is.

The CLI is unchanged by all of it. `webshot http://localhost:3000` still works,
because a person typed it.

### P4-5 — QA report v2 could not be additive, and one gate was missing

04 §2.3 lists what the report must contain but not what the keys are called, and
the v2.2 report has two shapes that cannot survive the additions:

- `ai_summary` carried different keys on the web and protected paths
  (`items`/`tables`/`links` vs `pages`), so "counts" could not be a typed field
  while it existed. It is replaced by `counts`, whose absent numbers are `null`
  rather than `0` — the protected path has no links to count, and measuring
  none is not measuring nothing.
- `validation` was *absent* unless `--validate-pdf` ran, which made "was not
  checked" and "was checked and found nothing" identical to a reader. In v2 it is
  always present, with `pdfa: null` when the gate did not run.

Both are breaking changes to the report, which is what the version bump is for;
the migration guide lists them. Two smaller findings:

- **The manifest could not supply the OCR word count**, so both manifests gained
  an additive `content.ocr_words`. Recomputing it in the pipeline would have put
  the same expression in two places and let them disagree.
- **`exit_code` is honest but narrow today.** A run that fails before publication
  writes no report at all, so the field is 0 whenever the file exists. Making
  failures write reports is task 5.1's audit, and claiming otherwise here would
  have been worse than saying so.

### P4-6 — The docs site publishes the design package rather than shadowing it

05 task 4.2 lists a docs site with a quickstart, CLI reference, bundle-format
spec, MCP guide, security model, and migration guide. Four of those six overlap
documents that already exist in `docs/`, and the migration guide *is* one.

`docs_dir` is therefore the existing `docs/` tree: the guide lives in
`docs/guide/`, and 01–11 plus the ADRs are published beside it under a "Design
package" section. Nothing is duplicated, `docs/migration-v2-to-v3.md` and
`docs/11-capture-ethics.md` are linked where they already are, and
`mkdocs build --strict` checks every cross-reference between the two halves —
which immediately found three broken links, one of them in a document that had
been merged four phases earlier.

The CLI reference is **generated** from the argparse parsers by
`tools/docs/cli_reference.py`, with `tests/test_docs.py` failing when the
checked-in page and the parsers drift. S9 is the reason: the hand-maintained
table in this document's own §1.1 once listed a flag that did not exist and
omitted fourteen that did. The MCP guide's tool list is checked against the
registered tools the same way, because those names are a frozen contract.

The Pages deploy workflow exists and is **not enabled**: publication is a
repository setting rather than a code change, and turning it on is a decision
for whoever owns the domain.

### P4-7 — A numbering note

The brief for this phase referred to "§5.8 (MCP operational rules)". §5's list
puts the MCP rules at **item 9**; §5.8 is the `--validate-pdf` edge case. The
rules are at §5.9, and this note exists so the next reader does not go looking
for a section that says something else.

### P4-8 — The MCP server was building a command line to reach its own rules

Found by the Phase 4 cleanup review. `mcp_server` constructed capture options
by calling `build_parser().parse_args(["--", "placeholder"])`, mutating the
resulting namespace, and handing it to `cli.options_from_args`. The synthetic
namespace existed for one reason: eleven of the twelve rules about whether a set
of options makes sense lived in `options_from_args`, and the MCP server had no
other way to reach them.

`config.py` had said this was wrong since Phase 0, in the docstring of the one
rule that *had* moved: *"docs/02-architecture.md makes this model the single
source of truth that the CLI **and** the MCP server construct, so a refusal that
lived in argparse would not apply to the second one."* Only `--pdfa` ever moved.

All twelve now live on `CaptureOptions.__post_init__`, exception types and
messages unchanged, and `options_from_args` is pure translation. Two things
follow, and the second is the reason this is a correction rather than a tidy-up:

- **The option surface became an allowlist.** The old shape built a full CLI
  namespace and then blanked the dangerous fields — `storage_state = None`,
  `interactive_auth = False`, and so on. That is allow-then-subtract on a
  surface whose entire thesis is deny-by-default: a future `--dump-har PATH`
  would have been silently reachable over MCP until someone remembered to add
  it to the blanking list. The server now names the seven options a tool call
  may set, and nothing else is constructible.
- **Precedence stopped being implemented twice.** `SETTING_TO_DEST` became
  `SETTINGS`, carrying both vocabularies — the argparse dest the CLI writes to
  and the `CaptureOptions` field the MCP server passes — so one table serves
  both callers and `tests/test_settings.py` checks each half against its target.

Writing that test immediately caught a real bug in the new code: `invert`
describes the *flag* relationship (`scroll = false` → `--no-scroll`), and the
first version of `capture_overrides` inverted the model field too, so a
config that disabled scrolling would have enabled it over MCP.

### P4-9 — Four more cleanup findings, and what each one was hiding

The same review found four smaller things, each of which turned out to be a
claim the code was not keeping:

**The bridge was 400 lines of things that never touch the SDK.** `server.py`'s
docstring said the tools "could be re-exposed over a different protocol by
rewriting one file"; the truth was the inverse. Staging, publication, and all
five bundle readers are WebShot operations, and they were sitting behind
`pytest.importorskip`, untestable on an install without `webshot[mcp]` — exactly
the property the `policy.py` split was made to buy (P4-2). They moved to
`service.py`, with progress as a plain callback rather than an SDK `Context`,
and `tests/test_mcp_service.py` now exercises them with no SDK present.

**The per-tool error translation was unnecessary, and cost the schemas.** Six
identical `except WebShotError: raise ToolError(...)` blocks became one
decorator, which broke every tool: the SDK derives a tool's name and its
published JSON Schema from the function's signature, and a `(*args, **kwargs)`
wrapper has neither. Reading the SDK settled it — `_handle_call_tool` catches
`Exception` and returns `CallToolResult(is_error=True, text=str(e))`, which is
precisely the refusal shape WebShot wanted. The translation layer was deleted
outright: a `WebShotError` now propagates and the SDK renders it.

**Two counts of one capture could disagree.** `mcp_server.results.CaptureCounts`
and `manifest.ReportCounts` were field-for-field identical and were populated by
two separate builders over the same `ai_summary` dict — one branching on
`source_kind == "protected-viewer"`, the other relying on `.get()` returning
`None` because the protected path's dict happens to lack the key. They agreed by
coincidence. There is now one model and one `report.counts()`, and the MCP
`capture` result calls it. `auth_mode` had the same shape — derived inline in
`pipeline.py` for the manifest and again in `report.py` for the QA report — and
is now a property of `CaptureOptions`.

**Three efficiency findings, one of them material.** `_asset_records` fell back
to parsing `content.json` when `assets.json` was absent; on a protected-viewer
bundle that is the word-level OCR store — measured at 5.9 MB and ~107 ms per
call on a real 49-page capture — and it holds no asset records at all, so the
whole parse was discarded. The manifest, two kilobytes, now says which file to
read. Paged reads seek instead of reading the whole file (paging an N-byte asset
cost N bytes *per page*), `query_chunks` streams its file, and the DNS lookups
in `check_source`/`check_final_url` moved to `asyncio.to_thread`, because
`capture` is the one tool the SDK awaits on the event loop and a slow resolver
would have frozen the whole stdio connection.

Two findings were considered and declined. `--transport` has one choice and an
unreachable guard, but the flag is spec §1.1's and the guard is what stops a
second transport being added without a handler. Turning the derived result
fields (`complete`, `returned`, `ready`) into pydantic `computed_field`s would
remove them from the *validation*-mode JSON Schema, and a contract frozen after
v3.0 should not depend on which mode the SDK happens to generate output schemas
in.

### P4-10 — Four defects the code review found, three of them in new code

Run at `high` over the whole phase diff. All four were reproduced before being
fixed, and each now has a regression test in `tests/test_mcp_service.py`.

**A caller could defeat the `read_asset` cap with a negative `limit`.** The page
size was `min(limit, cap)` with no lower bound, and the paged read passes that to
`file.read()`, where a negative value reads to end of file. `limit=-1` returned a
whole asset in one call against a 5 MB cap that docs/04-spec.md §5.9 makes a
MUST. The mirror case, `limit=0`, returned an empty page whose `next_offset`
equalled its `offset` — a client following the documented paging contract would
have looped forever. Both are now refused rather than clamped: a request for
zero or minus-one bytes is a caller bug, and guessing at it would hide it.

**`list_assets` stat-ed a path the bundle was not entitled to name.** It built
`directory / asset.file` and called `stat()`; `read_asset` routed the same value
through `resolve_in_bundle`. So one reader had guardrail (a) and the other did
not, and a crafted `assets.json` naming `/etc/hosts` made a listing report that
file's real size. This is reachable exactly where `[mcp] roots` are useful — a
root holding bundles WebShot did not write. The record is still listed, with no
size, because hiding it would hide the evidence.

**A config setting and a tool argument for the same option crashed the call.**
Introduced by P4-8's own refactor: the two layers were passed as separate `**`
expansions, so `[capture] mode = "faithful"` plus a call passing `mode` was a
duplicate keyword argument rather than an override. Five of the seven settable
tool parameters were affected. It is a small bug with a general lesson — the
allowlist made *which* options are reachable explicit, and then the merge that
combines the layers was written as if they could not overlap.

Worth recording that the two cap bugs share a shape: both are a caller-supplied
number reaching an API whose out-of-range behaviour is useful (`read(-1)`,
`min()` with no floor). The guardrails were all written against *paths*, *hosts*
and *credentials*, and the numbers went unexamined.

### P4-11 — The boundary matrix found two more, and one was an SSRF bypass

P4-10's lesson was that caller-supplied *numbers* held half the defects, because
every guardrail had been written against paths, hosts, and credentials. Rather
than note that and move on, the phase gained `tests/test_mcp_boundaries.py`: the
boundary space of every MCP tool parameter, asserted as properties instead of
examples. It found two things immediately, and the first is the most serious
defect of the phase.

**`http://0177.0.0.1/` reached loopback through the network guardrail.** Two
IPv4 parsers disagree about a leading zero, and WebShot was consulting only one
of them:

| reading | `0177.0.0.1` | `010.0.0.1` |
|---|---|---|
| `ipaddress.ip_address` | `ValueError` — rejected since 3.9.5, for this exact ambiguity | `ValueError` |
| `socket.getaddrinfo` (macOS) | **177.0.0.1** — public | **10.0.0.1** — private |
| `socket.inet_aton`, WHATWG/Chromium | **127.0.0.1** — loopback | 8.0.0.1 — public |

The policy fell through to `getaddrinfo`, saw a public address, and allowed the
capture — while the browser, following the WHATWG URL parser, would have fetched
loopback. An agent asked to capture `http://0177.0.0.1:8080/admin` would have got
an internal page published. Note the second column: the readings disagree in
*both* directions, so choosing a better single parser is not the fix.

A host string is now classified under **every** reading it has — the strict
literal, the `inet_aton` legacy literal, and the resolver's — and one internal
reading is enough to refuse it. A name with no literal reading at all is still
refused when it cannot be resolved.

**`100.64.0.1` was not refused, and the docstring said it was.** The check was
`is_private or is_loopback or is_link_local or is_reserved or …`, and RFC 6598
carrier-grade NAT is none of those by Python's reading — both `is_private` and
`is_reserved` are False for it. The enumerated predicate was narrower than the
prose above it claimed. It is now `not address.is_global`, which is the
predicate that actually means "allocated for the public internet", covers CGNAT
and the documentation, benchmarking and future-use blocks, and tightens itself
for free when a future Python learns a new special-purpose registry entry.

Two smaller things the matrix also fixed: a path containing a null byte escaped
the guardrail as `ValueError` from `lstat` rather than as a refusal — it had
still stopped the read, but had stopped saying *why*, which over MCP means the
caller gets a platform message instead of the rule and the remedy. And it pinned
a behaviour worth pinning: `~` in a manifest-supplied asset path is an ordinary
directory name inside the bundle, neither expanded nor treated as an escape.

The matrix asserts four properties rather than a list of cases, which is what
makes it a guard rather than a snapshot: no `(offset, limit)` a caller can send
exceeds the cap, fails to advance, or loses a byte; every hostile path is refused
*as a `Denied`*; every spelling of an internal address is refused by both the
source check and the redirect check, and the operator opt-in covers exactly the
same set; and across the cross product of all four read tools and the whole
hostile corpus, the only exception that ever escapes is `Denied`.

### P4-12 — What a maximum-effort review found after two passes had already run

`/code-review ultra` refused PR #9 on size (88 files, ~9,000 lines), so the
contingency ran: `/code-review max` inline, ten finder angles over the whole
phase branch. It found eleven more defects *after* the cleanup pass and the
`high` review, which is itself the finding worth recording — the earlier passes
were not lax, they were looking at the diff rather than at the system.

**Two were regressions introduced by the previous round of fixes:**

- **Multicast stopped being refused.** P4-11 replaced the enumerated address
  check with `not address.is_global` to catch CGNAT. But `is_global` reports
  `224.0.0.0/4` and `ff00::/8` as *public*, so `http://239.255.255.250:1900/`
  (SSDP) and `http://[ff02::1]/` (mDNS) sailed through a default-deny server.
  Fixing one gap opened another, and the docstring claimed multicast coverage
  in both versions. Multicast is now named explicitly.
- **A config setting and a tool argument for the same option crashed the call**
  — recorded as P4-10 already, and caused by P4-8's own refactor.

**The most serious was not a regression: guardrail (c) only ever guarded the
front door.** See P4-13.

**The rest, each now with a regression test:**

- `read_markdown` **destroyed** any character straddling a page boundary. Byte
  offsets and `errors="replace"` turn a split sequence into U+FFFD on *both*
  pages, so `naïve café` reassembled as `naïve caf��e`. The comment
  above the decode claimed the opposite ("the client gets the whole character
  on the page that contains its start"). Pages now end on a character boundary.
  The boundary matrix missed it because it reassembled *bytes*; it now
  reassembles text.
- A `content.md`, `chunks.jsonl`, `assets.json` or `manifest.json` that was a
  **symlink out of the bundle** was read straight through: `resolve_bundle`
  resolves the directory, and those four fixed names were the one place
  confinement was assumed rather than applied.
- The **staging directory lives inside `output_root`**, which is always a
  readable root — so a read tool could reach a capture whose `final_url` check
  had not run. Staged paths are now refused by name.
- **`::127.0.0.1`** (IPv4-compatible IPv6) normalises to `::7f00:1`, which
  `is_global` calls public. `ipv4_mapped` was unwrapped; `::/96` and 6to4 were
  not.
- **Malformed bundle JSON escaped as `JSONDecodeError`, `AttributeError`, or a
  pydantic `ValidationError`** from `query_chunks` and `list_assets`, carrying
  file fragments into the agent's context. Only `manifest.json` had been
  guarded. Relatedly, reading records *strictly* was wrong in the other
  direction: `extra="forbid"` meant a bundle from a later WebShot with one new
  asset field would fail every record and make the whole bundle unreadable. A
  reader of other people's files degrades; the writer's contract is enforced on
  recorded bundles in `tests/test_contracts.py`.
- **`WEBSHOT_OCR_PSM` did not work**, because pydantic will not coerce a string
  into an int-valued `Literal` even in lax mode — while the guide promised every
  setting had a variable. There is now an env round-trip test over *every*
  setting, with the two list-valued exemptions named rather than absent.
- **`capture.css` was the one path setting that was not `ExpandedPath`** — the
  exact case the alias's own comment says it exists to prevent. `~/brand.css` in
  a config file failed every capture on that server.
- **`pdf.margin` bypassed its only validation.** `validate_css_length` lived as
  an argparse `type=`, so it ran on flags and on nothing else; a margin from a
  file, the environment, or an MCP call reached Chromium unchecked and died
  there as an unclassified exit 1. The rule moved onto the model with the other
  twelve.
- **`page_count_invariant.passed` was `True` by construction.** `expected` was
  `source + appendix` and `actual` was the manifest's `pdf.pages`, but the
  manifest *derives* `appendix` as `pages - source` — so the spec §2.2 gate that
  exists to catch a lost page was comparing a value to itself, which is
  precisely what `assemble._verify`'s docstring warns against. `actual` is now
  `validate_pdf`'s independent re-read of the published file.
- **`counts.pages` contradicted the manifest** on the protected path: it used
  the composed PDF's page count, which includes the transcript appendix, while
  the bundle counts source pages.
- **`timings` always contained `validate`**, contradicting the contract stated
  in two docstrings ("a stage a run never entered is absent") — a `validate:
  0.0` on every capture reads as "veraPDF ran and was fast".
- **A progress notification could fail a published capture.** The heartbeat
  suppressed its errors; the four milestone reports did not, and the last one
  runs *after* the PDF and bundle are in place. A client that closed the stream
  mid-capture got a tool error for a capture that had fully succeeded, and never
  learned the path.
- **The golden corpus stopped being hermetic** the moment settings gained an
  environment layer: `tools/golden/harness.py` passed `os.environ` straight
  through, so a maintainer who had exported `WEBSHOT_CONFIG` would re-record
  every case under their own settings — visibly in `report.json`'s `options`,
  invisibly in the PDFs — and CI would call the corpus regressed. Seven
  `load_config` calls in `tests/test_settings.py` had the same problem.
- Two staging directories in one process **collided**: the name was keyed on the
  pid, but the capture slot is per-service, so two servers sharing an output
  root would delete each other's in-flight capture.
- A source WebShot cannot accept now exits **2** rather than 1. Moving the
  option rules onto the model (P4-8) put them *after* `normalize_source`, so
  `webshot ftp://x --legacy-bundle --no-ai-bundle` had quietly started exiting 1
  where it used to exit 2. An unusable source is a usage error either way.

**Coverage gaps closed:** the protected path's QA report had no test of any kind
— no golden case records one — so `page_count_invariant` was unreachable code
in practice. The `ai_summary` key contract between the pipeline and the report
is now pinned too, since `counts()` reads it with `.get()` and a renamed key
would publish `null` rather than fail.

### P4-13 — Guardrail (c) checked the front door and left the windows open

The most serious defect of the phase, demonstrated end to end by the review's
adversarial angle.

`check_source` and `check_final_url` classify the URL the *caller* supplied and
the URL the browser *ended on*. Neither says anything about what the page
fetches in between. So a capture of a page inside the configured roots —

```html
<iframe src="http://127.0.0.1:8080/secret"></iframe>
<script>fetch('http://169.254.169.254/latest/meta-data/')
  .then(r => r.text()).then(t => { document.body.textContent = 'LEAK:' + t; });</script>
```

— reaches the internal address, and the response lands in `content.md`, which
the agent then reads back with `read_markdown`. The review stood this up
against a live loopback server and recovered the secret from the published
bundle. A public page can do the same thing; the `file://` case was simply the
easiest to demonstrate.

A second shape of the same hole: `pipeline.py` sets `final_url = page.url` only
when the source kind is `web`, so for a local file it is the source string —
and `check_final_url` no-ops on a `file:` scheme. A local page that
`location.replace()`s to an internal URL therefore published a render of the
internal page while the manifest and the tool result both named the harmless
file.

**The fix is at the request level, where the question actually is.**
`CaptureOptions` gains `block_private_requests`, off by default so the CLI is
untouched — a person who types a URL chose it, and a page that loads an intranet
image is their business. The MCP server turns it on whenever it is refusing
internal *targets*, and off when the operator has opted into them, because then
the localhost dashboard's own subresources are the point. `capture/session.py`
installs a Playwright route handler that aborts any request to an internal
address, caching the verdict per host.

The classifier moved to `webshot/netpolicy.py` so both layers share one answer:
the MCP guardrail decides whether a capture *may be requested*, the capture
session decides whether a page *may fetch a subresource*, and a second copy of
the address rules would be a second thing to get wrong.

`tests/test_mcp_server.py` proves it against a recording server: with the filter
on, the internal host receives nothing; with it off, it receives `/img`,
`/fetch`, and `/frame`. The assertion is on the server's log rather than on the
captured text, because a subresource that lands after the snapshot leaves no
trace in the markdown either way — the first version of that test passed for
the wrong reason.

**What it still does not cover**, stated rather than implied: WebShot resolves a
name and Chromium resolves it again, so a name whose answer changes in between
is outside both checks. And a page can still *encode* data it already has into a
request to a public host; this is a network boundary, not an exfiltration
boundary.

### P4-14 — CI environment parity for optional-extra modules

The `lint + types` job went red on PR #9 while every check passed locally.
Cause: that job installs `uv sync --extra dev --frozen`, and mypy is configured
with `files = ["src", "tools"]` — the whole tree, including
`src/webshot/mcp_server/server.py`, which imports the SDK that lives in the
`[mcp]` extra. Three `import-not-found` errors, invisible in a development venv
that has the extra installed.

The general shape is worth naming, because Phase 4 is the first phase to put an
importable module behind an extra: **a job that type-checks the whole tree needs
an environment that can resolve the whole tree.** The `tests` and `docs` jobs
had already been given `--extra mcp` for their own reasons; the lint job was the
one that type-checks the most and had the least installed.

Three fixes were possible, and the deciding fact is that both `mcp` and
`mcp-types` ship `py.typed` — they are PEP 561 packages with real type
information:

| | | |
|---|---|---|
| **A — add `--extra mcp` to the lint job** | **chosen** | mypy checks the bridge against the real SDK API |
| B — add `mcp` to the `dev` extra | rejected | `dev` is tooling (pytest, ruff, mypy, pip-audit), not features. It is also what CONTRIBUTING tells a contributor to install, and what six other CI jobs sync — so B drags starlette, uvicorn, httpx2, pyjwt and opentelemetry into all of them and onto every first-time `uv sync` |
| C — `[[tool.mypy.overrides]]` for `mcp.*`, `mcp_types.*` | rejected | it would make `MCPServer`, `Context`, `CallToolResult` and `ServerRequestContext` all `Any` in the one file that is the SDK bridge and the security-critical registration surface. The existing overrides in `pyproject.toml` are for genuinely untyped upstreams (`img2pdf`, `defusedxml`) or for typed ones whose *stubs* mypy cannot parse under the 3.11 floor (the numpy family, `follow_imports = "skip"`). Neither describes a typed SDK that is simply absent from one job |

That the coverage is real rather than nominal was checked rather than assumed:
with the extra installed, a deliberately wrong SDK call
(`context.report_progress("not-a-float")` without an `await`) fails mypy with
`[unused-coroutine]`. Under option C it would have passed silently.

Verified with the job's own commands: `uv sync --extra dev --extra mcp --frozen`
then `uv run mypy` → clean, and `ruff check` / `ruff format --check` clean in the
same environment.

### Environment at the end of Phase 4

mcp 2.0.0 with mcp-types 2.0.0 behind `[mcp]`; mkdocs 1.6.1 and
mkdocs-material 9.7.7 behind `[docs]`. Nothing else moved: chromium 151.0.7922.34,
tesseract 5.5.3, Ghostscript 10.7.1, veraPDF 1.30.0, docling-slim 2.120.3,
docling-core 2.92.0, chonkie 1.7.0, nh3 0.3.6, markitdown 0.1.7,
trafilatura 2.2.0, playwright 1.62.0, CPython 3.12.14. The license gate passes
with every extra installed: 156 packages, none denied.

## Phase 5 corrections

### P5-1 — The exit-code taxonomy had no boundary between 2 and 4, or 3 and 5

Spec §3 listed nine codes and the phases adopted them a path at a time, which
worked until the last path. Finishing the migration meant classifying about
thirty `raise WebShotError(...)` sites at once, and half of them sat on a line
the table did not draw:

- Is a `--storage-state` file that does not exist a **usage** error (2) or an
  **invalid profile** (4)?
- Is an HTTP 500 a **navigation failure** (3) or a **capture integrity**
  failure (5)?
- Is a `--selector` that matches nothing a usage error, or a readiness failure?

Each has a defensible answer either way, which is exactly why leaving it to the
next person guarantees drift. Two boundaries were chosen and written into the
spec:

- **2 is "the arguments could not be used as given"** — decided before anything
  runs. **4 is "the far end refused, or the auth material was rejected"** — a
  decision only the site can make. So a missing storage-state file is 2 (it is
  the same shape as a missing `--css` file, already 2), and an HTTP 401/403 is
  4.
- **3 is "the page never became available to capture"**, which widens the
  table's original "navigation or readiness *timeout*" to cover an unreachable
  host and a refused status. **5 is "the capture ran and what came out cannot
  be trusted"**, which is where the protected path's page-count and back-link
  invariants live. A `--selector` that matches nothing is 2: the selector is an
  argument, and it did not match.

**Applied:** the boundaries are in spec §3 rather than in the commit message.
`errors.EXIT_CODES` became the single written form of the table — `--help`'s
epilogue is generated from it, and a test asserts the spec's own table lists the
same codes. `WebShotError`'s default moved off 1 (the spec says v3 never emits
it) onto 5, and an AST test asserts no module raises the bare base class, so
"unmigrated path" cannot be re-introduced by habit.

### P5-2 — `--validate-pdf=strict` threw away the only report worth having

`_conformance_check` raised `PdfValidationError` from inside the pipeline, which
meant the `CaptureResult` for that run was never constructed. Task 5.1 asks
every failure to write a QA report; for this one failure that would have
produced the *least* informative report of the set — source, options, exit 7 —
about the only failure that happens **after** a successful publication, where a
file exists, veraPDF ran, and there is a complete findings record to publish.

**Applied:** `_conformance_check` reports the strict verdict instead of raising
it, the pipeline builds the `CaptureResult` and then raises
`PdfValidationError(..., result=result)`, and `WebShotError` carries an optional
`result`. The CLI writes the full report for a failure that reached
publication, and a minimal one otherwise. Exit 7's report is therefore a
complete record of a capture plus the verdict that failed it.

The same task raised a smaller design question with a bigger blast radius: where
does the *message* go? Adding `error: str | None` to `QaReport` puts
`"error": null` in every successful report, which moves 19 recorded goldens for
a field that says nothing on the success path. The report is written with the
key **omitted** when it is null, which is the rule `timings` already follows for
a stage a run never entered: absence is the answer. `"error" in report` is then
a test a consumer can make rather than a null it has to interpret, and a v3.0
success report keeps the exact shape Phase 4 published.

### P5-3 — Two version literals, and the packaging metadata was the stale one

`src/webshot/version.py` held `VERSION` and `pyproject.toml` held its own
`version = "2.2.0"`. Both were 2.2.0, so nothing had gone wrong yet — but three
things publish the version (the wheel's metadata, `--version`, and every
bundle's `generator_version`), and the way they stop agreeing is someone
bumping one of them. A bundle claiming to come from a build that was never
released is a provenance failure, which is the one kind this project cannot
afford.

**Applied:** `pyproject.toml` declares `dynamic = ["version"]` and reads the
module through hatchling's version hook. A test asserts the installed
distribution's metadata equals `VERSION`, that the pyproject derives rather than
repeats it, and that no second literal exists in `src/`, `tools/` or the root.

Worth recording as a *negative* result too: after the bump, the golden corpus
passed 20/20 on the **strict** profile against a corpus recorded at 2.2.0. That
is the harness's version mask being complete — `generator_version`,
`tool_versions.webshot`, and the "Generated by WebShot X.Y.Z" strings in the
bundle README and the protected PDF's `/Creator`. The plan anticipated a
re-record and none was needed.

### P5-4 — The ONNX runtime cannot be trimmed, so the exception was made to expire

P3-2 recorded that `pip install webshot` carries an ONNX runtime it never
invokes, and left it. Phase 5.5 re-examined whether it could be removed and it
cannot:

| Route | Verdict |
|---|---|
| Wait for an upstream release where magika is optional | `magika~=0.6.1` is an **unconditional** requirement of every markitdown 0.1.x — all eight checked against PyPI, 0.1.7 being the latest |
| Wait for a magika that makes the runtime optional | magika 0.6.2 declares `onnxruntime>=1.17.0` unconditionally and publishes no extras |
| Move MarkItDown behind an extra | takes CSV, TSV, EPUB and ZIP out of the base install — formats spec §1.1 and the README promise there |
| Override the resolution in `uv.lock` | makes CI green about a footprint users do not have, which is worse than the weight |
| Re-add the bespoke parsers | undoes ADR-0001, to save 75 MB |

**Applied:** the gate does not assert no-onnxruntime, because that assertion
would be false. It asserts the line that is actually load-bearing — no torch, no
GPU stack — and adds the *accelerated* ONNX builds (`onnxruntime-gpu`,
`-openvino`, `-directml`, `-training`) to the deny list by name, because each is
a GPU or vendor-accelerator stack under a name close enough to the exception to
be waved through by someone skimming. The exception is then written to expire:
`test_the_onnx_runtime_exception_is_still_needed` fails **on the good news**, so
the day MarkItDown drops magika, CI says so instead of the allowance quietly
outliving its reason.

The general shape: a documented exception with no test is indistinguishable
from an undocumented one a year later.

### P5-5 — A perf budget is only as good as the machine it was calibrated on

docs/06 specified the perf smoke as "median of 3 runs on the standard runner,
budget = 3× the recorded baseline median" and did not say where the baseline
comes from. The obvious reading — record it while writing the tool — produces a
budget calibrated on a maintainer's laptop and enforced on a shared CI runner.
This laptop's median is ≈ 4.0 s; a GitHub-hosted runner is slower and noisier,
so a 12 s budget would be either permanently red or, in the other direction,
unfailable.

**Applied:** the baseline records the environment it was taken on (system,
architecture, runner), and a run whose environment does not match publishes its
number **without judging it**. The baseline is produced by the CI workflow's own
`record_perf_baseline` input, which makes "recorded on the runner" a procedure
rather than an intention — including for the re-records docs/06 permits on a
pinned-browser upgrade.

Two smaller things the same reasoning closed: the first run pays for Chromium's
first launch and the font cache, so it is discarded rather than folded into the
median; and a **missing** baseline exits non-zero and says so, because silently
passing with nothing to compare against is how a budget stops existing without
anyone deciding to remove it.

### P5-6 — Nothing in the plan ever used a built artifact (plan amendment)

Phase 5's release task built an sdist and a wheel and attached them to a
release. No task installed one. Every other check in this repository runs
against the source tree, where `src/webshot/` is importable, `schemas/` is on
disk beside it, and the console script is an editable shim — none of which is
what a user has. The bugs that shape hides are the ones a release cannot
survive: a package that forgets a subpackage, an entry point that points at a
moved function, a data file that exists only in the checkout.

**Applied:** docs/05 gains task 5.6, and `tools/release/wheel_check.py`
implements it — `uv build`, an empty virtual environment, `uv pip install
./dist/webshot-*.whl[extras]`, and then a real capture, `webshot schema
manifest` and `webshot doctor` run from a working directory that is not the
repository. It is a step in `release.yml` before anything is published, and it
is runnable locally.

Two findings from running it the first time, both worth recording:

- **The wheel needs no data files.** The task expected to check that `schemas/`
  ships in the wheel; it does not, and does not need to, because
  `webshot schema` generates every contract from the pydantic models rather
  than reading a file. `src/webshot/` contains no non-Python file at all. So the
  gate asserts the invariant that is actually load-bearing: what an *installed*
  wheel prints for `webshot schema manifest` is byte-identical to the committed
  `schemas/manifest.schema.json`, and every contract the wheel lists has a
  committed counterpart.
- **The wheel resolves its floors, not the lock.** Installing the wheel outside
  the project pulled docling-slim 2.121.0 where `uv.lock` pins 2.120.3, which is
  what a user actually gets — and is therefore worth exercising rather than
  pinning away.

### P5-7 — Two specification MUSTs that no phase had built

Auditing the documents against the code for the release turned up two
requirements with no implementation and no plan row, the same gap P0-1, P3-7 and
P4-3 found in earlier phases:

- **§5.1's per-output lockfile.** Two runs aimed at one `--output` did not
  collide over their temporary files, which carry the process id, but they
  published into the same PDF path and the same `.ai` directory. The winner
  could be the PDF from one run and the bundle from the other — a manifest whose
  `pdf.sha256` does not match the file beside it, which is precisely the
  integrity claim §2.2 exists to make. Implemented in `outputlock.py`: an
  `O_EXCL` file holding the owner's process id, so the claim is atomic; exit 2
  naming the holder and the file to delete; and a lock whose owner is no longer
  running is taken over, because a crash must not make an output path
  permanently uncapturable.
- **§7's retirement of the legacy entry-point shims at v3.0.** `webshot_new.py`
  and `simple_web_to_pdf.py` were still in the tree. Both are deleted;
  `webshot.py` stays, as plan task 0.1 says.

The lesson is the one the earlier corrections keep repeating in a different
place each time: a MUST written in a specification and not carried into a plan
row is a MUST nothing builds. What is new here is *when* it was caught — the
release audit is the last point at which that is cheap.

### P5-8 — A generated page had a hand-written copy of a generated table

`tools/docs/cli_reference.py` generates `docs/guide/cli.md` from the parser, and
it already interpolates a computed list (`Available schemas: {schemas}`). Its
exit-code table was nonetheless typed out inside the template — so when P5-1
finished the migration, the published page still said "navigation or readiness
*timeout*" where the constant said *failure*, still described code 4 as "the
profile is invalid", and still closed with **"paths that phase 5.1's audit has
not reached still exit `1`"**, which that same phase had made false. The
`errors.EXIT_CODES` docstring claimed four consumers and had three.

**Applied:** the table is generated from `EXIT_CODES`. `docs/guide/troubleshooting.md`
carries the same claim in a hand-written table with an extra "Usually" column
and no generator, so it was corrected by hand and a grep confirms no page still
tells a reader that anything exits 1.

The shape is worth naming because it is the *inverse* of the usual one: this
was not documentation drifting from code nobody checked, it was a file whose
entire purpose is to be generated carrying a block that was not.

### P5-9 — Three defects the cleanup review found, two of them in the promise

Four reviewers ran over the Phase 5 diff for reuse, simplification, efficiency
and altitude. Three findings were real defects rather than tidiness, and two of
them falsified the phase's own headline claim:

**"v3 never exits 1" was true only for the exception types someone listed.**
The CLI caught `(WebShotError, PlaywrightTimeoutError, PlaywrightError,
OSError)` and `classify` — the function written precisely to own "which
exception means which code" — was reachable only through that tuple. Anything
else escaped as a traceback, and a traceback is Python's own exit 1. Verified
by raising a `ValueError` inside the pipeline: exit **1**, no QA report. Live
candidates were not exotic — pypdf's parser types, `subprocess.SubprocessError`
from Tesseract or Ghostscript, a `KeyError` in a bridge. The suite was green
because it tested `classify` in isolation, which is the shape of test that
cannot see a caller that never calls it. Fixed with `except Exception`
(`KeyboardInterrupt` and `SystemExit` derive from `BaseException` and are
deliberately still not caught), and the regression test now goes through
`main`.

**§5.1's lock protected nothing on the MCP path.** `convert_url_to_pdf` locks
the output it is given; what the MCP service gives it is a path inside a
per-process staging directory that no other process can name. The swap that
matters happens afterwards in `_publish`, into `output_root` — by which time
the lock has been released. Two servers sharing one output root, which is the
ordinary case because a stdio server is spawned per client from one
`webshot.toml`, compute the same slug from the same URL and can interleave the
PDF swap with the bundle swap. Guardrail §6.8f does not cover it: it serializes
captures *within* an instance. Fixed by locking the path that is actually
published, from before the capture, so a second caller is refused early rather
than after paying for a capture it cannot publish.

That test needed a second attempt, which is the more useful half of this entry.
The first version passed against the *unfixed* code — because the staged PDF
has the same basename as the published one, locking the staged path made the
service collide with its own pipeline's lock and raise the very same
`UsageError` for entirely the wrong reason. The message was identical; only the
lock *path* differed. The test now asserts which lock file the refusal names,
and was re-run against the reverted code to prove it fails there.

**The perf budget could not fail, and the first fix did not fix it.** The step
ended with `exit "${PIPESTATUS[0]}"`, which reads the status of the last command
in the braces — and with a closing ``` fence in there, that is the `echo`. An
over-budget run reported success.

The obvious repair — move the capture to `uv run … || status=$?` inside the
braces — is also wrong, and testing it is the only reason that is known here
rather than in six months: **bash runs both sides of a pipeline in subshells**,
so an assignment inside a brace group piped to `tee` is discarded with the
subshell and `$status` is 0 again on the other side. Proven with a five-line
script before the second repair was written.

What works is not having a pipeline around the thing whose status matters:
redirect the tool to a file, compose the step summary from that file, and the
status is an ordinary variable in the step's own shell. The **veraPDF gate had
the identical latent bug** and is fixed the same way — it is non-blocking either
way, but a gate that cannot fail is worse than no gate, because it looks like
evidence. The license gate is correct as written, because there the tool *is*
the last command in the braces.

Two smaller ones worth recording. The perf job skipped the Playwright cache on
the theory that a downloaded browser is a different first run from a cached one
— but `playwright install` completes before the tool starts, and the warm-up
capture is discarded precisely to absorb cold start, so the theory cost a
~150 MB download on every push and bought nothing. And the "omit `error` when
null" rule lived in the writer while the field lived on the model, so any other
caller of `model_dump` would have re-introduced the null; it moved onto
`QaReport` as a `model_serializer`, which is where a property of the contract
belongs.

### P5-10 — What a maximum-effort review found after the cleanup pass

Ten angles over the Phase 5 diff, after `/simplify` had already run and been
applied. It found more than that pass did, and the pattern is the one P4-12
recorded: **a review of the diff misses what a review of the system finds.**
Several findings were in code the earlier pass had just rewritten.

**Two would have broken the release itself.**

- **`gh release create` had no permission to create anything.** The workflow
  declares `permissions: contents: read`; the `release` job declared none, so
  it inherited read. The real tag push would have run the whole twenty-minute
  verification — full suite with every extra, wheel build, the wheel-install
  gate, veraPDF — and then 403'd on the last call. What makes this worth the
  entry is *why the rehearsal could not catch it*: a dry run is defined as the
  run that skips that step. A green rehearsal is evidence about everything
  except the one action a release exists to perform. The step's own comment
  claimed "the only place the permission is granted", which is the shape of
  comment that stops a reader checking.
- **The perf budget could not fail, and the first repair did not repair it** —
  see P5-9, corrected in place.

**Three were in the output lock, and they were the lock's whole point.** The
first design claimed the lock with `O_EXCL` and decided staleness by asking
whether the pid inside was alive. A reviewer wrote scripts that proved three
separate mutual-exclusion failures: the file exists for a moment *before* the
pid is written, so a concurrent run reads an empty file, concludes the owner is
dead, and takes a live lock; the stale-takeover `unlink` was unconditional, so
it could delete a lock a third process had just claimed; and a pid too large
for `pid_t` made `os.kill` raise `OverflowError` — not an `OSError` — which
escaped the handler and left the output path permanently uncapturable. On
Windows the probe was worse than any of them: CPython's `os.kill` special-cases
only the two console-control signals, so signal 0 becomes `TerminateProcess`,
and the liveness *check* would have killed the holder and then taken the lock.

The module was rewritten on `flock`. The lock lives on the open file
description, the kernel releases it however the process dies, and there is no
staleness to reason about and no pid to parse — the pid is still written, but
only so a refusal can name who holds it. The lock file is deliberately **not**
deleted on release, because unlinking reintroduces the same class of race: a
second process can already hold a descriptor to that inode, so a new run would
create a fresh file and lock that instead. The test that proves it is now a
real second process, because an in-process test cannot see the failure the
first design had.

Two more the lock rewrite absorbed: it guarded the PDF path only, so two runs
with different `--output` and one shared `--ai-bundle-dir` took different locks
and raced on the bundle anyway; and it decorated the guarded name, so a
250-byte output name made the *lock* fail with `ENAMETOOLONG` on a capture that
used to work. It now takes every published path, in sorted order, and shortens
by digest when the decorated name will not fit.

**The rest, each a real defect:**

| | |
|---|---|
| A successful capture exited **9** when `--report` could not be written | the report write sat inside the capture's `try`, so a full disk turned a published, valid PDF into a failed run — and `_record_failure` then overwrote the destination with a report saying the capture never happened |
| `[ocr] require = true` made `--no-ocr` **unusable** | the flag names `no_ocr`, the setting lands on `require_ocr`, and the model refuses the pair — so the file won by making the command line impossible, inverting spec §1.1's flags > env > file. The flag now drops a setting it contradicts |
| `classify` sent `TimeoutError` and `ConnectionError` to **9** | both are `OSError` subclasses, and in 3.11+ `TimeoutError` *is* `asyncio.TimeoutError` and `socket.timeout` — so a timed-out wait was reported as an environment failure `webshot doctor` could not explain. They are answered before `OSError` now, as 3 |
| An auth path could reach the QA report's `error` string | spec §2.3 keeps `--storage-state` and `--auth-profile` out of `options`; `error` arrived as a raw `str(exc)` with no such gate, and the refusal for a missing storage-state file interpolates the path. Redacted where both report shapes converge, from the *parsed command line* — because the refusal fires while building the options, so there is no `CaptureOptions` to read them from |
| The generated exit-code table rendered as a **run-on paragraph** | `ArgumentDefaultsHelpFormatter` inherits `_fill_text`, which re-wraps the epilogue. The one thing generating it from `EXIT_CODES` was for did not work |
| `_record_failure` duck-typed `.result` and guarded only `OSError` | any exception carrying a `.result` attribute would have been read as a `CaptureResult`, and the resulting `ValidationError` was outside the guard whose docstring promises a report never replaces the failure |
| An unforeseen exception logged **no type** | `str(KeyError("x"))` is `"'x'"`. An operator hitting a genuine WebShot bug got one line and no name for it; `--verbose` now adds the traceback |
| `materialize_source`'s `OSError` was **exit 2** | a stale NFS handle told an operator their flags were wrong. Split: a missing or wrong-kind path is 2, everything else the filesystem says is 9 |
| The `--require-ocr` gate ran **between** `materialize_source` and its cleanup | leaking the rendered temp tree until GC. It runs before, which is also the better order: refusing for a missing recognizer should not first render a 40 MB DOCX |
| The published `error` schema said `anyOf: [string, null]` | blessing a document the serializer can never emit, and forcing consumers to handle a value they will never see |
| `docs/06` said the release is manually approved, and it was not | Phase 5 had rewritten the *document* to match the workflow. The job takes a `release` environment instead, so arming the gate is a repository setting |
| Two new tools ran captures with `WEBSHOT_*` inherited | a maintainer with `WEBSHOT_CONFIG` exported could have **recorded a perf baseline under personal settings**, with nothing in the environment record to say so |

**One consequence worth stating, because it changed a claim.** The lock now
touches every published path before the capture starts, so a destination whose
parent is a regular file is refused as a usage error immediately instead of
failing at bundle-build time after a full capture. That is a better outcome —
and it removes the last command-line route to exit 8, which is now reached by
injecting a failure at the real publication boundary. The coverage table says
so rather than keeping the older, nicer-sounding claim.

**And one that did not reproduce.** A single golden run reported one differing
case, immediately after a `uv sync` that re-added three extras. Six subsequent
runs were clean, the harness collects nothing that could hold a lock file
(`bundle.rglob` plus the named artifacts), and the cause was not found. It is
recorded here rather than omitted.

### Environment at the end of Phase 5

Unchanged from Phase 4 apart from the project's own version: chromium
151.0.7922.34, tesseract 5.5.3, Ghostscript 10.7.1, veraPDF 1.30.0,
docling-slim 2.120.3, docling-core 2.92.0, chonkie 1.7.0, nh3 0.3.6,
markitdown 0.1.7, trafilatura 2.2.0, mcp 2.0.0, mkdocs 1.6.1 with
mkdocs-material 9.7.7, playwright 1.62.0, CPython 3.12.14. **Phase 5 added no
runtime dependency**; the license gate re-run against the final `uv.lock` with
every extra installed reports 156 packages, none denied, and now identifies the
project as `webshot 3.0.0`.

## Post-release correction — P6-1

**The web path never had the associated-file embeds docs/02 assigned to it.**
The module map named `webshot/render/post.py` as the home of "AF embedding
with `AFRelationship`", and that module was never written: embedding existed
only in `protected/assemble.py`, so an ordinary capture produced a PDF and a
bundle directory that had to be kept together. Nothing failed, which is why no
phase caught it — the sidecar bundle works, and no acceptance criterion asked
whether the PDF could stand alone. It surfaced from *using* the tool: a real
capture of a live site reported `attachments: 0`.

Built as `render/embed.py`, with `embed_associated_files` moved out of the
protected path and shared rather than written twice — it had already been
proven against PDF/A-3B by veraPDF, including the two ISO 19005-3 clause 6.8
defects (`/UF`, a mangled MIME `/Subtype`) that only a validator finds. The
protected-viewer golden is byte-identical across all fourteen artifacts after
the move, which is the evidence that sharing changed nothing about the path it
came from.

Two things the implementation had to respect. The manifest records the
published PDF's SHA-256, so embedding happens *between* rendering and
finalizing; an embed after the manifest is written leaves it describing a file
that no longer exists, and `test_manifest_hash_matches_the_published_file`
holds that ordering. And `manifest.json` cannot itself be embedded — it hashes
the file that would carry it — so `capture.json` carries the same provenance
without the circularity, and says in the file that the sidecar manifest remains
the authority on checksums.

**The lesson worth carrying:** every phase verified against fixtures, and the
gap was invisible to all of them because it was a *missing* capability rather
than a broken one. A documented module that no task explicitly created can
simply never be built, and the traceability matrix maps goals to tests — not
architecture rows to files.

### P6-2 — a log line made the capture irreproducible

The embed logged the file it had just written, and at that point the file is
still the process-scoped staging name (`.article-clean.93782.tmp.pdf`). The pid
changes every run, so every web-path capture's stderr became unreproducible —
18 of 20 golden cases failed on CI while passing locally.

They passed locally for a reason worth writing down: **the golden tests are
marked and deselected from the default `pytest` run** (`-m golden` selects
them), so a full local suite that reports "662 passed" has not compared a
single golden. Re-recording and then *running* `tools/golden/check.py` twice —
which is what the record-and-check pair is for — reproduces CI's result in
about a minute. That is now the habit: after any change that touches captured
output, run the check, not just the suite.

The fix is to log the count and let the existing "Created …" line name the
published file, which is the name a reader is looking for anyway.

### P6-3 — embedding provenance makes the PDF's size environmental

With `capture.json` inside it, a capture's PDF is a few bytes different on
another machine, because the provenance names that machine's tool versions.
That is correct behaviour — the provenance is the point — but the strict
golden profile was still comparing the byte figure in the "Created … (2 pages,
252.4 KB)" log line, so 18 cases failed on CI while matching locally.

The harness had already decided this question everywhere else:
`_normalize_manifest` masks `pdf.bytes`, `pdf.sha256` and `tool_versions` for
*both* profiles, on the stated grounds that they are environment rather than
output. Only the stderr size had escaped, in a portable-only rule. Moving it
into `normalize_text` makes stderr agree with the policy the rest of the
harness already applied, and page counts remain compared in both profiles —
this widens no hole, it closes an inconsistency.

## Phase 7 — embedded-video capture

Corrections and findings from building `feat/lms-video-capture`, measured
against the live services on 2026-08-22 rather than inferred. Three of the
five overturn something the proposal in docs/13 asserted.

### P7-1 — the walkthrough endpoint is authenticated, so WebShot does not call it

docs/13 originally recorded that `GET app.guidde.com/c/v1/quickguidde?id=<id>`
needs no authentication for a publicly shared playbook. It does. A plain
`curl` answers `401 {"error":"Unauthorized"}`, and so does a `fetch` issued
from the share page's **own** context — the token lives in the app's Firebase
session, not in a cookie the request inherits.

The design consequence is the whole shape of the bridge: WebShot never issues
that call. It navigates a page to the share URL, lets the Guidde app make its
own authenticated request, and reads the response body off the wire through
Playwright's `response` event. There is deliberately no HTTP-client path and
no token replay — a decision recorded here because the alternative is
discoverable and was explicitly ruled out.

Two consequences worth knowing before touching `fetch_playbook`:

- **The intercepted response is wrapped.** The call the page actually issues
  carries `&forEmbed=true` and answers `{"playbook": {…}, "data": {…}}`, where
  `data` is branding configuration. A bridge written against the bare playbook
  finds no `steps` and reports an empty walkthrough. `parse_playbook` unwraps
  `playbook` when present and accepts a bare object otherwise.
- **Waiting for the event is not enough.** Playwright fires `response` before
  an async handler has finished reading the body, so a `wait_for_event`
  predicate that tests "have I got the body yet" races and usually loses. The
  handler sets an `asyncio.Event` once the bytes are in hand, and the wait is
  on that.

### P7-2 — a playbook id is a path segment, on every surface

The one unknown the spike existed to settle: where the id lives on a page that
embeds a player. Answer: it is a path segment of a `guidde.com` URL, in all
three surfaces, verified on one public playbook:

| Surface | Shape |
|---|---|
| share page | `app.guidde.com/share/playbooks/<id>` |
| embeddable player | `embed.app.guidde.com/playbooks/<id>?autoplay=true` |
| hydration call | `app.guidde.com/c/v1/quickguidde?id=<id>` |

The share page's own `twitter:player` meta tag names the embed URL, which is
what an embedder is handed — so the embed form is the one an LMS is most
likely to carry.

**Nothing has to be clicked.** The proposal supposed the id might only be
reachable through the player's link control; it is not. Scanning URL-bearing
attributes (`src`, `data-src`, `href`, `content`, …) over the prepared DOM
finds it whether the host used an iframe, a lazily-hydrated `data-src`, an
anchor, a data attribute, or an `og:video`. `tests/test_video_capture.py`
holds all five shapes plus two look-alike hosts that must **not** resolve.

One trap: that scan reaches `<head>`, where an `og:video` can live, and
`<html>` has no `parentElement` — the locator walk copied from
`capture/snapshot.py` threw on it. The walk is now defined once, null-safe, as
`snapshot.LOCATOR_JS` and injected into all three scripts that need it. The
golden corpus proved the extraction changed nothing.

### P7-3 — subtitle timings are per-step, and reconstruct Guidde's own VTT exactly

Each step's `subtitles[]` restarts near zero. A cue's document-level time is
the running sum of every prior step's `duration` plus the cue's own start.

This is not inferred — it is checked. Guidde publishes a document-level `.vtt`
of its own at `subtitlesUrl`; the offsets computed from the per-step subtitles
reproduce it **cue for cue**, all thirteen, and the step durations sum to the
top-level `duration` exactly (81.4 s). That is the evidence that one
intercepted response is sufficient and the transcript never needs a second
fetch. `tests/fixtures/guidde/subtitles-reference.vtt` keeps Guidde's own cue
timings byte for byte, with cue text and playbook id replaced by the synthetic
fixture's, and is kept as the assertion's other half.

Two properties that follow, and one that does not:

- Cue starts are monotonic and non-negative. Asserted.
- Cue times stay **within** the stated duration. **False, upstream.** The
  closing `end` step has `duration: 1.0` while carrying cues out to 10.7 s
  relative — 91.1 s absolute against a stated 81.4 s. Guidde's own published
  VTT overruns identically, so this is upstream data rather than a
  reconstruction error. It is recorded as a manifest warning and reported as
  Guidde published it, never silently clamped.

### P7-4 — a step with no text is normal, and the fallback chain is what makes it so

On the sample playbook 4 of 11 steps carry no `audioNote` and 3 carry no
subtitles at all: they are silent screen action. They still carry screenshots.
An extractor that requires `description` or `subtitles` per step crashes on an
ordinary playbook.

So instruction text comes from a chain — `description`, then the narration
script with the synthesizer's `/pause-N/` directives stripped, then the joined
cue text, then the step title — and the walkthrough marks a silent step rather
than implying its text went missing. Screenshots need a chain for the same
reason: `docScreenshot` is empty on the cover and closing steps, so the
annotated `drawnScreenshot` leads and four fallbacks follow.

What this produces is **the complete narration plus the per-step written
instructions**, and every artifact says exactly that. It is not a transcript
of a recording, and calling it one would overclaim. Every step in the sample
is `audioNote.type: "textToSpeech"`, where the audio is generated *from* the
authored text — so the text is the source and there is nothing an ASR pass
could recover. A playbook narrated any other way is unverified: the bridge
reports the style in `unverified_narration` and the capture warns, rather than
assuming the reading is complete.

### P7-5 — an appendix cannot be printed with `set_content`

The video appendix illustrates each step with a screenshot already downloaded
into the staging bundle, referenced as a `file:` URL. Under `set_content` the
page stays on `about:blank`, and Chromium refuses `file:` subresources from
it — measured: the image arrives with `naturalWidth === 0`, silently, with no
error anywhere. Navigating to the authored document on disk loads it.

`render/pdf.py` therefore has two appendix entry points over one set of print
settings: `render_html_to_pdf` (the protected path's self-contained appendix,
unchanged) and `render_document_to_pdf` (a file, so it can reference local
images). Worth knowing because the failure mode is a blank picture rather than
an exception.

### P7-6 — `--block-private-requests` did not reach the asset downloads

Caught in review of PR #20, and the near miss is worth recording as much as the
fix. `capture/session.py` enforces the flag with `context.route("**/*", gate)`,
and **Playwright applies route handlers to page-initiated traffic only**: an
`APIRequestContext` request goes straight past them. Measured with an aborting
gate installed on one context:

```
page.goto      : BLOCKED
context.request: status=200 body='LEAKED!'
```

The video asset download was the repository's only `context.request` call, so
this arrived with the feature rather than predating it. It mattered more than
the general shape suggests, for four reasons that stack: the URLs come out of
the *intercepted Guidde response*, so third-party data named the fetch target
(the shape docs/09 P4-13 exists for); step screenshots download on the default
path, not behind `--video-assets`; the bytes are written into the bundle and
embedded in the PDF the user shares; and `mcp_server/service.py` sets
`block_private_requests` from its own policy, so the caller most likely to be
driven by untrusted input was the one relying on the guarantee.

Two things the fix had to get right. It is **gated on the flag, not
unconditional** — an LMS on an intranet serving its media from a private
address is a real deployment and plausibly this project's own, so refusing by
default would break a legitimate capture silently. And it re-checks
`response.url` **before reading the body**, because a public URL that redirects
to an internal one walks straight through a pre-check; the page gate gets that
for free (a redirect is a new request and is routed again), so this is the same
guarantee restated where the route handler does not reach.

Also verified while fixing, because the claim it supports is load-bearing:
`context.route` **does** cover pages created after it was installed, so
`fetch_playbook`'s `context.new_page()` navigation was protected all along.
The download was the only hole.

### P7-7 — the walkthrough output is golden-locked through a third case kind

The intricate half of this feature — an appended appendix, a two-level outline,
per-step chunks, four artifacts per video — was covered by assertions but never
byte-compared, which is the gap P6-2 was written about, reopened in the newest
subsystem.

What blocked it looked structural: the golden runner shells out to
`python -m webshot`, and a subprocess has no way to be told where the Guidde
stand-in is bound. But the corpus already had a case kind that does not shell
out — `protected-viewer` calls the assembly directly, because its path needs a
fixture a command line cannot supply. `kind="video"` is the same answer to the
same problem: serve the stand-in on loopback, drive the pipeline in-process,
record everything.

One normalization was needed. The stand-in binds port 0, so its origin differs
every run and reaches the artifacts legitimately — a video's `source_url` is
the URL its walkthrough was actually read from. `normalize_text` masks it to
`<STANDIN>`, the same treatment the working directory already gets. Recorded,
then checked twice on different ports.

### P7-8 — `<audio>` was missing from a count whose job is to be summed

`_MEDIA_SCAN` selected `video` only, so the `embedded-media` fixture's audio
briefing — which has a German subtitle track — went unmentioned while the video
beside it was flagged, and `video_tally.total` said 1 on a page with two media
elements.

Worth fixing immediately rather than with the ASR work, on a distinction worth
keeping: this was not a missing capability but a **false number**. The tally
exists so a journey-wide count is a sum of manifests, and a field that is
quietly wrong is worse than one that is absent. Audio is in scope on its own
merits too — the question the list answers is what spoken content the bundle
does not have, and an audio briefing is nothing but spoken content.

### P7-9 — the per-file download cap bounded nothing on its own

`MAX_ASSET_BYTES` refuses a single oversized asset, but the *number* of assets
comes out of the intercepted response, so a playbook claiming ten thousand
steps would have paid the per-file limit ten thousand times.

A cumulative budget in bytes rather than a cap on steps, deliberately.
`--max-assets` counts elements because a page's visuals are things a reader can
see and fifty is a sane number of them; a walkthrough's steps *are* the
document, and truncating a long one at an arbitrary count would silently drop
the procedure's ending. Bytes is what actually bounds the work and is where
`--video-assets` puts its weight — and when the ceiling is reached the steps
past it keep their instructions and timings and lose only their screenshots.

### What the feature changed in existing output

One thing, on every capture: `report.json` gains `"videos": true` and
`"video_assets": false` in `options`. No PDF byte, no bundle file, no
manifest, and no log line changed on any video-free fixture — the golden
corpus was re-recorded and the diff is those two keys and nothing else. The
one exception is `embedded-media`, whose fixture genuinely has a `<video>`
and which now records it as untranscribed; that is the feature working.

## Post-review corrections — P7-10, P7-11

A max-effort review of PR #20 (ten finder angles) found twenty-four defects in
the embedded-video work. What is worth recording is not the count but the
pattern: **the errors clustered where third-party data crosses into an
artifact, and where one piece of text is written to two places.**

### P7-10 — the twenty-four, grouped by what caused them

**One text, two locations.** The worst of them: `walkthrough_markdown` built
image references relative to `videos/<id>/`, correct for the standalone file —
and `inline_into_content` spliced the *same string* into `content.md` at the
bundle root, where every screenshot resolved two levels above the bundle. It
was recorded in a golden and called green. The fix is to render the text once
per destination (`asset_prefix`), not once and hope.

**Third-party JSON reaching code that assumes well-formedness.** `json.loads`
accepts bare `NaN` and `Infinity`, and `1e400` is ordinary valid JSON that
parses to `inf` — so a playbook could hand us a float that `int()` refuses, and
every timestamp helper calls `int()`. The crash landed inside the artifact
writers, past `_bundle_errors` (which catches only `OSError`), and failed a
capture that had already rendered. Non-finite values are now stopped at the
bridge, `bool` is excluded from the numeric check (`true` had become a
one-second step), strings are length-bounded, and the step and embed counts are
capped — all because those numbers chose how much work a capture did.

**A guard written for one path, inherited by another it does not fit.**
`rewrite_back_links` walked every page of the merged document. On the protected
path the base pages are OCR'd images and can carry no link, so scanning them
was harmless. A *captured web page* can carry any `<a href>`, including one in
this marker's own reserved scheme — rewriting the reader's outbound link into
an internal jump, or failing the capture outright if it pointed past the page
count. The rewrite now starts at the first appendix page.

**A fetch that inherited a session but not a policy.** Covered in P7-6; the
review added the other half of it. `context.request` also carries the capture
context's cookies (measured), so an unrestricted download was a credentialed
read of any host the signed-in user is authenticated to, written into the
bundle and embedded in the PDF they share. A playbook may now only name assets
on the service it was read from. The per-file cap also bounded nothing on its
own: refused bodies were not charged to the per-capture budget, so a playbook
of oversized steps ran straight past the ceiling that existed for it. Response
bodies are now disposed rather than held for the run.

**Markdown treated as inert.** Playbook prose went into `content.md` unescaped:
a bracket ends alt text early, and a leading `#` injects a heading — which is
also an anchor the section placement would later mistake for the page's own.
Relatedly, `_section_end` matched `#` inside fenced code, so a shell snippet's
comment counted as a section boundary and whole walkthroughs were spliced
*inside* the code block.

**A promise made at one boundary and not the next.** The bundle's chunks carry
playbook id, step and timestamp — and `mcp_server` copied six fields and
dropped `meta.video`, so an agent querying over MCP got the walkthrough's words
with no way to cite them. That is the feature's stated acceptance criterion,
true on disk and false over the tool surface. Video assets were likewise
unlistable and unreadable while the chunks referencing them served fine.

**Silence where rule 5 requires speech.** `--no-ai-bundle` and
`--protected-viewer` skipped enrichment with no warning at all, so a capture of
a page whose procedure lives in a player reported `videos: true`, no warnings,
and no mention of the video — indistinguishable from a page that had none. And
`detect_media_videos` still swallowed its own failure, making the warning added
to report it unreachable.

**Claims that outran the code.** `shared_with` was documented as naming another
video's download and was set to the record's own playbook; `assets` counted
records rather than files; the bridge's docstring said a second provider was
one file's work when three files name Guidde. Each is now either fixed or
stated accurately.

### P7-11 — a fixture that could not fail

The golden's stand-in rewrote every screenshot URL to one path, so the download
cache collapsed all eleven steps onto a single file — and a golden recorded
that way cannot distinguish correct per-step mapping from an implementation
that points every step at the first screenshot. It also *pinned* the
`shared_with` bug, because with one file the wrong value looked plausible.

The stand-in now generates a distinct valid PNG per URL, and the golden records
eleven files with eleven checksums. The general lesson: **a fixture whose
values are interchangeable cannot detect a bug that interchanges them.** The
same reasoning applies to the `video` corpus case, which recorded neither
`report.json` nor `stderr` and so pinned neither the counts nor a single line
of what enrichment said; it records both now.

### P7-12 — re-reviewing the fix pass, and what that found

The fix commit for P7-10 was itself reviewed before merging, and that pass
found nine more — **five of them introduced or left half-done by the fixes**.
Worth recording because the ratio is the point: a large corrective commit is
not a safe commit, and reviewing the corrections found nearly as much as
reviewing the original.

What the fixes got wrong:

- **A boundary applied to one half of a pair.** `rewrite_back_links` learned to
  skip the captured page's own links; `assert_no_marker_links` did not. The fix
  turned a silent hijack of a reader's link into a *hard failure* of a capture
  whose PDF was already built — strictly worse in one direction. Both take the
  boundary now.
- **A guard on inputs that the arithmetic regenerates.** `_number` refused
  non-finite values on the way in, and `offset += duration` made them again:
  three steps of `1e308` sum to `inf`. Clamped as it accumulates, and
  `dump_json` now passes `allow_nan=False` — Python emits bare `Infinity` that
  its own parser accepts and no other language's does, which is a broken
  artifact rather than an odd one.
- **A fence tracker that toggled on either marker**, so a `~~~` line inside a
  ``` block closed it and exposed the `#` comments the fix existed to protect.
  The opener's character is tracked now.
- **An escape in the wrong position.** `\1` is not a CommonMark escape, so
  neutralising `1. Open Projects` shipped a visible stray backslash. Numbered
  openers escape the separator, which is what CommonMark actually defines.
- **Dead code that read as a fix.** `untranscribed_chunks` was written and
  never called, so the "chunks carry the gap too" half of P7-10 did not ship at
  all — and the contract widening that accompanied it was vacuous, passing
  tests only because nothing ever emitted such a chunk.

And three the original review had not reached:

- `permitted_asset_host` guessed the registrable domain as the last two labels,
  which makes `evil.co.uk` and `victim.co.uk` one service — and two unrelated
  addresses `1.2.3.4` and `9.8.3.4` one service too, since an IP's labels are
  no hierarchy. It is a suffix rule now: same host, or beneath it. Stricter
  than a public-suffix lookup would be, and the failure mode of being too
  strict is a named gap in the bundle rather than a credentialed fetch.
- `step.title` and `cue.text` were interpolated raw onto lines WebShot
  composes, so a title carrying `\n\n## …` injected a heading the section
  placement would then mistake for the page's own — the same defect the
  instruction escaping had just closed, arriving through a field nobody had
  classified as prose.
- MCP's `_asset_records` fed video-asset records to `VisualAssetRecord`, which
  fails ten validations and falls to the lenient path — silently dropping the
  playbook and step that make a still identifiable. The reader now returns the
  published result shape directly, so a record's provenance cannot be lost in
  a model conversion on the way out.

**The lesson worth carrying:** every one of these is a fix that was correct
about *what* to change and wrong about *where the change ends* — one half of a
pair, the input but not the accumulation, the writer but not the reader, the
instruction but not the title. When a defect has a boundary, the review after
the fix should ask what else sits on that boundary.

### P7-13 — a hash of the path the repository happened to sit in

CI failed `embedded-media` on two chunks whose `text` was **byte-identical** to
the golden. Every meta field matched. Only `meta.sha256` differed.

The digest is taken over the chunk's text as written, and an untranscribed-media
note ends `Source: file:///Users/…/tests/fixtures/sample-video.mp4`. The harness
masks that path to `<REPO>` *in the text* and had no reason to think anything
else carried it — but the hash was computed before masking, so the recorded
value was a hash of the recording machine's checkout. Confirmed rather than
inferred: hashing the golden's text with `<REPO>` substituted back reproduces
the golden hash exactly.

No earlier chunk hit it because no earlier chunk text embedded an absolute
path. The untranscribed-media notes are the first, so it arrived with the
`<audio>` widening — and it is P7-12's shape once more: a value masked in one
place with a derivative of the unmasked value left in another.

**The fix is the property, not the value.** Masking the digest alone would have
exempted exactly the two chunks that exposed the problem, and an exemption is
how a genuinely wrong hash gets through later. `chunk_digest_failures` instead
asserts, on the raw output before any normalization, that every chunk's
`sha256` is the digest of its own `text` — every chunk, every machine — and the
recorded value is then masked. Nothing is lost: the text is still byte-compared
and the assertion proves the two agree.

The general rule now in CONTRIBUTING: **never record a value derived from
content the harness normalizes.** A digest or a length over unmasked text is a
fact about the recording machine. Assert the derivation instead.

### The blind spot itself, which is the more useful finding

`verify.py` cannot see this class **by construction**. It compares this checkout
against goldens recorded from this checkout, so any path-dependent value agrees
with itself. "Everything passed in 230s" was true and uninformative — the same
shape as P6-2, where a green local `pytest` compared no goldens at all, and
P6-3, where a byte count moved with the recording machine.

Standing the tree up somewhere else is all it takes, and it is now a step:
`tools/golden/second_path.py` runs the corpus from a throwaway worktree, and
`verify.py` includes it in the full pass. It costs about ninety seconds — a real
tax, and smaller than the round-trip it replaces, which this project has now
paid twice.

Two things nearly went wrong wiring it, both worth remembering. Ruff reformatted
the new call in `record.py` into a chain of unary-plus expressions, which would
have silently dropped that assertion *and* the existing structure check —
caught by lint failing on an unrelated constant beside it, not by any test.
And `git stash create` is what carries uncommitted work into the second
checkout: it writes the commit objects and returns a hash without touching the
index, the working tree, or the stash list, so the check measures what is about
to be pushed rather than only what is already committed.

### P7-14 — the environment fingerprint omitted the fonts, which is what moved

Three weeks after the goldens were recorded, the full pass failed **twelve of
twenty-six cases** under the heading:

```
golden profile: strict (environment matches the recording)
```

That line was true of every dimension the fingerprint held — platform, Python,
Playwright 1.62.0, Tesseract 5.5.3 all identical — and false about the one that
had changed. `PROFILE_KEYS` was `("platform", "playwright", "tesseract")`. The
fonts were not in it.

**What actually moved.** macOS rewrote `/System/Library/Fonts` on 2026-09-03,
`Helvetica.ttc` among them, which is what `sans-serif` resolves to. The corpus
rasterizes text through Chromium: `professional_page.html` draws its chart with
`context.font = 'bold 22px sans-serif'`, and `chart-source.svg` is rasterized
the same way. Every recorded raster digest was a digest of glyphs drawn with
August's fonts.

**The evidence, because "the fonts changed" is a guess until it is measured:**

| observation | what it rules out |
|---|---|
| three consecutive runs produced the *same* new digest | nondeterminism |
| the Chromium build is 1234, installed 2026-08-19, before the recording | the browser |
| the only commits since touch `journey/cli.py` and `journey/walk.py` | WebShot's own rendering |
| OCR read the identical string in every case; only `confidence` moved, by hundredths | a real content change |
| **rasters containing no text did not move at all** — `captions-read`, `embedded-media` and `protected-viewer` passed with their rasters intact, while every case whose raster carries rendered text failed | anything but the glyphs |

That last row is the one that decides it. A browser or pipeline change moves
every raster; a font change moves exactly the ones with text in them.

**The fix is the property, not the value** — P7-13's lesson, one artifact over.
A digest over pixels a font drew is a fact about the recording machine in the
same way a digest over an unmasked path was. So the portable profile masks it,
and `asset_digest_failures` asserts, on the raw output before any
normalization, that every visual asset's `sha256` is the digest of the raster
it names. Masking without that assertion would have been the P8-42 shape
exactly: a bundle published with a checksum that does not describe its own file
would then pass every profile, and verifying a handed-over bundle is the one
thing those fields exist for.

Three things fell out of it, none of which were the reported failure:

* **The fingerprint now records the fonts** — a digest of name and size across
  the system font directories, not of their bytes, because size changes when a
  font is rebuilt and hashing hundreds of megabytes answers the same question.
  A dimension the recording never wrote now counts as *drift* rather than a
  match: treating "unknown" as "same" is how a dimension that moves output
  stays invisible for as long as nobody adds it.
* **The legacy bundle was never covered by the portable profile.**
  `_OCR_BEARING_ARTIFACTS` listed `bundle/content.json` and
  `bundle/assets.json` and not `bundle/legacy/content.json`. Its OCR *text* was
  being scrubbed incidentally, by the fragment pass that replaces recognized
  strings wherever they appear, so the gap read as covered; its `confidence`
  and its raster digest were never masked at all. `article-legacy` was the last
  case still failing after the first fix, which is how it surfaced.
* **One map, two readers.** `_raster_records` is the single description of
  where a raster digest lives, shared by the masker and the assertion, for the
  reason `_visit_ocr_fields` already documents: a collector and a masker that
  each carry their own copy of the layout drift apart silently, and then the
  profile masks a field nothing checks.

*Corrected by P18-1 and P18-2:* the artifact list was not shared. The
assertion kept its own two-file list, so the legacy raster digest this entry
added to the mask was masked and never checked. The fingerprint read only each
directory's top level, which is `"none"` on Linux, where every font is in a
subdirectory.

**Parity failed the same way, and needed a different answer.**
`tools/parity/check.py` scores a v3 capture against a *frozen v2 baseline*, and
it has no portable profile — its baselines were recorded long ago, on fonts
that no longer exist and were never fingerprinted, so a masked-in-portable-mode
escape was not available. Its `asset_hashes` metric was a multiset of raster
digests at threshold 1.0, which scored **0.00%** on nine cases: one digest
missing, one unexpected, the asset itself untouched. Every other metric on
those cases read 100%, including both text-coverage metrics.

The digest was doing duty as an *identity*, and it is not a stable one. What
the metric actually owes the suite is what its two `strip-asset` mutations
demand: that no visual asset is lost. So it now keys on `(kind, width, height,
alt)` — authored or laid out, never rendered — and is named `assets` rather
than `asset_hashes`, because a metric that no longer hashes should not say it
does. Checked before changing it: that identity is **byte-identical between
every v2 baseline and its v3 capture** across all ten asset-bearing cases, and
`bundle-rasters.json` (format, width, height, mode) compared equal through the
font change while every digest differed.

The threshold stays 1.0 — this is not a loosening, and `parity.toml` says a
loosening needs written justification. All twelve `strip-asset` mutation tests
still fail the metric, which is the calibration that makes it worth anything.
The property the digest used to carry has not been dropped either; it moved to
where it is actually checkable, inside a single capture, as
`asset_digest_failures`.

*Corrected by P18-5:* it is a loosening. The new identity never reads pixels,
so a wrong raster at the right kind, size and alt text scores 100%, and
`asset_digest_failures` proves only that a digest describes its own file.

**What this costs, stated plainly.** The corpus now runs *portable* on every
machine, because no recording carries a font fingerprint yet, and portable
compares less. It still byte-compares every text surface, the chunk stream, the
accessibility tree, the PDF text and `bundle-rasters.json`'s format and
dimensions, and every assertion runs on every profile. Strict returns the next
time the goldens are recorded, and a font update after that will flip to
portable *and say which dimension moved* rather than printing twelve
unexplained diffs under a heading claiming the environment matched.

## The Codex review backlog — P8

A ChatGPT/Codex GitHub integration had been reviewing every pull request since
PR #1, and I had not been reading its comments: **94 comments across twelve
merged pull requests**. This section records what triaging them found. The counts, so the
shape is visible before the detail:

| | |
|---|---|
| Comments | 94 (the estimate was ~87; PR #20 had 20, not 17) |
| Distinct defects behind them | 88 — six findings were reported twice, on PR #9 and again on PR #11 |
| Already FIXED before this branch | 11 comments |
| WONTFIX | 1 comment (P8-69) |
| LIVE | 82 comments = **76 distinct defects**, of which **74 are fixed here** |
| LIVE and deliberately not fixed | 2 — P8-70 and P8-74, each with its options written out |
| Found while fixing, not in any review | 4 — P8-39's second half, P8-42's true scope, P8-53's second half, and P8-75 (which is therefore *not* counted in the 88) |

The six duplicates are worth naming, because they are the same defect seen
twice by the same reviewer six weeks apart and are easy to double-count:
`service.py` output-name collision (P8-54), the PDF/bundle publish rollback
(P8-55), `file:` subresource confinement (P8-11), `query_chunks` paging
(P8-63), the `--require-ocr` language preflight (P8-44), and the argparse
failure report (P8-73).

### P8-0 — what the pattern was

Not "94 bugs". Reading them together, almost every LIVE finding is one of four
shapes, and naming them is more useful than the list:

**A guard written against the wrong noun.** `is_internal_url` classified
schemes and called `file:` safe; `unavailable_reason` asked whether Tesseract
exists rather than whether it can read the requested language; `guard_archive`
summed one central directory when the converter expands recursively;
`alternatives()` split a grammar with a delimiter. Each guard was present,
tested, and answering a question next to the one that mattered.

**One fact, two surfaces, allowed to disagree.** The README claimed structure
tags while `manifest.pdf.tagged` said false; `transcript_provenance` said
`authored` while its sibling prose said unverified; docs/06 named a repository
variable that had been renamed; the QA report carried warnings the bundle's own
manifest did not. Every one of these is a value written twice instead of
derived once.

**A check that could not fail.** `tools/verify.py` skipped four of its ten
steps and printed "Everything passed"; the veraPDF job omitted `--strict` so
`continue-on-error` never had a failure to soften; the golden harness
recomputed manifest checksums before comparing them; the perf job's baseline
was incomparable on the runner it moved to. This is the most expensive shape,
because it is indistinguishable from working.

**An escape hatch that was documented and not implemented.** `gh variable
delete CI_RUNNER` left half the matrix queued; `config.sh remove` was
documented against the registration-token endpoint; deleting a held lock was
advised by the message you see only when the lock is held.

### P8-1 — a fork pull request could run on the maintainer's Mac

*Historical: this entry concerns the private repository's self-hosted runners, which this copy does not use.*

`.github/workflows/ci.yml` had `on: pull_request` and an unconditional
self-hosted `runs-on`. The go-live checklist (docs/12, which this public copy
does not carry) says to remove the runners before going public; that is a
step someone has to remember, and the consequence of forgetting is
arbitrary code execution on a personal machine from an approved fork PR that
edits the tests or the golden scripts.

Made structural instead: a pull request whose head is a fork routes to a
GitHub-hosted runner whatever the variables say. On a private repository no
such pull request can exist, so the change is inert today and the hole is
closed before it can open.

### P8-2 — the single switch was not single

*Historical: this entry concerns the private repository's self-hosted runners, which this copy does not use.*

`vars.CI_RUNNER` was advertised in three places as the one deletion that
returns CI to hosted runners. The Linux legs read `vars.CI_LINUX_RUNNER`
independently, and those containers run on the same Mac — so the one action
taken when the machine is off left half the test matrix queued on it.

`CI_LINUX_RUNNER` is now read only when `CI_RUNNER` is set. docs/06 also still
named `MACOS_RUNNER` and claimed Linux stayed GitHub-hosted; both had been
untrue since the Linux runners landed, which is what made the documented
recovery a no-op.

### P8-3 — the license gate covered one extra out of six

The `licenses` job installed `--extra dev` and then inspected the *installed*
tree, so `mcp` and `docs` — added to `pyproject.toml` without being added here
— were never checked. Spec §6 invariant 9 says the installed tree contains no
GPL/AGPL/SSPL package and this job is the only thing that verifies it. Now
`--all-extras`.

### P8-4 — the conformance gate could not fail

`tools/verapdf/check.py` returns 0 for a non-conformant report unless
`--strict` is passed, and the workflow did not pass it — so
`continue-on-error: true` never had a failure to make non-blocking. The file's
own comment fifteen lines above calls a gate that cannot fail worse than no
gate, "because it looks like evidence". It was describing itself.

### P8-5 — the perf job could not detect a regression

The budget is 3× a baseline recorded on Linux/x86_64, and
`Environment.comparable_to` declines to judge a run from a different
environment: it prints `REPORTED ONLY` and exits 0. Pointed at the self-hosted
Mac, that is every run. Moved to the Linux runner the baseline came from.

### P8-6 — the decommissioning procedure did not work, in two files

*Historical: this entry concerns the private repository's self-hosted runners, which this copy does not use.*

`config.sh remove --token` needs a **removal** token; the go-live checklist called the
**registration-token** endpoint, whose token `config.sh remove` rejects. The
procedure therefore ran `svc.sh uninstall` and failed on the next command,
leaving the runner registered with its credentials on disk — a half-removed
runner that looks removed. It is the procedure that must complete before the
repository goes public.

The same wrong endpoint was in the runner setup script's own removal comment
(`tools/ci/add-runner.sh`, not in this public copy), which is the copy that
travels to other repositories. Fixing only the one the
review anchored to would have been P7-12's shape exactly: one half of a pair.
`grep -rn 'registration-token' .` before calling this kind of thing done.

### P8-7 — the license gate approved a mandatory GPL term

`alternatives()` split the license expression at every ` OR `, so
`GPL-3.0-only AND (MIT OR Apache-2.0)` became the fragments
`GPL-3.0-only AND (MIT` and `Apache-2.0)` — and the second names nothing
denied, so `is_denied` returned false for a package whose GPL obligation is not
optional.

`OR` binds looser than `AND` and parentheses override both. That is a grammar,
and a grammar cannot be parsed with `str.split`. Replaced with a small
recursive-descent parser: a conjunction contributes one alternative and is
refused whole if any operand is refused.

### P8-8 — a password's suffix reached `accessibility.yaml`

`_redact_password_values` replaced each captured value in turn over one string,
in DOM order. A form holding `secret` in the current-password field and
`secret123` in the new one had the shorter value replaced first, leaving
`[REDACTED PASSWORD]123` — after which the pass for `secret123` matched
nothing. The published artifact carried the distinguishing half of a password
while looking, to a reader and to an absence assertion checking whole values,
exactly like a successful redaction.

Longest-first is what makes sequential replacement equivalent to simultaneous
replacement here. Spec §6.2 is a MUST, so a partial leak is not a tradeoff.

**The fixture is a separate page, and that decision is the interesting part.**
The obvious move was two more fields on `form_page.html`. It broke `parity`:
that case has a frozen v2 baseline, v2 no longer exists to re-record it, and
the harness would have gone on comparing a v3 capture against a v2 capture of a
*different document* — a green-looking comparison of two unrelated things,
which is worse than a red one. `form-overlapping-passwords` has no baseline and
`parity/check.py` skips it with a stated reason, which is the honest outcome
for a fixture that postdates v2. Generalisable: **a fixture with a frozen
baseline is append-only.**

### P8-9 — a spreadsheet cell could fire a network request

CSV cells were escaped for `|` and newlines. Markdown passes raw HTML through,
so `<img src="https://tracker/x">` in a cell became a real element and
rendering the document fetched that URL — a beacon, or an internal address,
chosen by whoever wrote the file. The sanitizer does not stop it and was never
meant to: `img[src]` is in `MARKDOWN_POLICY` because Markdown may legitimately
emit an image, and the allowlist governs what *Markdown* emits. This text is
not Markdown; it is data being rendered through Markdown.

Cells are HTML-escaped and their Markdown openers backslash-escaped. The v2
renderer had this property and the move to MarkItDown lost it, which is the
recurring cost of adopting a component: the behaviour you were relying on may
not have been the component's to keep.

### P8-10 — WebSockets bypassed the private-network guard entirely

`context.route` governs HTTP; a WebSocket handshake is routed separately and
was ungoverned. A captured public page opening `ws://127.0.0.1:PORT` could
exchange data with an internal service and copy the replies into the DOM while
`final_url` stayed public and the capture published. `ws`/`wss` are now
classifiable schemes and `route_web_socket` applies the same rule.

### P8-11 — an MCP capture could read any file the user could

Measured, not theorised. `check_source` confines the top-level document to the
configured roots and nothing confined what that document then referenced;
`is_internal_url` returned False for every non-HTTP scheme. A probe with a
plain Playwright context confirmed both halves: `file:` subresources **are**
routed, and an out-of-root image loaded with `naturalWidth == 40`.

`CaptureOptions` carries the roots and the request gate refuses `file:` outside
them, resolving symlinks first. Unconditional rather than gated on
`--block-private-requests`: that flag is about addresses.

### P8-12 — the output lock could truncate an unrelated file

The lock path is predictable and output directories can be shared. Another
local user could pre-create it as a symlink; `os.open` followed it and the pid
write truncated the target. `O_NOFOLLOW`, plus an `fstat` regular-file check on
the descriptor already held so nothing can be swapped underneath.

*Corrected by P10-24:* `O_NOFOLLOW` is POSIX. Named unguarded, it made every
Windows capture exit 5 at the lock from this fix onward. Where the flag is
missing, the lock now gets the same guarantee from `O_EXCL` and a check after
the open.

### P8-13 — the message advised deleting a lock the kernel had just confirmed held

Unlinking a held lock lets the next run create and lock a *different* inode
while the first keeps publishing — the interleaving the lock exists to prevent.
The recorded pid can also be stale during a handoff, so "no run is in progress"
is not something the file can be read to mean. The advice is gone; the path is
still named, because it is the only part of the message that distinguishes
"another run holds it" from "this run collided with its own staged copy", and
a test depends on telling those apart (P5-10).

### P8-14 — a full disk could leave an MCP server holding a lock forever

The descriptor was appended to the instance's list *after* the pid write, so a
failing `ftruncate`/`write` left it open and locked with nothing tracking it. A
CLI process drops it at exit; a long-lived server refuses every later capture
of that destination until it restarts. Registered before the writes now.

### P8-15 — a credential in an MCP source URL was logged, filed and published

`check_http_target` reads the hostname. Userinfo passed through into the log
line, the derived filename, the manifest and the summary — a password in four
places on disk and in the agent's transcript, against a boundary that promises
no credential-bearing parameters. Refused rather than stripped: an
unauthenticated fetch answers a different question from the one asked.

### P8-16 — a 688-byte archive expanded to 200 MB

`guard_archive` read the outer central directory and MarkItDown converts zip
members recursively, so a nested archive was charged as its *compressed* size
and then decompressed anyway. Budgets are shared across three levels of
nesting; deeper is refused rather than guessed at. Verified with a real bomb.

### P8-17 — the asset cap was applied after the body was read

`APIResponse.body()` buffers the whole response in the driver process and
Playwright's request context has no streaming read — which is the API this
download uses precisely *because* it carries the capture session's cookies. So
a server omitting `Content-Length` and answering with an endless chunked body
exhausted memory before `MAX_ASSET_BYTES` was consulted, and these URLs come
out of the intercepted playbook.

An undeclared length is refused before the read. **What that does not close**,
written down rather than implied: a server that declares a small length and
sends a large body is still read whole. What bounds that case is the
60-second timeout, the per-capture byte budget, and `permitted_asset_host`.
Closing it means not using `APIRequestContext`, which means reproducing the
session cookies on a streaming client — a larger change than a review pass, and
recorded so the next person meets it as a decision rather than a surprise.

### P8-18 — `transcript_provenance` was a constant

`extract/guidde.py` recognises narration that is not `textToSpeech` and calls
it "unverified territory", setting `unverified_narration`. The manifest record
went on declaring `transcript_provenance: "authored"` regardless, with only the
sibling prose disagreeing — so a consumer filtering the structured field, which
is what a structured field is for, was told the subtitles were authoritative in
exactly the case the parser had flagged. The contract gained `"unverified"` and
the value is derived from the same fact the prose is.

### P8-19 — `content.html` was the one always-written file the PDF did not carry

`create_ai_bundle` always writes and checksums it, and `docling_bridge` warns —
in the bundle's own warnings — that a caption it could not re-attach to a
nested table "is preserved in content.html". A consumer handed only the PDF,
which the README and the product documentation now present as a supported way
to receive a capture, lost data that exists nowhere else in the file.

### P8-20 — video assets embedded as `application/octet-stream`

`--video-assets --embed-assets` gathers `.mp4`/`.webm` into the PDF and
`_asset_mime` knew only image suffixes.

### P8-21 — the README claimed tags the user had disabled

Under `--no-tagged-pdf` the embedded README still said the captured pages carry
structure tags while `manifest.pdf.tagged` correctly said false. Two provenance
surfaces in one deliverable contradicting each other, precisely when the user
had asked for the thing one of them asserted. Both now read one measurement,
taken once before the attachments are added.

### P8-22 — the embedded provenance did not name the OCR engine

`tool_versions` lists what is *installed*, which under `--ocr-engine rapid`
names two recognizers and identifies neither as the one that produced the text.
The sidecar manifest already derived the actual engine; the embedded
`capture.json` did not.

### P8-23 — a JSON pointer that resolved to nothing

Video chunks emitted `#/videos/<id>/steps/<n>` — 1-based, and rooted at a
`videos` key that `steps.json` does not have. It resolved at neither end, while
page chunks emit `#/texts/N`, which does. The same field meant an address on
one chunk kind and nothing on another.

**The contract, decided rather than patched.** `doc_items` holds RFC 6901
pointers that resolve in a document the record names: `content.json` for page
chunks, and the file in `meta.video.steps_document` for video chunks. A pointer
without its document is not a reference, and the pair also stays unambiguous on
a page carrying several walkthroughs, where `#/steps/0` alone would not.

`chunk_pointer_failures` asserts resolution rather than recording the string,
for the same reason `chunk_digest_failures` exists: a recorded pointer that
resolves to nothing looks exactly like one that does.

### P8-24 — the Guidde host prefilter was case-sensitive

`getAttribute` returns the author's casing and `https://APP.GUIDDE.COM/...` is
a valid spelling, dropped by the live DOM scan's cheap gate before
`playbook_id`'s own case-insensitive check could see it. The walkthrough was
silently absent from the bundle, the appendix and the tally. The offline
scanner already handled it, which is why the gap survived: **two readers of one
rule, one of them tested.**

### P8-25 — CSS-hidden players counted as captured content

The scan skipped `data-webshot-hidden` — what `--exclude` sets — and not
`display:none`, `visibility:hidden` or zero geometry, which is what a
responsive layout or a preloaded second player uses. Walkthroughs the reader
never saw in the PDF were fetched and appended, and `video_tally` counted
duplicates.

The visibility predicate is now defined once (`VISIBLE_JS`) and injected into
all three stages that ask, the way `LOCATOR_JS` already was. Three copies of a
question is three chances to ask a different one.

### P8-26 — the exclusion list missed the source recording

A page embedding a Guidde playbook *and* rendering that playbook's own source
recording in a `<video>` listed the same file twice — once as a documented
walkthrough and once as `NOT TRANSCRIBED` — corrupting the tally and putting a
contradictory note in `content.md`.

### P8-27 — a two-player page could report `total: 1`

A walkthrough that could not be fetched was dropped from `videos`,
`video_tally`, `content.md` and the embedded README, leaving only a warning.
Detection had succeeded, so the bundle *knew* the video was there and published
a count that said otherwise. It is recorded as an untranscribed video with a
note that does not claim there was no authored walkthrough behind it — there
is one, and it is why the record exists.

### P8-28 — narration with no subtitle cues produced an empty transcript

A step can carry `audioNote.markdown` and no `subtitles`. Parsing sets
`step.narration`, which makes the step non-silent, and every transcript writer
emitted only `step.cues` — so `transcript.txt` held the heading and nothing
under it and `transcript.vtt` held nothing at all, for a step the bundle
counted as narrated. `narration_cues` is the single fallback all four writers
now go through, and `transcript_cues` counts what the file holds.

### P8-29 — the appendix did not match the page it was bound into

It received the paper *name* and not `--landscape` or
`--prefer-css-page-size`, so a landscape capture published a portrait
appendix: one document, two orientations, while the appendix claimed to use
the capture's page box.

### P8-30 — `--no-outline` was ignored when composing the appendix

The walkthrough root, its per-video children and every step bookmark were added
unconditionally, so a capture that asked for no bookmarks got a tree of them
and `manifest.pdf.bookmarks` reported false while the file disagreed.

### P8-31 — the protected path claimed it ignored a setting it used

`--protected-viewer --ocr-engine rapid --ocr-psm 6` warned that `--ocr-psm` was
ignored. That path ignores *RapidOCR* and passes the PSM straight to
OCRmyPDF/Tesseract (`tesseract_pagesegmode`), so the warning contradicted the
setting that actually produced the text layer — in the same list that correctly
said the engine was ignored.

### P8-32 — `--embed-assets` with `--no-embed-bundle` succeeded in silence

The guard on `collect_payloads` meant the flag never ran and nothing said so,
which reads exactly like a capture that embedded the assets. Rule 5.

### P8-33 — `--protected-viewer --no-embed-bundle` embedded anyway

`build_protected_viewer_artifacts` calls `embed_associated_files`
unconditionally while the QA report said `embed_bundle: false`. Reported as
unsupported on that path rather than silently disobeyed: the searchable PDF and
its payloads are assembled together there, so the attachments are part of the
deliverable rather than a step that can be skipped.

### P8-34 — three runners, one package cache, two red CI runs

*Historical: this entry concerns the private repository's self-hosted runners, which this copy does not use.*

Not from the review — it happened during this work and is recorded because the
symptom is the hard part.

Runner instances on one machine share a home directory, so they shared
`~/.cache/uv`. uv's cache does not survive several separate processes writing
the same wheel at the same instant, which is what one push produces when it
dispatches three macOS legs together. Two consecutive runs failed at *Install
the locked environment* with

```
The wheel is invalid: Metadata field Name not found
```

naming a different package each time — `pytest-9.1.1`, then `soupsieve-2.9.2`
and `mypy-2.3.1`. Three jobs red before a line of project code ran, on a
dependency each, which reads as a broken lockfile or a bad release and was
diagnosed that way twice.

**If several runners fail simultaneously on *different* packages at the install
step, suspect the cache before the code.** The fix is one `UV_CACHE_DIR` per
instance in its `.env`; the runner setup script (not in this public copy) now
writes it, so a new machine does not rediscover this.

The Linux containers were never affected, and the reason is the lesson: their
image sets `UV_CACHE_DIR` *inside the image* and
each container has its own filesystem. The container path was right by
construction and the macOS path wrong by omission — which is most of this
document.

### P8-35 — `permitted_asset_host` is narrower than "Guidde", and that is fine

Not a defect; a boundary worth knowing. Measured against
`tests/fixtures/guidde/quickguidde-sample-playbook.json`, every URL
WebShot actually fetches is on `storage.app.guidde.com` and passes. The same
response names two hosts the rule refuses: `static.guidde.com`
(`docPublicScreenshot` ×9, `docCoverPublicScreenshot` ×1) — a *sibling* domain
outside `app.guidde.com`'s subtree — and `storage.googleapis.com`
(`magicCapture.captureType.imageUrl` ×1).

Nothing breaks today because nothing reads those fields. It is recorded because
of where it bites next: `docPublicScreenshot` on the public CDN is the obvious
fallback when `drawnScreenshot` returns 403 on a non-shared playbook, and
taking that fallback without widening this rule loses every screenshot to a
per-asset warning. Widening it is a real decision — a second permitted host is
a second host a hostile playbook can aim a credentialed fetch at — so it
belongs in the change that wants it. The docstring's claim was corrected to
what was measured.

### P8-36 — a repeated heading took the first section every time

`_section_end` matched on normalized text and returned the first hit, so a
later section's walkthrough was spliced under an earlier, unrelated one.
Occurrence counting fixes the common case; the residual is named in the code:
two videos both under the *second* of two identically-titled sections still put
one under the first, because the only positional signal available is order —
the player element is not in the snapshot at all, so there is no line in
`content.md` to map a DOM locator to.

### P8-37 — a malformed asset URL failed a capture that had already rendered

`urlparse` raises `ValueError` on `http://[`, and both classifiers parse the
URL *outside* the request `try`. Worse than the review said: `permitted_asset_host`
raises too and is called first, so the crash did not need
`--block-private-requests` to be on. A URL this code cannot parse is one it
cannot vouch for, so it is refused — the documented per-asset degradation.

### P8-38 — the full-page warning fired on an explicit selector

`--selector` with `--auto-selector` returns the explicit selection, and the
warning was gated on the *flag* rather than on the strategy — so the QA report
and the manifest said no region was detected, of a capture that had isolated
exactly the region the caller named.

### P8-39 — a locator that named no element, twice

`body > html > head > meta` for an `og:video` embed: `html` and `head` are not
descendants of `body`, so the manifest's video locator resolved to nothing.
That is the finding.

Fixing it surfaced the same break for a case the review did not mention and
which is far commoner: `body > p#para` for a `<p id="para">` inside a `<div>`.
The walk stops at the first id and drops the ancestors above it — the intended
shortening — but `>` then asserts a parent-child relationship those dropped
ancestors deny. `querySelector` returns null. Five locators in the recorded
corpus were of this shape.

Fixing only the head case would have left the contract false for the commoner
one, which is the half-fix pattern this document keeps recording. A browser
test now asserts the property — every locator finds its own element — rather
than any particular string, because a wrong locator is perfectly plausible in
a recording.

### P8-40 — the protected bundle's manifest omitted the run's warnings

`build_protected_viewer_artifacts` writes and publishes the manifest from
inside its own call, and the pipeline merged the ignored-option warnings into
its list *afterwards*. They reached the QA report and the log and not the file
— so a bundle whose OCR engine was silently swapped said nothing about it, and
a consumer holding only the bundle could not tell.

### P8-41 — the protected path reported four stages as one

OCR, bundle, render and publish all happen inside one call, between `capture`
and `package`. The QA report attributed every second of recognition and
rendering to packaging and showed no `extract` or `render` at all, for a run
that unmistakably did both.

### P8-42 — the golden harness could not see a wrong checksum

`normalized_snapshot` **replaces** every manifest `bytes`/`sha256` with values
recomputed over the normalized text. It has to: it masks OCR output and
absolute paths, so a digest of the raw file could not match on a second
machine. The consequence is that the published values were discarded before
comparison — so a capture emitting a checksum that did not describe its file
passed both profiles, and the one thing a consumer uses those fields for was
the one property the corpus could not check.

This is P7-13's lesson applied one level up: **normalize for comparison, assert
the derivation.** `manifest_digest_failures` checks the published values
against the files as written, before normalization touches them, and five
tests plant the defects it must catch.

### P8-43 — `webshot doctor` crashed in the environment it diagnoses

Both probes caught `ImportError` only. A broken install does not fail politely:
a compiled extension that will not load raises `OSError`, an upstream
initializer raises `RuntimeError`, and `version()` raises
`PackageNotFoundError` — which sat *outside* the guard entirely, as did the
`acquire.adapters` import that pulls MarkItDown, Markdown and defusedxml in at
module load. Each propagated as a traceback and Python's exit 1 from the one
command contracted to return a failed check and exit 9.

### P8-44 — `--require-ocr` could exit 0 having recognized nothing

`unavailable_reason()` asked whether the binary exists, not whether it has the
requested language pack. `--require-ocr --ocr-language spa` therefore passed
the preflight on an English-only install; Tesseract then failed per image,
`capture_visual_assets` turned each failure into a warning, and the run
succeeded with no text — the exact outcome the flag exists to prevent, where
the taxonomy says an unavailable required engine is an early exit 6.

Availability now includes the language, asked once. An unlistable Tesseract is
deliberately *not* read as having no languages: refusing a capture because
`--list-langs` did not answer would be worse than the gap it closes. And the
runtime half is fixed too — a recognizer that fails under `--require-ocr` now
raises instead of warning, because the preflight can only answer "could it
run", not "did it".

### P8-45..P8-47 — three assumptions in one function

`_capture_portrait_viewer`, all three of which the review found:

**P8-45, the scroll target.** `pages[0].y` and `viewer.y` are both
viewport-relative, so their difference is the page's offset *in the current
scroll state* — written straight to `scrollTop`, a document coordinate. A
viewer restored to a nonzero scroll, or scrolled during interactive
authentication, shifted every page: each stitched page carried its neighbour's
whitespace and lost part of itself. `_viewer_metrics` already reported
`scrollTop`; nothing used it.

**P8-46, the units.** The context sets `device_scale_factor=2` and
`page.screenshot` defaults to device-scale output, so each segment PNG is twice
as tall as the CSS rectangle it was clipped from — while `page_height` came
from that rectangle. `stitch_segmented_pages` compared 1100 against 1800 and
raised "stitching is unnecessary", on **every multi-page portrait protected
capture**, at the scale factor this code sets itself. The existing stitch test
passed synthetic images with consistent units and so could not see it: P7-11
again.

**P8-47, the page count.** A single-page letter or form exposes one rectangle,
so there is no spacing sample — and the check for one aborted the capture
rather than noticing that a one-page document needs no inter-page spacing. A
page that fits inside the viewer is now shot once rather than stitched from two
segments that would not overlap.

### P8-48..P8-49 — `--ai-bundle-dir` was only tested beside the output

Two failures from the same assumption. `mkdtemp(dir=ai_directory.parent)`
raised `FileNotFoundError` before any artifact was built when the parent did
not exist — the web path creates its destination's parent and the protected
path did not. And the manifest's `"file": "../<name>"` path to the PDF resolves
to nothing from a bundle that is not the PDF's sibling, so `manifest.json` —
whose job is to let a consumer find and verify the artifacts — could not locate
the one it had just checksummed.

### P8-50 — non-finite configured timings

TOML has no `inf`, but `WEBSHOT_DELAY=inf` and a JSON `1e400` both parse to
one, and NaN fails every comparison silently: `nan < 0` is False, so both
passed the sign checks. An infinite delay reaches `asyncio.sleep`; a non-finite
timeout dies inside `round(timeout * 1000)` as an unclassified exception where
the contract says a bad configured value is a usage error.

### P8-51 — a malformed `--storage-state` was exit 5

Existence was checked and usability was not, so Chromium rejected it from
inside `new_context()` as a plain `PlaywrightError` — classified as
capture-integrity failure, for a caller-supplied file that was unusable before
anything was captured. Parsed, not schema-checked: the shape Playwright wants
is Playwright's, and duplicating it here would be a second thing to keep
current.

### P8-52 — *(withdrawn)*

Reserved while the report-collision fix was being placed on `CaptureOptions`;
it belongs to the CLI and is P8-53.

### P8-53 — `--report` naming the output destroyed it

The QA report is written *after* publication, deliberately, so that a report
which cannot be written does not fail a capture sitting on disk. Pointing
`--report` at the output therefore replaced the finished PDF with JSON, and
the command exited 0 — reporting success for a run whose only deliverable it
had just overwritten.

Refused in the CLI rather than on the options model: the report path is not a
capture option and the MCP caller can choose neither path. And the refusal
needed a second half the review did not mention — the refusal *is itself a
failure*, so `_record_failure` was about to write the explanation over the
file it was protecting. Verified with an existing 63 KB PDF: it survives.

### P8-54 — two MCP sources could publish to one name

`sanitize_filename` builds a slug from the host and the last two path segments
and drops the query string, so `?id=1` and `?id=2` — and two local files
sharing a basename — collided. Callers cannot choose an output path and
captures are serialized, so the later one replaced the earlier PDF *and* its
bundle with nothing to notice it by. The slug is kept and a twelve-character
digest of the complete normalized source appended, so the name still reads as
what it captured.

### P8-55 — a failed bundle swap left a mismatched pair

The PDF and its bundle are one deliverable — the bundle's manifest records the
PDF's SHA-256 — and `publish_directory` restores the old bundle on failure
while `os.replace` had already overwritten the PDF irreversibly. The output
root was left with a new PDF beside a bundle describing a different capture,
while the tool reported an error.

### P8-56 — the manifest tool returned page text with neither safeguard

`title`, `page_metadata.description` and `warnings` are whatever the captured
page said. Every neighbouring content tool says UNTRUSTED in its description
and carries a result-level `notice`; `get_manifest` had neither, because it
returned a bare dict. Two of the server's three stated prompt-injection
safeguards absent from the tool whose output a model reads as fact.

### P8-57..P8-58 — veraPDF gave up early and left the last run's verdict behind

A `verapdf` on PATH that is broken, half-installed or hangs ended the candidate
loop before Docker was tried, so a machine with a working container backend
reported conformance as unavailable because of a binary it did not need. And a
run that produced no report left the previous run's JSON sitting beside the new
PDF, looking like its verdict.

### P8-59 — a PDF render failure was exit 5

A `PlaywrightError` out of `page.pdf()` — a browser that crashed mid-print, a
PDF protocol failure — reached the generic branch and was reported as
capture-integrity. The published taxonomy assigns PDF render failures to exit
7, and the distinction is what tells a caller whether the *page* was the
problem.

### P8-60 — `chromium: "unknown"` on every authenticated bundle

`page.context.browser` is `None` for a persistent context — an `--auth-profile`
capture *is* its own context — so the provenance field was defeated precisely
where reproducibility matters most. Read over CDP now, not from the user agent,
which `--user-agent` overrides. Verified: both context kinds report the same
build.

### P8-61 — a configured profile no caller could name

A `[mcp.auth_profiles]` key that does not match `PROFILE_NAME` loaded cleanly
and was advertised by `--print-roots`, while `resolve_auth_profile` refused the
same value before looking it up. Startup reported valid configuration for
something unreachable. Validated against the same pattern the request side
uses, imported rather than restated.

### P8-62 — an additive field made an old manifest invalid

`ViewerContentCounts.ocr_words` was made required while `schema_version` stayed
`1.1`, so every protected-viewer manifest written before the field existed
failed validation against the schema it still identifies itself as — while the
migration guide called 1.1 unchanged. **Additive fields are optional or the
version moves; it cannot be both.**

### P8-63 — every match past the first page was unreachable

`query_chunks` had no offset or cursor, so the scan always started from the
beginning — while the tool's own description said omitting the query pages
through everything. It takes an `offset` counted in *matches*, so a filtered
query pages the same way an unfiltered one does, and returns `next_offset`
matching the convention the byte-paged readers already use.

### P8-64 — a text window too small for one character destroyed it

`_incomplete_tail` shrinks a page off a split character, but when the whole
window *is* one incomplete character there is nothing to shrink to. `limit=1`
on `é` returned the lead byte alone — U+FFFD on that page, U+FFFD on the next
when it resumed mid-sequence, the character on neither.

Growing the read to finish the code point was the obvious fix and is wrong: it
puts a page over `read_markdown_max_bytes`, a bound the server states, and the
existing paging property test asserts exactly that. Text windows below four
bytes are refused instead — refusing a window too small to be usable tells the
caller something true; quietly exceeding a declared limit does not.

### P8-65 — MCP callers could not see what a capture failed to fetch

The pipeline records `failed_requests` and the CLI prints them; the summary
returned only `warnings`. An agent got an apparently clean capture with no sign
that images or frames were missing — including the ones the private-network
filter deliberately aborted, which is the case it most needs to know about.

### P8-66 — OCR precedence worked in one direction

A lower-priority `[ocr] enabled = false` lands on `no_ocr`, and an explicit
`--require-ocr` then meets it as a contradiction — so the file made the flag
unusable. The same inversion of flags > env > file that P5-10 fixed for
`--no-ocr`, read from the other side. One rule, two directions.

### P8-67 — vocabulary is not text

The auto-selector scored candidates on distinct-word coverage. A summary box
naming every word the article uses cleared a 90% bar in a fortieth of the
length; shortest-wins then chose it and `isolate_content` hid the article.
Coverage is now occurrence-counted as well, capped at what the article actually
contains so one word repeated cannot buy it.

The test is built so it *can* fail — verified by removing the check and
watching it select the summary. The first attempt could not: adding "in period
N" to each paragraph gave the article forty words the summary lacked, dropping
its distinct coverage to a third and making the test pass for the wrong reason.
P7-11, encountered while writing the test for something else.

### P8-68 — "Page 1 of 1" on a six-page file

Chromium resolves `totalPages` while printing and the appendix is merged
afterwards, so the footer was not stale — it was measuring a different
document. The total is dropped when a walkthrough is going to be appended,
rather than corrected: the placeholder is resolved inside the browser and the
appendix's length is not known until it has been rendered in a second one.
"Page 3" is true on every page of the composed file; "Page 3 of 4" is false on
four of them.

### P8-69 — `--no-ai-bundle` and video capture: WONTFIX, documented

The walkthrough files are written *into* the bundle and the appendix is
composed from them, so with no bundle there is nowhere for the work to land. A
warning already existed. What was missing is that no documentation described
the behaviour the warning referred to, so a reader who saw it could not look it
up. Now in the bundle guide and the spec's option table, with what to do
instead — capture with the bundle and let `--embed-bundle` carry it inside the
PDF.

### P8-70 — figure chunks for non-picture assets: LIVE, deliberately not fixed

`docling_bridge` seeds a figure chunk only for assets matched to a Docling
`PictureItem`, which means an `<img>` in the sanitized snapshot. `canvas` and
`svg` are converted to `<img>` earlier and are covered. **`video`, `iframe`,
`[role=img]` and CSS-background assets are not**, so OCR text inside one
reaches `assets.json` and never `chunks.jsonl` — invisible to retrieval, which
is what the chunk file is for. The v2 path appended a visual chunk for every
asset.

Two ways to close it and neither is small:

1. **Add the assets to the DoclingDocument as pictures.** Consistent, gives
   them real `#/pictures/N` refs, and puts them in `content.json` and
   `content.md` too — but it changes `content.json` for every capture with such
   an asset, and appends them out of document order because their position in
   the DOM is not recoverable at that point.
2. **A third chunk provenance shape** pointing into `assets.json`, the way
   video chunks point into `steps.json` (P8-23). Cheaper, but it makes
   `doc_items` mean a third thing and needs its own schema and harness support.

Not attempted here. No fixture has OCR text in one of those assets, so there is
no corpus signal in either direction, and a third chunk shape decided at the
end of a long branch is how the next P7-12 gets written. The gap is real and
this entry is the ticket.

### P8-71 — the boring ones

Recorded so nobody reads them again. WONTFIX, with the design each one is
working as: `query_chunks` has no embedding search (v3.0 scope, ADR-0008);
`--legacy-bundle` on the protected path is warned-and-ignored (the format is
unchanged in v3.0); `pdfa` is protected-viewer only (ADR-0010); the appendix
carries no structure tree (S8, and the merge cannot preserve one); protected
`page_width`/`page_height` are device pixels (they describe the images that
were written); `MAX_ARCHIVE_DEPTH` is three (deeper is refused, not guessed);
`installed_languages()` returning empty means "could not ask", not "has none".


### P8-72 — the snapshot recorded the form the page shipped, not the one it showed

`document.body.innerHTML` serializes content *attributes*, not IDL properties,
so a control that JavaScript or the user changed after load arrives in the
inert clone carrying its page-authored default. The clone-side rewrite then
read `.value` off the *clone* — the same default — and wrote it back, which is
why it looked like state synchronization and was a no-op. Checkbox and radio
checkedness and select choice were never carried at all.

The in-browser block extraction twenty lines above reads the **live** DOM. So
`content.html` and the `form-value` records in `assets.json` described the same
page differently, and only one of them was right.

**And the first fix for it was wrong, which is the part worth recording.**
Position looks like the obvious correspondence — the clone is a fresh parse of
the same markup in the same order — and it is not: the filter a few lines above
has already removed hidden and excluded elements from the clone, so one dropped
input shifts every later index and controls receive *each other's* values. A
worse fidelity bug than the one being fixed, introduced by the fix.

Measured against the fixture with a hidden input in it: the positional version
emitted `<input id="notify">` with no `checked`, having read the hidden CSRF
input's state. The correspondence is now an explicit mark written on the live
DOM before serialization, riding through `innerHTML` as an ordinary attribute
and stripped from both documents afterwards. Passwords are still replaced by
the marker rather than copied, decided by the *clone's* own type so it holds
even if a mark goes missing (spec §6.2).

It was caught while writing the "least confident in" list at the end of the
work, not by a test — the tests passed, because the fixture had no removed
control in it until this was noticed. That list has now earned its place twice.

The fixture's script sets every value *away* from its default, so the
assertions fail on the unfixed code rather than passing for the wrong reason.

### P8-73 — an argparse rejection wrote no report

Spec §2.3: a run given `--report` records its exit code and its error, succeed
or fail. `parse_args` raises `SystemExit(2)` from inside itself — before the
namespace exists, and therefore before the failure-report boundary could be
established. So `--scale nope`, an unknown option, an invalid choice, or a
missing source exited 2 and wrote nothing: the shape of failure an automated
caller is *most* likely to hit was the one the contract did not cover.

`--report`'s value is read off raw argv, deliberately forgivingly — this is a
best-effort read for the purpose of writing a diagnostic, and a spelling it
does not recognise means no report, which is where this started. argparse's own
message is captured through an overridden `error` so the report can carry it,
and the `SystemExit` is re-raised untouched: argparse's exit code is argparse's
to choose.

### P8-74 — DNS rebinding: LIVE, and not closed here

`_block_private_requests` resolves the hostname, decides, caches the verdict
per host, and calls `route.continue_()`. Chromium then resolves the name again
itself. An attacker-controlled name that answers public on the first lookup and
private on the second reaches the internal address, and the per-host cache
means later requests to that name skip a fresh check entirely. `final_url` sees
only the hostname, so the post-navigation re-check does not catch it either.

`check_http_target`'s docstring has always said this: *"This cannot close the
gap between this lookup and the browser's own (a name whose answer changes in
between — DNS rebinding)."* The review is right that the guard does not close
it; the docstring is right that this guard cannot.

Closing it properly means the browser must connect to the address that was
validated, not to a name it re-resolves. Two ways, both structural:

1. **`--host-resolver-rules`** at launch, pinning each host to its vetted
   address. The host set is not known before the page runs, so this needs
   either a pre-pass or a relaunch, and it is process-wide rather than
   per-request.
2. **Proxy every request** through a local listener that dials the validated
   address. Correct, and a substantial new component in the capture path.

Neither is a review-pass change, and doing half of one would leave a guard that
looks stronger than it is — which is what most of P8 is about. Recorded as the
known limit, with the two options, so the next person meets a decision. What
does hold today: the pre-check refuses a name that resolves internal at all,
`check_final_url` re-checks after redirects, and `--block-private-requests`
covers every subresource and now WebSockets (P8-10) and `file:` (P8-11).

### P8-75 — `<option>` is allowlisted and stripped anyway

Found while writing P8-72's fixture, not in any review. `option` and `optgroup`
are in `SNAPSHOT_EXTRA_TAGS` and `option` has `{"value", "selected"}` in
`SNAPSHOT_EXTRA_ATTRIBUTES` — and nh3 removes them from the snapshot regardless,
leaving `<select id=… name=…>` with three blank lines inside it. Reproduced on
the pre-existing `form-fields` golden, so it predates this branch by some way.

Not fixed here: it is outside the review being triaged, the authoritative
surface for form state is the `form_fields` record in `assets.json` (which
carries the selected option's text correctly), and changing the snapshot
allowlist moves `content.html` for two golden cases. It is written down because
an allowlist entry that does nothing is exactly the kind of thing that reads as
working — and because P8-72's own test had to say why the select is asserted on
one surface and not the other.

## P9 — recorded in docs/13, not here

The ninth measurement round is **docs/13 P9-1..P9-9**, in
[13-lms-capture-proposal.md](13-lms-capture-proposal.md), rather than in this
file. It measures one journey of one LMS against the proposal that describes it,
and every entry corrects a claim on the page beside it — so it is kept next to
what it overturns.

Pointer, not a copy: there is one record of those findings and it is the one in
docs/13. What it covers — the walk over one completed training journey — is
the hierarchy's true depth (P9-1), what the type badge cannot tell you (P9-2),
the detector needing no new code (P9-3),
navigation and its hazards (P9-4), scale (P9-5), unreadable completion state
(P9-6), text that renders clamped while the DOM holds it whole (P9-7), the
module metadata and the duration label that disagrees with its own video (P9-8),
and an outbound link the fixtures do not model (P9-9). The specification those
findings produced is at the foot of the same document.

## Phase 10 — building the journey enumeration

Four findings from building stage 1 of the walker (docs/13). None is about
Continu: two are about what a test can and cannot prove, one is a guard defeated
by the property it was guarding against, and the last is an intermittent abort
that turned out to be **older than this branch** — its entry is the correction
of an attribution this section originally got wrong.

### P10-1 — the stale-reference hazard is a safety violation, not only data loss

docs/13 P9-4 records that a reference held across an expansion silently
addresses a different node, and the design treats the consequence as a group
that reads as empty. Building the naive implementation to prove the fixture
discriminates showed it is worse than that.

The obvious walker collects the group rows once and clicks them in order.
After the first expansion the row nodes are rebound, so the second handle lands
on the description row and the third lands on a **module row** — and clicking a
module row is the one action the walker must never take. The fixture records
every module opened, and the naive walker opens one.

So the rule "never carry a reference across a click" is not a data-quality
rule. On this application it is what keeps a read-only walk read-only, and the
test that proves it asserts on `window.__opened_modules` rather than on the
outline. A walker can be wrong about its output and wrong about its blast
radius in the same bug.

### P10-2 — a second observation with nothing in between is a value compared with itself

The per-node count assertion is docs/13's, and the first implementation of it
read each group's module count twice: once when the group was expanded, and
again on the next statement. That check cannot fail. Nothing re-renders between
two adjacent reads, so it compares a value with itself while reading like a
verification — the same shape as P7-11's fixture whose values were
interchangeable.

The fix is not a better assertion but a later one: each group's second count is
now taken once **every other group in the track has been opened**, which is a
point the application has had reason to move. That version does fail — against
a stand-in variant behaving like an ordinary accordion that keeps one group
open at a time, the earlier groups come back empty and the counts disagree.

Worth stating as a test-design rule: **a re-read is only a second observation if
something could have happened in between.** Where it could not, the assertion
belongs at a level where it could, or it should not be claimed at all.

### P10-3 — `offsetParent` reports a fixed-position dialog as absent

Found while wiring docs/13 P11-1's completion modal into the walk, and worth
recording because the guard was defeated by the very property that makes the
thing block.

The obvious visibility test for "is a dialog covering the page" is
`element.offsetParent === null`. A `position: fixed` element has **no offset
parent by definition**, so that check answers "no dialog" while the dialog is
on screen intercepting every click. The modal handling therefore did nothing at
all, silently, and the walk failed one step later with a message about an
accordion that would not expand — the right symptom attributed to the wrong
cause.

`getClientRects().length === 0` is the test that holds, and it is now used both
for the dialog and for filtering retired rows out of the accordion read.

The general form is the one CLAUDE.md already names: **a guard covers what it
covers — ask what the thing you are guarding against actually looks like.** A
blocking overlay is fixed-positioned *because* that is what makes it block, so
the one visibility check that fails on fixed positioning is the one check that
must not be used here.

### P10-4 — an intermittent teardown abort, older than this branch

**Resolved by P10-14 (2026-09-27):** onnxruntime's telemetry uploader, racing
its own static destructor. The entry below is kept as it was written. It was
right that the common factor is a native library at teardown and not
Chromium. What it lacked was the stack, and macOS writes one for every
abort.

Recorded unresolved rather than omitted, in the style of P5-10. The attribution
below was **corrected after the first version of this entry**, which read as
though this branch had introduced the abort. It had not.

    libc++abi: terminating due to uncaught exception of type
    std::__1::system_error: recursive_mutex lock failed: Invalid argument

#### Prior art: the same signature at v3.0.0, with no browser anywhere near it

This exact message was seen once during the **v3.0.0 release, 2026-08-21** —
two weeks before the journey work — and the context could hardly be less like
the one below. A freshly downloaded wheel, installed into an empty venv,
running `webshot --version`. It printed the correct version and then aborted
with **exit 134**. Eleven subsequent runs and a full capture from the same wheel
were clean. The cause was never found.

**No browser. No parity. No test suite. No concurrency.** It lived only in the
maintainer's notes and not in this repository, which is why the investigation
below started without it and reached a conclusion this section now corrects.

#### What was measured on this branch

| Configuration | Runs | Aborts |
|---|---|---|
| `webshot --version`, no browser, no suite *(the control)* | 8 | 0 |
| `tools/parity/check.py` alone | 6 | 0 |
| This branch's browser tests, then parity | 4 | 0 |
| Full `verify.py`, master | 6 | 0 |
| Full `verify.py`, this branch, one browser per test | 9 | **3** |
| Full `verify.py`, this branch, one browser per module | 6 | 0 |

**Corrected: it is not parity-specific.** The first three occurrences on this
branch all landed in the **parity** step, always *after* it had finished and
found no mismatch — 71.2 s in the aborting runs against 71.1–71.2 s in the
passing ones — and this entry originally said "always parity" on that evidence.
A fourth occurrence, on 2026-08-24, landed in the **golden corpus** step
instead, after it reported all 23 cases matching. "Always parity" was an
artifact of a three-sample, and the only thing common to all four is the shape:
a step completes its work, finds nothing wrong, and the process dies on the way
out, turning a green check into a non-zero exit.

The post-mitigation tally now reads **1 abort in 10 full runs**, which is close
to the roughly-1-in-12 base rate the v3.0.0 occurrence implies and is the
strongest evidence yet that the mitigation changed a rate rather than removing
a cause.

One confound to know about before comparing any future numbers with these: on
2026-08-23 master gained `pytest -n auto` (commit 710fb10), which took the test
step from ~120 s to ~20 s and changed how much runs concurrently. Every figure
above from before that merge was measured serially.

#### What the numbers do and do not support

**Six clean runs do not clear master.** The v3.0.0 occurrence was roughly one in
twelve on a path with no browser at all. At that base rate, six trials expect
about 0.5 events and return zero **59%** of the time, so "0 in 6" cannot
distinguish *does not happen* from *happens rarely*. Reading the table as
"this branch introduced it" is the mistake this paragraph exists to prevent.

What the branch numbers do support is narrower and still worth having: 3 in 9
against a 1-in-12 base rate is unlikely by chance (about 3%), so **this branch
appears to have raised the rate of something already present**. That is a
different claim from causing it, and the difference is the whole point.

#### The common factor is not Chromium

The two occurrences share almost nothing — a release-wheel smoke test and a
parity run inside a long gauntlet — except this: **a Python process holding
native extensions, exiting, and aborting inside libc++ during teardown.**

`webshot --version` is a fair host for that despite doing nothing. Measured with
`-X importtime`, printing the version string imports **1,823 modules**, among
them nine native-extension roots — `numpy`, `PIL`, `lxml`, `nh3`,
`pydantic_core`, `pypdf`, `playwright`, `regex`, `charset_normalizer`. So even
the control case tears down a large native stack.

The browser-churn hypothesis therefore explains the *rate* plausibly and
**cannot explain the v3.0.0 case at all**, which means it is not the mechanism.
Anyone meeting this next will otherwise re-derive "it must be the browser" and
be wrong, which is the reason this subsection is here rather than a sentence.

#### What was done, and what it is not

This branch's browser tests now share one event loop and one browser for the
module instead of starting the Playwright driver and launching Chromium once
per test — 27 start/stop cycles down to one, and the module from ~38 s to
~22 s. **It is a mitigation, not a fix.** It is worth keeping on its own merits
regardless of whether it touches the abort at all, and it is not evidence that
the cause is understood: no abort has ever been attributed to a line of code.

One further data point, from the other side of the same lesson. The sixth of
the six post-mitigation runs *did* fail, without the abort: its `pytest` step
took **2,134 s** against a normal 136 s, and two tests that shell out — the MCP
stdio subcommand and Tesseract — timed out. That was six full gauntlets run back
to back on a machine that also hosts three CI runners, which is to say **the
measurement starved the thing it was measuring.** It is not evidence about this
branch's code, and filing it as a timeout regression would have been the easiest
wrong conclusion available.

**It can also make a step lie about why it failed.** On 2026-08-25 a
`verify.py` run reported `FAIL golden corpus (second path)` under the message
*"The corpus differs when the repository sits somewhere else, so something
recorded depends on this checkout's path"* — the P6-3/P7-13 diagnostic, which
points at a real and specific class of defect. Run standalone immediately
afterwards the same check exited 0 with all 25 cases matching, and the next full
run was clean. The abort had killed the step and the harness attributed the
death to the difference it exists to detect.

So the rule for anyone meeting this: **when a step fails and `recursive_mutex`
appears in the log, re-run that step alone before believing what it said it
found.** Someone taking that message at face value would have gone looking for
a path-dependent recording that was never there.

CI is unaffected either way: each step is its own job on its own runner there.

### P10-5 — the reads were scoped and the click was not

A multi-agent review of the enumeration returned four defects, on code that had
already been through the naive-implementation discipline of docs/13. All four
were real. What is worth recording is the shape of the worst one and the reason
that discipline did not catch it.

**The defect.** `panel_tracks_js` read a section's track rows from inside that
section's panel. `click_track_js` resolved the row to click with a
document-wide `find`, taking the first match in DOM order. Reading was scoped;
acting was not.

That asymmetry is only reachable because of something measured and welcomed:
P11-5's `allowMultiple` means `_read_section` never re-collapses, so by the
time the walk reaches section N, sections 1..N-1 are still expanded with their
track rows in the document. Two sections sharing a track title — ordinary
courseware naming — put section one's row in front of section two's. Section
two was then recorded holding section one's content, under section two's
synthetic ids.

**Both self-checks miss it, for reasons this design states out loud.**
`diff_outlines` compares two walks that are deterministically wrong in the same
way, which is P10-2's shape at a larger scale. `count_failures` sees
`listed == len(children)` because the right *number* of nodes is emitted; only
what is inside them is wrong. **Identity moved and nothing was watching
identity** — the counts and the diff both watch shape.

**Why the hazard-discrimination pass did not find it.** Every measured hazard
had a naive implementation written against it and a fixture variant to prove
the stand-in could fail. That process is sound and it has a boundary worth
naming: **it proves the fixture discriminates the hazards you listed, not the
ones you had not thought of.** No fixture variant had a duplicate track title
across two sections, so no test could distinguish a scoped click from an
unscoped one. The variant exists now.

The other three were smaller and two of them were the same family this
repository has now written down six times: `--no-verify-determinism` published
`determinism_findings: []`, which is what a *passed* check publishes; and a
group whose panel yielded no module was recorded as an empty group rather than
reported — the likeliest cause being the one selector P11-3 did not measure, in
which case every group reads empty and every check passes on `0 == 0`. The
fourth was half a split: `count_failures` ran on the published walk only, while
`rows()` deliberately excludes `listed`, so a count that disagreed on the second
reading was invisible to both checks.

Fixing the first prompted the question P7-12 asks — what else sits on this
boundary — and the answer was the completion modal's dismissal, resolved
document-wide by the same helper. Pressing the wrong `View Journey Details`
navigates away, so it now resolves inside the dialog. Nobody reported that one.

One thing that came out of the fix rather than the review: **the count findings
were only ever logged.** `outline.json` carried `determinism_findings` and
nothing about whether the walk's own per-node assertions had held, so a failing
outline on disk was indistinguishable from a clean one — the exit code carried
that fact and the exit code is not in the file. `count_findings` is now in the
artifact.

## P10 — the startup footprint of the fast paths

P10-1..P10-5 are the LMS journey-walker round and are recorded on that branch.
This entry is numbered to follow them because it is a sibling of **P10-4**: the
same interpreter-teardown surface, measured rather than reasoned about.

### P10-6 — `--version` imported the whole capture stack, through the package init

`webshot --version` loaded **1823 modules** and all nine native-extension roots
— `playwright`, `pydantic_core`, `pypdf`, `PIL`, `lxml`, `numpy`, `nh3`,
`regex`, `charset_normalizer` — to print `WebShot 3.0.0`, in **1.02 s**. The
same command on a `--extra dev`-only install loaded 1505 in 0.88 s; the 318-module
difference is `ocr-rapid` and `office` being present, which is the first finding
here and the reason the test added with this entry denies **by name and never by
count**. A ceiling would have been a fact about the recording machine, the shape
P7-13 warns about.

#### The line that dominated was not in `cli.py`

`cli.py:24`'s `from .pipeline import convert_url_to_pdf` was the obvious
suspect, and measuring per-module cost with the package `__init__` neutralised
confirms `webshot.pipeline` is worth **1456 of 1467** modules — `.report` 291,
`.config` 179, `.settings` 174, `.bundle.manifest` 173, `.doctor` 73,
`.acquire.router` 27, `.errors` 15, `.version` 3.

But deferring that line would have changed **nothing**, because measured against
the *installed* package every submodule cost exactly the same 1467:

    webshot.version   1467 modules   ['PIL', 'lxml', 'numpy', 'playwright', …]
    webshot.errors    1467 modules   ['PIL', 'lxml', 'numpy', 'playwright', …]

`webshot.version` is one string constant. An identical figure for every module
is the signature of the **parent package**: importing any submodule runs
`webshot/__init__.py`, and that file eagerly re-exported `convert_url_to_pdf`
for spec §7. The cost was one import in the package init, reachable from
everything, and `cli.py` was downstream of it.

Two measurement traps on the way to that number, both worth keeping:

- The first probe neutralised `src/webshot/__init__.py` in a copy and measured
  no change. The checkout root carries `webshot.py`, a shim that shadows
  `sys.path[0]` and re-points at `src/`, so the probe measured the real package
  every time. **A guard covers what it covers** (P7-6) applies to instruments.
- `-X importtime` reports only what the `import` *statement* loaded. A module
  reached through `importlib.import_module` is absent from its output —
  reproducible in one line, `import_module("colorsys")` versus `import
  colorsys` — and that is precisely how the fix resolves its deferred
  re-exports. The test therefore dumps `sys.modules`, which records what was
  loaded rather than how. Measured the other way, the one construction this
  change introduced would have been invisible to the test guarding it.

#### What changed

`webshot/__init__.py` resolves its re-exports through PEP 562 `__getattr__`;
`cli.py` imports each stage inside the function that uses it, *inside* the
`try` that already classifies failures; `config.py` takes `ViewportSize` under
`TYPE_CHECKING`, which is the whole of its Playwright dependency and 179 of the
modules above.

`webshot.py` needed the same treatment and is the reason to check every entry
point rather than the intended one. After the package was lazy, the shim's
`convert_url_to_pdf = _loaded.convert_url_to_pdf` still pulled the stack:
`python webshot.py --version` loaded **1809** modules while the console script
loaded 158.

Medians of five runs of the installed console script, on one machine with the
`dev`, `ocr-rapid` and `office` extras present:

| | modules | native roots | wall clock |
|---|---|---|---|
| `webshot --version`, before | 1823 | 9 | 1.03 s |
| `webshot --version`, after | **158** | **0** | **0.06 s** |
| `webshot --help`, before | 1823 | 9 | 1.02 s |
| `webshot --help`, after | **158** | **0** | **0.06 s** |
| `python webshot.py --version`, before | 1809 | 9 | — |
| `python webshot.py --version`, after | **157** | **0** | 0.06 s |
| `webshot doctor`, before / after | 2285 / 2094 | 9 / 9 | 2.26 / 2.12 s |

`doctor` keeps its nine roots on purpose: its probes import the extraction
stack in order to *report* on it, and its module scope was already stdlib-only.
Its `--json` output is byte-identical across the change, as are `--version` and
`--help`.

#### One behaviour did change, and it had to

Deferring moves an `ImportError` from `webshot.cli`'s import to the first use of
the stage that needs it. That is the better moment, but only if the failure
keeps a code from the §3 taxonomy — and before this it had none: the exception
escaped `main` as a traceback and Python's own exit 1, which §3 forbids
outright. `classify` now answers `ImportError` with **9**, the code that names
the machine as the problem. No working command changes behaviour; a
previously-fatal-at-startup condition gains a correct exit code.

#### What this does not do

It does not fix P10-4, and no claim here should be read as saying it does. The
abort's cause is still unknown. What it does is remove nine native-extension
roots from the control case, so the cheapest path to reproduce P10-4 no longer
holds any of them.

The before-and-after numbers, honestly stated: **1529 runs** of
`webshot --version` before the change and **5000 after**, six-way parallel,
**zero aborts in both**. That is **not** evidence of an improvement. It is
evidence that this environment does not reproduce P10-4 on this path at all,
which the recorded control (8 runs, 0 aborts) was too small to say either way.

The informative half is the *before* number, because those 1529 processes each
held all nine native roots at teardown and none of them aborted. By the rule of
three that puts the per-invocation rate here under **0.2%** (95%); at the
v3.0.0 session's "roughly one in twelve" the chance of 1529 consecutive clean
runs is effectively zero. So 1-in-12 describes that session, not this path in
general.

The after number is a weaker test of the same question by construction: those
processes have no native extensions left to tear down, so they cannot exercise
the surface even in principle. And the conditions differ from the original in a
way this does not control for — v3.0.0 was a freshly downloaded wheel in an
empty venv; this is an editable install with dev extras, on a different machine,
five days later.

What can be said: the change removes nine native-extension roots from the
cheapest path to reproduce P10-4, so anyone investigating it now has a control
case that holds none of them. Whether that matters to the abort is unknown, and
nothing here should be read as closing P10-4.

*Corrected by P10-14:* there were ten. `onnxruntime`, reached through
markitdown's magika, was loaded on this path too and is missing from the list
above. It was the cause of P10-4. The 1,529 clean runs stand and are
consistent with it: the race depends on network timing, not merely on holding
the library.

### P10-7 — a fix made on reasoning, held by nothing

P10-5 records applying P7-12's question — what else sits on this boundary — to
the document-scoped click, and finding the completion modal's dismissal
resolved the same wrong way. That reasoning was right and the fix was right.

**It was also decoration, and an adversarial check found it.** Reverting
`_dismiss_completion_modal` to the document-wide resolver left the entire suite
green. No test named `click_dialog_button_js`, and the stand-in had no control
outside the dialog for a document-wide search to hit — so the scoped version
and the unscoped version were indistinguishable to every check in the
repository.

The same held for two more, found in the same pass:

- `click_named_button_js` matched `textContent.includes(name)` across every
  button in the document. Its only caller is the return to the journey, and its
  safety rested on a comment saying every match does the same thing. It is
  leaf-buttons-only and normalised equality now, and the stand-in carries a
  `Back to Journey Overview` decoy that a substring match takes first.
- `_read_section` captured the section's `aria-controls` once and reused it for
  every track in that section — across a full navigation into a track view and
  back. `aria-controls` is React `useId` output, and `DomContract`'s own
  docstring says such values are not stable across renders, so the code
  contradicted the rule written six lines above it. The stand-in had hard-coded
  panel ids, so it could not have noticed. It regenerates them on every view
  change now, and the walk re-resolves before every click.

Each of the three is now held by a test that fails when only that fix is
reverted — checked one at a time, because reverting any of them also breaks
most of the suite, and "everything went red" does not tell a later reader which
guard they just removed.

**The lesson is narrower than "write tests" and worth stating exactly.** The
enumeration's whole discipline is a naive implementation per hazard, and it
worked: every hazard *someone had named* has a fixture variant that
discriminates. What it does not cover is a fix made from reasoning during a
review pass, because no hazard was named for it and so no variant was built.
P10-5 already said this discipline "proves the fixture discriminates the
hazards you listed, not the ones you had not thought of". This is the same
sentence from the other end: **a fix you reasoned your way to needs a way to
fail before it is worth anything, and the moment to build it is while the
reasoning is still in your head.**

### P10-8 — a fixture a frozen baseline was recorded from is frozen too

Caption path 1 needed one page carrying both a `captions` track and a
`subtitles` track pointing at *different* files, so a test could tell a
transcript from a translation at the level of the text. `media_page.html`
already had both kinds and needed a one-word edit: the audio's `src` from
`captions.vtt` to `subtitles-de.vtt`. Every caption test passed. Four parity
self-tests then failed, on mutations that have nothing to do with captions.

The `embedded_media` parity metric compares a tuple per track that includes
its URL. `media_page.html` is also the input `tests/parity/v2-baseline/
embedded-media` was recorded from — and that directory's README says, in
as many words, that nothing in it may be re-recorded, because the v2
extraction path it came from no longer exists. So the baseline held
`captions.vtt` for the audio, the candidate now held `subtitles-de.vtt`, and
the metric was correct to report a divergence it had no way to ever stop
reporting. The self-test surfaced it as *four* unrelated failures because a
metric already failing before the mutation is blamed by every mutation that
does not expect it.

**The stated rule was only half the rule.** "Do not re-record the baselines"
was written and obeyed; what it did not say is the consequence — a fixture on
the baseline side is as frozen as the baseline. The prohibition lived next to
the artifacts and not next to the input, so the input read as ordinary. The
README now says both halves, and the new fixture says why it is a separate
file instead of two more lines in the old one.

The fix was to leave `media_page.html` exactly as the baseline saw it and give
the new case `captions_page.html` of its own. That is also the better shape:
`embedded-media` keeps proving what a `file://` capture does with a track it
cannot read, and `captions-read` proves what loopback does with one it can,
and neither case's evidence depends on the other's fixture staying still.

### P10-9 — three things sat on the boundary of one two-line change

Caption path 1 changed one fact: a media element's `transcribed` stopped being
`false` by construction. The read itself worked first try. Everything that
went wrong was downstream of that fact, in code that had encoded the old one:

- **The manifest's video union discriminated on `transcribed`**, with a
  comment arguing that this was better than discriminating on `provider`
  because it is "the question a consumer actually asks". It was a fine
  argument while `transcribed` was a proxy for the provider. A transcribed
  media element matched neither arm, and every recorded bundle failed the
  contract test at once — the loud, cheap failure of the three.
- **`video_tally.untranscribed_media` was `len(self.media)`.** It kept
  returning that. So `captions-read`'s manifest said `transcribed: true` on a
  video and, four lines below, counted two media elements as untranscribed.
  Nothing failed: the parts still summed to the total, because the count that
  should have appeared did not exist yet. Narrowing the field alone would have
  left `0 + 1 ≠ 2` — a tally with a case missing, which reads as authority for
  a number nobody measured (P7-8). It needed the new part *and* the narrowing.
- **The new golden recorded an ephemeral port.** The loopback mask was
  anchored to `http://`, and the two leaks were not URLs: Trafilatura reads a
  loopback page's `sitename` as the bare `127.0.0.1:<port>`, and Chromium's
  print header puts the same bare host next to the title in `pdf-text.txt`.
  Recording succeeded. The corpus check would have failed on the *next* run,
  against a golden that looked deliberate.

**The pattern is the one P7-12 named, and the way it was caught is the point.**
None of the three was found by a test that already existed. The first was found
by running the suite; the second and third by reading the re-recorded diff line
by line and asking of each line what fact it asserted — `"untranscribed_media":
2` next to `"transcribed": true`, and a five-digit number that had no business
being stable. "Green is not correct" is the rule; this is what applying it
costs and what it buys. A golden records whatever it is handed, and both of
these were handed to it by a green run.

### P10-10 — the filter reached the guard and not the loops beneath it

Path 1 needed one behaviour changed: a media element whose `captions` track was
read must stop being announced as untranscribed. The change looked done. The
recorded bundle disagreed with itself:

```
manifest.json   "transcribed": true,  "cues": 2,  "role": "transcript"
content.md      ### Video not transcribed: Quarterly training video
```

`_record_untranscribed` computed `absent = [v for v in media if v.transcript is
None]` and passed it down. The function it passed it to used the parameter for
its early return — `if not media: return` — and then iterated
`enrichment.media`, the unfiltered list, in both loops beneath. Twice. So the
filter was real, was correct, and did nothing except decide whether to run.
Both media elements got a gap note; the transcribed one got the *translation*
note, because `untranscribed_note` was asked a question it exists to answer
only about media with no transcript.

**Three more things were wrong for the same reason, and all of them shipped
green.** The PDF's embedded README split its groups on `transcribed`, which was
a provider proxy until path 1 — so it put the captioned video in the walkthrough
branch and told a reader to open `videos/walkthrough.md`, `videos/steps.json`
and an appendix, none of which exist, under the headline "0 steps, documented in
full". The transcript itself reached no retrieval surface at all: not
`content.md`, not `content.txt`, not `chunks.jsonl`, only a file on disk. And
the gap chunks numbered `#/media/N` from their own output rather than from the
media's position on the page, so the moment the transcribed element stopped
producing one, `#/media/1` pointed at the element whose files are in
`media-02`.

**Every one of these is the same shape as `P7-12`, and none was found by a
test.** The manifest was read, the goldens were re-recorded, and the corpus
check was green through all of it, because a golden records whatever it is
handed. What found them was reading a re-recorded diff and asking of each line
what it asserted — `"transcribed": true` four lines above `not transcribed`,
and a README naming three files nothing had written. **When a fact changes,
grep for the fact, not for the function you changed**: `transcribed` appeared
in a discriminator, a tally, a README branch and a note writer, and the two-line
read that started it had a boundary touching all four.

### P10-11 — a guard that catches the total case reads as coverage for the partial one

`release.yml` builds a tagged release's notes by extracting the CHANGELOG
entry for that version:

```awk
$0 ~ "^## \\[" v "\\]" { found = 1; next }
found && /^## \[/                { exit }
found                            { print }
```

and then guards it:

```sh
test -s release-notes.md || { echo "::error::CHANGELOG.md has no entry for $version"; exit 1; }
```

That guard is real and it works — on the case where **nothing** was extracted.
It cannot fire on the case that actually happens. A release is cut by renaming
`## [Unreleased]` to `## [3.0.0]`, and if some of the release's features are
under that heading and others are still under a new `[Unreleased]` below it,
the extraction returns a non-empty file. `test -s` passes. The release ships
with notes that describe part of itself, looking exactly like a release whose
notes are complete.

This repository has been one pre-flight check away from that once already, with
the self-contained-PDF entry. It nearly happened a second time here: six merged
PRs of the journey walker sat under `[Unreleased]` with no entry at all, beside
a captions entry that would have extracted cleanly.

**This is the partial-coverage sibling of a trap already in CLAUDE.md.** That
one says *absence must not read as success* — ask what a check does when its
input is **missing**. All four gates it lists were found by that question. This
needs the second one: **what does the check do when its input is only half
there?** `test -s` answers "success", because non-empty is exactly what a
half-migrated CHANGELOG produces. A guard whose failure condition is the total
case is not evidence about the partial case, and the partial case is nearly
always the one that occurs — total absence tends to be caught by whoever
notices nothing happened at all.

### P10-12 — the cue cap bounded nothing, and cut the transcript in silence

Caption path 1 shipped with `max_cues: int = 5000`, spent as `[:max_cues]` in
`read_caption_tracks`. Found in review after the merge. One line, two defects,
opposite in kind:

**It did not bound what it was written to bound.** Its comment cited P7-10 —
"a number from a third party chooses how much work a capture does" — but the
slice ran on the Python side of the bridge, after `page.evaluate` returned.
By then every cue of every track had been parsed by the browser, serialized,
carried across CDP and deserialized into a Python list. The slice bounded what
was *kept*, never what was *done*. That is P7-9 exactly — a limit applied once
the cost it exists to refuse has already been paid — and the reviewer who
raised it had read it the other way, as a bound in the right place. It was
worth checking rather than accepting: the answer was in the four lines of
JavaScript that map `[...track.cues]` with nothing between them and the map.

**And what it dropped, it dropped silently.** `record["cues"]` published
`len(cues)`, so 5000-exactly and 5000-of-8000 were the same number, and a
transcript cut mid-sentence was inlined into `content.md` under a heading
saying "transcript" with nothing marking where it stopped. `MAX_VIDEO_BYTES`,
twenty lines from the old cap in the neighbouring module, makes the argument
against it about itself: a budget in bytes rather than a cap on steps,
*deliberately*, because "a walkthrough's steps are the document, and truncating
a legitimately long one at an arbitrary count would silently drop the
procedure's ending." Caption cues are the document in that same sense. The
reasoning had been written down, correctly, and was not applied twenty lines
away.

**One thing about the fix that must not be "tidied" later.** The in-page reader
still does `const all = [...track.cues]`, materialising the parsed list before
it spends the budget, and it has to: `total` is `all.length`, taken *before*
the cut, and that count is the only thing that makes truncation detectable.
Spreading the list is not the cost the bound exists to refuse — the browser
parsed those cues when the track loaded, and nothing in this file could have
prevented that. What was uncontrolled was the *serialisation across the
bridge*, which is what the budget now bounds. A later reader who removes the
spread to "tighten the bound" would tighten nothing and would silently delete
the ability to tell a cut transcript from a complete one, which is a worse
failure than the one this entry is about and would look like an improvement
while making it.

The bound is now a character budget spent inside the page, cue by cue, so the
work stops rather than the result being trimmed; and truncation is *reported* —
by the JS loop that knows it happened, not derived from a length comparison
that the blank-cue filter also makes true. It reaches the manifest's
`warnings`, the track's record, the `.vtt` file's own header and the
`content.md` section where a reader would quote it. A test drives the budget
down to one character so the guard is proven to fire, and asserts the same
fixture read without it is *not* marked truncated — a flag that is always true
proves as little as one that never fires.

**Unreachable is not the same as unimportant.** 5000 cues is about four hours
of speech, so no LMS module here would ever have hit it. But the failure mode
is a partial transcript wearing the word complete, in a tool whose output is
evidence, and the cost of the fix was an afternoon against a defect that by
construction only appears on material nobody has tested with.

### P10-13 — the diagnosis gets measured and the remedy gets shipped

Four instances in one afternoon, three of them by people who had just finished
insisting on measurement. None was carelessness, and that is the finding.

**1. The green-certifier that could not fail.** CI on #60 was certified with:

```sh
gh pr checks 60 | awk -F'|' '$2!="pass"{print}'; echo "--- (blank above means all green)"
```

Two of CLAUDE.md's own listed traps in one line. `gh pr checks` exits 8 for
pending and 1 for failing; the pipe turned every one into 0. And zero rows —
which is exactly what "CI has not started" produces — prints blank, under a
label reading *all green*. It did not produce a wrong answer, because rows
existed and had been read individually a call earlier. The conclusion was right
by luck; the check could not have distinguished the two cases.

**2. The remedy inherited the defect through a different mechanism.** The fix
offered for (1) was to keep the exit code:

```sh
gh pr checks 61 --json name,state,bucket > checks.json || echo "gh exit $?"
```

Measured afterwards, against a PR with twelve checks pending:

```
plain:  exit 8
--json: exit 0
```

`--json` zeroes the status. The `||` can never fire. One message after
explaining that a pipe discards the status being tested, the replacement
discarded the same status a different way — written with full attention, aimed
directly at that line.

**3. A gate shipped on one happy-path run.** The reviewer's replacement watcher
was correct by design — it derives its verdict from row data rather than from
gh's bucket status — and was shipped on a single run against a healthy PR.
Measured afterwards against three fabricated inputs (empty jq result, malformed
JSON, valid JSON missing the key), it failed closed on all three. It was sound.
It had not been *shown* to be sound, by someone who had spent the previous
message saying so.

**4. A count guard is not a set guard.** That same watcher reported GREEN on
fourteen checks. #60 had fifteen. The missing one was `review`, because
`claude-review.yml` fires on `[opened, ready_for_review, reopened]` and the PR
had been drafted before the event dispatched. `total > 0` proves checks
**exist**; it does not prove the expected ones **ran**. So a guard written
specifically to stop absence reading as success still let absence read as
success one level above the one it fixed — the missing thing was no longer a
row, it was a whole job.

**5. And once more, inside the commit writing this entry.** The docs build for
this very text was checked with `mkdocs build --strict | tail -3; echo $?`,
which printed `0` — `tail`'s status, not mkdocs'. Caught immediately, because
by then the trap was the subject of the paragraph being typed; it is recorded
because it is the strongest evidence for what follows. Knowing the trap by
name, while writing about it, was not enough to prevent reaching for the pipe.

**Attention is not the missing ingredient.** In every instance the author had
just moved their doubt onto exactly that line. What was missing is narrower and
more mechanical: *the diagnosis was verified and the remedy was shipped
unverified*. The doubt is spent by the time the fix is written, and a fix
arrives feeling like the end of an investigation rather than the start of one.

**The rule, and it is nearly free: whatever you used to establish the defect,
run it against the fix.** Exit codes were measured to find (1); the same
measurement run once more would have caught (2) before it was sent. Instance
(3) needed three fabricated inputs, which is more work — but (2) cost one
already-written command, not run, by four people who all knew better.

This is the same boundary P10-9 and P10-12 describe, seen from the other side.
P7-12 says a fix has a boundary and asks what else sits on it; this says the
fix *is* on that boundary, and the guard written to catch a class of defect is
one of the most likely places to find the next member of it.

### P10-14 — the teardown abort is onnxruntime's telemetry uploader

Numbered into P10 for the reason P10-6 gives: it is P10-4's sibling, and
closes it. P14-30 and P16-3 are the same abort seen from a signed-in export
run and from a live page.

#### The answer had been written down eleven times

macOS writes a crash report for every process that aborts, under
`~/Library/Logs/DiagnosticReports/`. There were eleven for `python3.12` on the
maintainer's machine, one from 2026-09-20 and ten from 2026-09-27, the last
of them reproduced for this entry. All eleven carry the same two stacks:

    main thread     exit → __cxa_finalize_ranges → PosixEnv::~PosixEnv
                    → PosixTelemetry::~PosixTelemetry → PosixTelemetry::Shutdown
                    → LogManagerImpl::FlushAndTeardown
                    → HttpClientManager::cancelAllRequests   (sleeping)
    crashed thread  WorkerThread::threadFunc → HttpClientManager::onHttpResponse
                    → HttpResponseDecoder::DispatchEvent
                    → DebugEventSource::DispatchEvent → recursive_mutex::lock
                    → system_error("Invalid argument") → terminate → abort

Every frame on both stacks that is not libc, libc++ or the interpreter is
in `onnxruntime_pybind11_state.so`. At exit the main thread
runs onnxruntime's static destructor, which flushes the telemetry queue and
waits for the upload to finish. The response lands on the SDK's own worker
thread, whose handler locks a mutex that teardown has already destroyed. The
throw has no handler, and the process aborts. The capture was finished before
any of this started.

The obvious Python-side instruments could not have found it.
`faulthandler` and `threading.enumerate()` report Python threads, and at exit
the only Python thread is `MainThread` (measured on the reproduction); the two
threads in the race are native and belong to onnxruntime. `--no-ocr` would
not have bisected it either, for the reason below.

#### What onnxruntime is doing, and why every capture does it

onnxruntime **1.29.0** (2026-08-17) is the first release with telemetry off
Windows: `core/platform/posix/telemetry.cc` does not exist at 1.27.0 or
1.28.0. It embeds Microsoft's 1DS SDK and starts the uploader **on import**.
Importing onnxruntime adds two native threads; with `ORT_DISABLE_TELEMETRY=1`
it adds none. (The crash reports show a 1DS worker and a CFNetwork loader among
the process's threads.) The events go to
`https://mobile.events.data.microsoft.com/OneCollector/1.0`. Upstream's
`telemetry.cc` names them: process info (OS, CPU model, memory), session
creation, runtime errors with a scrubbed message, file and line, and run
timings. They are keyed to a persistent device id, kept with an offline event
cache in `~/Library/Application Support/Microsoft/DeveloperTools/.onnxruntime/`.

WebShot never imports onnxruntime itself; two dependencies do. markitdown, a
base dependency, imports magika at module scope, and magika imports
onnxruntime. `acquire/adapters.py` imports the markitdown bridge at module
scope, so **every capture loads the uploader**, an HTML page as much as an
office file. RapidOCR is a second route. That is why the abort was never
specific to OCR, to a page, or to Chromium.

*Narrowed by P10-15:* the bridge is now imported only where a local document
is read through MarkItDown, so a URL, HTML, Markdown or image capture no longer
loads onnxruntime at all. Captures that read a document still do, and still
rely on the switch below.

It also explains two things P10-4 recorded without explaining.

- **CI never saw it.** onnxruntime suppresses telemetry when any of thirteen
  CI variables is set (`CI`, `GITHUB_ACTIONS`, `TF_BUILD`, …), and every CI
  job sets them. Local gauntlets and ordinary captures did not.
- **The v3.0.0 "no browser" case fits.** Re-run at the tag, `webshot
  --version` loads onnxruntime through the package init → pipeline → adapters
  → markitdown → magika chain that P10-6 later cut. So that process held the
  uploader too. It still does at P10-6's parent commit: P10-6 listed nine
  native roots on that path, and onnxruntime was a tenth, missed. The timeline
  agrees: onnxruntime entered `uv.lock` at 1.29.0 in 8dead5b on 2026-08-20,
  and the v3.0.0 abort came the next day. None of the recorded occurrences
  predates it.

The rate was never a property of a page. The race needs the answer to the
exit-time upload to arrive after teardown has begun, which is a matter of
network timing:

- 1,529 `--version` runs in P10-6, each holding the uploader: no abort.
- 96 processes that only built a magika session and lived 0, 2, 5 or 9 s: no
  abort.
- The research center's page (P16-3) on 2026-09-27: 4 of 8 in one session,
  1 of 8 an hour later, 0 of 16 in the A/B below.

"This page aborts and the others never do" was a sample drawn in a bad
minute.

#### The fix, and the ones not taken

`webshot/__init__.py` sets `ORT_DISABLE_TELEMETRY=1` with `setdefault`.
onnxruntime latches the variable when it initializes, and with it set creates
no uploader, no events and no device id. Upstream's `docs/Privacy.md` states
this, and the thread count above measures it. The package init is the one
place every path into WebShot runs before it can import anything: the console
script, `python -m webshot`, the `webshot.py` shim, the MCP server, the
library API and the tests. A caller's explicit value is obeyed, and `0` opts
back into both the telemetry and the abort.

Alternatives that were rejected:

- **`onnxruntime.disable_telemetry_events()`** only stops events. By the time
  Python can call it, import has already started the uploader.
- **Joining the thread before exit** is impossible from Python: the thread is
  not a Python thread and nothing exposes it.
- **`os._exit(code)` after the outputs are written** skips every static
  destructor, not just the faulty one, and drops unflushed stdio. The probe
  written for this entry lost its own output that way. It would also hide the
  next teardown bug instead of reporting it.
- **Treating 134 as success when the report is complete** would teach every
  caller to ignore the one exit code that means the process died. The QA
  report already carries the evidence: the aborting reproduction wrote
  `exit_code: 0` before teardown, so a fresh report saying 0 beside a process
  exit of 134 was always this abort. That is a diagnosis, not a policy.

#### Measured after the fix

The measurement that established the defect, run against the fix (P10-13):

- 20 captures of that page with the variable set by hand, after 5 aborts in 16
  without it earlier the same afternoon: 20 of 20 exited 0, with no new crash report.
  That comparison is not interleaved, so on its own it is weak.
- An interleaved A/B on the fixed code: arm A opted back in with
  `ORT_DISABLE_TELEMETRY=0`, arm B left the variable unset. The result was 0
  aborts in 16 for A and 0 in 16 for B. The race was quiet that hour, so this
  arm proves nothing either way, and it is recorded so that nobody counts it.

The evidence that carries the fix is structural. The crashing thread is the
1DS worker. With the switch set it is never created: two threads without the
switch, none with it, and upstream's `Initialize()` returns before building
the log manager. The race cannot happen without its participant.

`tests/test_onnxruntime_telemetry.py` holds the ordering, which is the property
that matters. A fresh interpreter, with the variable removed from its
environment, imports each route to onnxruntime (the markitdown bridge, and
RapidOCR's recognizer) under an import hook. The hook records the variable at
the moment onnxruntime is first looked up. If the variable is missing, the
hook exits before the native module loads, so the regression test cannot
itself start the uploader. It failed against the unfixed package with
`ORT-IMPORT None`, for the right reason. A route that stops reaching
onnxruntime fails as a measurement of nothing, not as a pass. A third test
looks for the variable's name in the installed native module, because an
upgrade that renamed it would leave every other test green and the uploader
running.

#### What follows

- **A 134 is a crash again.** It is not a known quirk to retry past, and not
  noise to ignore. If one recurs, read the crash report first: it names the
  library in its first dozen frames. For six weeks nobody looked, and it had
  the answer each time.
- **"No test touches the public internet" had a hole the size of a
  dependency.** Every local test that loaded the capture stack also loaded
  the uploader. The crash stacks show responses being decoded, so uploads
  were being made. That includes the test asserting that RapidOCR "would have
  to download models, which no test may rely on". The rule was enforced on
  page traffic and on model downloads, not on a library's own background
  thread, which is the P7-6 shape: a guard covers what it covers.

### P10-15 — every capture loaded an ML runtime to read no document

Numbered to follow **P10-14**, which was measured in parallel on #71 and
landed first. P10-14 found the route this entry cuts: `acquire/adapters.py` imported the MarkItDown bridge at module
scope, so every capture loaded onnxruntime and, with it, the telemetry uploader
whose teardown was the exit-134 abort. P10-14 switched the uploader off. This
entry stops loading the runtime where nothing uses it.

#### The chain

`pipeline.py` imports `acquire.adapters` for every capture, because
`materialize_source` is how any source, a URL included, becomes something the
browser can open. `adapters.py` imported `markitdown_bridge` at module scope;
the bridge imports markitdown; markitdown imports magika; magika imports
onnxruntime. Each link is an ordinary module-scope import and none is wrong on
its own terms. Only one branch of one function in `adapters.py` ever calls the
bridge: the one that reads a local document through MarkItDown.

On this machine the chain also pulled `mammoth`, `openpyxl` and `pptx`
(markitdown's office converters, present because the `office` extra is), and
`pandas` and `numpy` with them. Those five depend on which extras are
installed, which is the P10-6 reason the test below denies names and asserts
no counts.

#### What changed

The bridge is imported inside `_render_local_file`, in the branch that calls
it. Markdown, HTML and images never reach that branch; CSV, TSV, JSON, JSONL,
the text formats, XML, YAML, EPUB, ZIP and the office formats do, and still
load MarkItDown when they are read, because that is what reads them.

#### Measured

`import webshot.pipeline` in a fresh interpreter, eleven runs each, medians,
run from a directory outside the checkout so the `webshot.py` shim could not
answer instead of the package (P10-6). Modules are counted from `sys.modules`,
not `-X importtime`, for P10-6's reason.

| | modules | import | process wall clock |
|---|---|---|---|
| before | 1861 | 0.754 s | 0.871 s |
| after | **959** | **0.461 s** | **0.538 s** |

The module count was the same in all eleven runs on each side. The roots that
left are `onnxruntime`, `magika`, `markitdown`, `markdownify`, `numpy`,
`pandas`, `bs4`, `soupsieve`, `requests`, `idna`, `dotenv`, `mammoth`,
`cobble`, `openpyxl`, `pptx`, and the stdlib and Cython-runtime modules only
they used (`ctypes`, `tarfile`, `_strptime`, `_cyutility`, …). No root was
added.

A real capture, not just the import: `webshot.cli.main` against
`tests/fixtures/professional_page.html` served from a loopback server, with
`sys.modules` read after `main` returned.

| | modules at exit | onnxruntime / magika / markitdown |
|---|---|---|
| default options (Tesseract OCR), before | 2241 | loaded |
| default options, after | **1848** | **not loaded** |
| `--no-ocr`, before | 2241 | loaded |
| `--no-ocr`, after | **1848** | **not loaded** |

Every run exited 0 and its report said 0. The capture wall clock was one run
per cell (5.71 s → 3.88 s with OCR, 3.87 s → 3.81 s without), and a single
run proves no timing difference, so none is claimed beyond the ~0.3 s the
import itself measured. The first before run was also the first capture of the
session.

The output did not change. Stderr was identical line for line apart from the
loopback port and the output paths. Every bundle file was byte-identical except
`manifest.json`, which differed only in `captured_at`, the port, and the PDF's
`bytes` and `sha256`. The PDF itself varies between runs of *unchanged* code
(three `--no-ocr` runs after the change: 321614, 321608 and 322435 bytes), so
its size is not evidence either way.

When this was measured, the golden corpus and the parity gate could not
certify it on their own, because on this machine both failed on master: 12 of
26 golden cases and 9 parity cases, the P7-14 font drift. macOS rewrote
`/System/Library/Fonts` on 2026-09-03, after the goldens were recorded, and
P7-14's fix was still on #71. So the check was an A/B in one environment
instead. `tools/golden/check.py`
and `tools/parity/check.py` were run with `adapters.py` at master and again
with this change, and their full output was compared after masking temporary
paths. The two were identical, down to the same drifted digests and OCR
confidences. That shows the change moves nothing the corpus records. It does
not show that the corpus passes, and on that master it did not. The merge that
brought P7-14 in is where the corpus was run as a pass/fail gate again. At
5989891 it passed: all 26 golden cases on both paths, under the portable
profile P7-14 falls back to while no recording carries a font fingerprint, and
all 17 parity cases.

Measured per local format with `materialize_source`: `sample_report.md` no
longer loads the three roots; `sample_data.csv` and `sample_notes.txt` still do.

#### One behaviour changed, and a test holds the part that matters

A missing or unloadable markitdown, magika or onnxruntime used to surface when
`webshot.pipeline` was imported, which after P10-6 is inside the CLI's `try`
and exits 9. Now it surfaces only when a local document is read through
MarkItDown, and a URL, HTML, Markdown or image capture no longer fails on it at
all: those captures never needed it.

The exit code for a document capture has to survive a longer trip.
`materialize_source`'s handler in the pipeline turns `ValueError` into 2 and
`OSError` into 9; an `ImportError` is neither, so it reaches `classify`, which
answers 9. `test_a_document_reader_that_cannot_load_is_an_environment_failure`
runs the CLI on a `.txt` fixture with `markitdown` made unimportable and asserts
9, the report's code, the error naming markitdown, and no PDF. Adding
`ImportError` to the pipeline's `ValueError` clause makes it fail with `2 == 9`,
the mutation it exists to catch. The library API and the MCP server also meet
that `ImportError` when a document is read rather than at import. How each of
them reports it is not changed by this entry, and it was not tested here.

#### The tests, and what each was run against

`test_pipeline_import_loads_no_ml_runtime` denies `onnxruntime`, `magika` and
`markitdown` on `import webshot.pipeline`. With the module-scope import
restored, it fails naming all three and 1861 modules.

`test_pipeline_measurement_actually_ran_and_can_see_what_it_denies` is its
guard, in two halves. The first requires `webshot.acquire.adapters` among the
loaded modules: that is the module that carried the import, and without it the
deny test would pass because the carrier was never loaded. The second is a
positive control. The same probe, importing the bridge itself, must see every
denied root. A deny list is only as good as its spelling, and a misspelt name
is denied forever at no cost; `"markitdwon"` in the list fails the control and
passes the deny test, which is the case the control exists for. The control
also fails when the chain shortens, for example when magika stops importing
onnxruntime. That failure is good news, but a deny-list name nothing can load
would otherwise sit in the list checking nothing. The control process loads
onnxruntime, so it sets `ORT_DISABLE_TELEMETRY=1` itself. P10-14's
package-init default is a `setdefault`, which honours a caller's `0`, and a
test process must not start the uploader whatever the environment it inherits
says.

#### What this does not do

It does not replace P10-14. A capture that reads a CSV, a text file or an
office document still loads onnxruntime and still starts its uploader unless
the telemetry switch is set. P10-14's package init sets that switch by
default. Structurally, a process that never loads onnxruntime's native module has
neither its uploader thread nor its static destructor, so a URL, HTML,
Markdown or image capture no longer carries the P10-4 teardown surface even
where a caller sets `ORT_DISABLE_TELEMETRY=0`. No abort rate was measured for
this entry, and none is claimed.

The runtime is still installed. `test_the_onnx_runtime_exception_is_still_needed`
still argues for that, and nothing here changes it. What changed is that being
installed no longer means being loaded.

### P10-16 — the audit checked its runner's share of the lock and called all of it clean

`uv export` writes one requirements file for every platform the lock covers,
with an environment marker on each line that applies to only some of them.
pip-audit evaluates those markers against the machine it runs on and drops
every line that does not match. It gives no skip reason, and `--strict` does
not object, because `--strict` fails on a *skipped* dependency and a line whose
marker is false never becomes a dependency at all. Both of pip-audit's
collection paths do this. With `--no-deps` alone, which is what CI ran, pip
resolves the file in a scratch environment (`pip install --dry-run --report`)
and leaves the line out. With `--disable-pip`, pip-audit's own parser skips it.

Measured on macOS: a file holding only
`transformers==5.8.1 ; sys_platform == 'linux'` printed "No known
vulnerabilities found" and exited 0, although 5.8.1 carries PYSEC-2026-3929.
The lock at 65a7cc4 pins 5.8.1 for `sys_platform == 'darwin'` (docling-core's
`chunking` extra caps transformers below 5.9 there) and 5.15.1 everywhere else.

Which share CI saw depended on where the job landed. At the time, `runs-on`
sent it to `vars.CI_RUNNER`, the self-hosted Mac, unless the pull request came
from a fork, which got `ubuntu-latest` (this copy's CI runs the job on
`ubuntu-latest` only). The `vulnerability scan` job ran on the self-hosted Mac
runner in all of the twelve most recent CI runs. There it did catch
5.8.1: `perf/defer-markitdown-import` failed on PYSEC-2026-3929 on 2026-09-27.
What the Mac never audited was the other side of every fork. This finding was
first written up as the Linux CI job passing 5.8.1, and the runner history does
not support that. The defect is the same; the pins it hid on this repository's
CI were the non-macOS ones.

No single host audits the whole lock. By marker evaluation, of its 177 pins:

| host | pins it never audits |
|---|---|
| macOS arm64, CPython 3.12 (the CI job's runner, and this machine: measured) | httpx2-jsfetch 1.0, numpy 2.4.6, pywin32 312, transformers 5.15.1, tzdata 2026.3 |
| Linux x86_64, CPython 3.12 (a pull request from a fork) | httpx2-jsfetch 1.0, numpy 2.4.6, pywin32 312, **transformers 5.8.1**, tzdata 2026.3 |

Stripping the markers is not enough on its own. pip-audit refuses two versions
of one package in a single run ("package transformers has duplicate
requirements"), across every `-r` file that run is given, so a forked package
cannot go in as two lines.

`tools/audit/check.py` now does the whole job, and CI and `tools/verify.py` run
that one command, so the export flags and the audit flags cannot drift apart:

- It parses every line of the export as exactly one `==` pin. A URL, a range,
  an option line, or an export with no pins at all is an error, not a line
  passed over.
- It keeps the markers for the report and never hands them to pip-audit. The
  pins are grouped so that no run names a package twice: two runs for this lock,
  one for a lock with no fork.
- It passes `--disable-pip`, because bare pins include `pywin32`, which pip
  cannot resolve on Linux.
- It requires pip-audit's JSON report to account for every pin it was handed.
  This is a set comparison, per run and again over the whole audit. A missing
  report, a skipped pin, or an exit without a finding is exit 2, *incomplete*,
  which is distinct from both clean (0) and vulnerable (1).
- It prints the pins that apply only to another platform, so every run shows
  that the audit covered them instead of asserting it.

**Its first CI run failed closed.** CI sets `FORCE_COLOR=1`, and `uv export` to
stdout then wraps each comment line in escape codes (`\x1b[32m# via typer`), 400
of 577 lines. The first line no longer read as a comment, the parser refused it,
and the job exited 2, *incomplete*. That is the exit the design exists for: an
audit that could not read its input did not report clean. The script now
exports with `--output-file`, which uv writes plain, and a test sets
`FORCE_COLOR` and asserts that the export parses. Requirement lines are never
colored, which is why `tests/test_footprint.py`, which parses the same stdout
with a regex anchored on package names, was unaffected.

**The service was not the one named.** The CI step was titled "pip-audit
(OSV)", and SECURITY.md, the security guide, and docs/06 all say OSV, but no
flag chose a service, and pip-audit's default is PyPI's JSON API. The script
passes `--vulnerability-service osv`. On this lock the two services report the
same single advisory; OSV returns it twice under one ID, and the report
de-duplicates it.

Held by `tests/test_vulnerability_audit.py` and
`tests/fixtures/audit/foreign_marker_pin.txt`. The tests run the real pip-audit
against a loopback OSV, and the fixture hides the vulnerable pin behind
`sys_platform == 'win32'`, a marker that matches neither a Linux nor a macOS
test host. One test reproduces the defect: pip-audit, given the file directly,
reports it clean and never queries 5.8.1. Two mutations of the script were
checked against the suite. Re-applying the host filter, and putting both
versions in one run, each fail the end-to-end vulnerable and clean tests.

Consequence: the lock at 65a7cc4 fails this audit on every runner, as it
already did on the Mac. #71 took docling-core to 2.96.0, whose `chunking` extra
no longer caps transformers on macOS, and that collapsed the fork into a single
transformers 5.17.0. That lock audits clean, with all 176 of its pins accounted
for. #80 reached the same pin through a `[tool.uv]` override and is superseded:
on top of #71, its own expiry test fails, because docling-core 2.96.0 admits
5.17.0 without the override.

This is CLAUDE.md's "a guard covers what it covers" (P7-6) in a gate rather than
a route: the audit guarded the lines that reached it, and it never saw the
filter standing in front of it.

### P10-17 — mcp 2.1 masks every exception that is not a `ToolError`

P4-9 deleted the per-tool error translation on a measured fact: the SDK's
`_handle_call_tool` caught `Exception` and returned its text as the tool
result, which was already the refusal shape WebShot wanted. mcp 2.1 reversed the
fact. `Tool.run` now passes on the text of a `ToolError` (or `ResourceError`)
only; anything else is re-raised as `UnexpectedToolError`, whose message is
`Error executing tool <name>` and nothing more, with the original kept on the
server for the log.

The Dependabot bump to 2.1.1 (#67) failed the test legs on every platform, and
locally 13 tests in `tests/test_mcp_server.py` fail against the unchanged
bridge, every one the same loss: `test_every_reader_refuses_a_path_outside_the_roots`
saw `Error executing tool read_markdown` where the policy had written which root
the path fell outside. The guardrails still refused — nothing was read or
published — but the agent was told only "error", which is the retry loop
`policy.Denied`'s docstring says a refusal exists to prevent.

The fix is the translation P4-9 removed, in a form that keeps what P4-9 was
protecting. Each tool body runs inside `_refusals()`, a context manager that
re-raises a `WebShotError` as a `ToolError` with the same text. A context
manager inside the body leaves every tool's signature alone, so the published
schemas do not move (the decorator P4-9 tried replaced them with
`(*args, **kwargs)`). And it translates `WebShotError` alone: an exception
WebShot did not anticipate keeps the SDK's masking, which is new protection
2.1 brings and the bridge should not undo. `test_only_a_webshot_error_reaches_the_model_with_its_text`
holds that half — it passes against both the fixed bridge and the unfixed one,
and fails when `_refusals()` is widened to `except Exception`.

The floor in `webshot[mcp]` is `mcp>=2.1.1`, which Dependabot set. The bridge
would work on 2.0 as well, since `ToolError` existed there, but 2.0 is the
version whose behaviour this entry no longer describes.

### P10-18 — the reviewer went to wait for its agents, and the session ended

`Claude review` passed on #78 (run 36350676728) after three turns, with no
denials and no comment. The session's last words were *"I've kicked off both
checks in parallel … I'll wait for both to finish before proceeding."* Nothing
followed. The guard printed `turns=3 denials=0` and exited 0, because nothing
had been refused.

Every review from 2026-08-24 to 2026-09-27 that reached a result record was
read from its job log. There were 26, and all 26 were green:

| Outcome | Runs | Pull requests |
|---|---|---|
| Ended with subagents still running, no comment | 20 | #45–47, #49–59, #62, #71, #77, #78, #79, #81 |
| Stopped at the plugin's first step, no comment | 4 | #60 (draft, then closed), #61 (draft), #64 (no `gh`) |
| Posted a review | 2 | #61 (two inline, one summary), #63 (five inline) |

The API agrees: none of the 20 pull requests has a comment from `claude[bot]`.
The 20 left 53 subagents running between them, in 3 to 43 turns each.

#### Why the session ended

Three facts, each read from source rather than inferred from behaviour:

1. **Claude Code 2.1.241 starts a subagent in the background by default.**
   The pinned action (v1.0.201, `c81e3bc`) installs that version. In its
   bundle the launch decision is `is_async = remote || (run_in_background ===
   true || … || (!teammate && run_in_background !== false)) &&
   !backgroundTasksDisabled()`, and the tool describes itself as *"Agents run
   in the background by default"*. The
   code-review plugin has not changed since 2026-03-12. It says "launch a haiku
   agent" and never says to wait. The result record's `subagent_stats.requested`
   shows both routes in. In 12 of the 20, every spawn left the parameter unset.
   In the other 8, the model passed `run_in_background: true` for the four
   parallel reviewers, which is what the tool's own guidance tells it to do.
   2.1.281 was read as well, the nearest local build to the 2.1.283 that #76's
   bump installs. It moves the rule into a helper without changing it:
   `shouldRunAsync = remote || (… || wantsBackground !== false) &&
   !backgroundTasksDisabled`.
2. **The action stops at the first result.** `base-action/src/run-claude-sdk.ts`
   `break`s out of the SDK's `for await` on the first `type === "result"`
   message. A comment there says nothing follows a result by the SDK's
   contract. With agents in the background, the orchestrator's turn ends on
   "I'll wait", that turn is a result, and the loop exits. #76 bumped the
   action to v1.0.235 (Claude Code 2.1.283) while this was in review. That
   version breaks in the same place, so the bump does not fix this.
3. **The guard asked what the review was refused, not what it produced.**
   `denials=0` was true every time. The record said otherwise four keys away:
   `subagent_stats` reported `completed: 0` against `spawned: 2`.

#63 shows that this is the whole mechanism. Twelve of its fourteen subagents
started in the background, but its orchestrator kept working for 357 turns
instead of ending the turn, collected all fourteen, and posted five comments.

#### The second hole: `gh`

The plugin does all of its GitHub work through `gh`: the eligibility check, the
diff, and the "no issues found" comment. Eleven of the 26 runs hit
`gh: command not found`. Ten were every run on the second Linux runner. The other
was on the first, in a run whose review started at 20:16Z, a minute
before `gh` was installed there. #64's
review ended *"The gh CLI is not installed in this environment. This is a hard
blocker"* and exited success. The runner image (its Dockerfile is not in this
public copy) did not install `gh`; the two containers had it only because it
had been installed by hand, so a container rebuilt from the image would lose
it again.

#### The third hole: a queued run read master's edit as the PR's

This one was found while this entry was in review, by the session that opened
#90. The job's first step decides whether the PR edits the reviewer by diffing
`origin/<base>` against `HEAD`. `HEAD` is the merge commit GitHub made when the
event fired, while `origin/<base>` is the base as it is when the job starts.
#76 changed `claude-review.yml` on master at 01:47Z on 2026-09-28. The reviews
of #93 (created 01:13Z) and #90 (00:57Z) sat queued until 01:55Z and 01:58Z.
Each checked out a merge commit built on the pre-#76 master (`c54bacc` and
`c21d157`), saw the pin #76 had changed, and printed "This PR edits
claude-review.yml". Neither PR touches the file. The action refused the stale
copy, and the guard took its self-modified branch — "the documented
behaviour; there is nothing to verify". Both went green in ten seconds, having
reviewed nothing.

The PR's own change is `HEAD^1..HEAD`: the merge commit against its first
parent, which is the base as it was when the merge was made. The step now
diffs that. A run whose copy differs from master's for any other reason is
`stale`, and the guard fails it with the way out: a re-run reuses the stale
copy, but a fresh event does not.

#### The fix

- `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` on the action step. In 2.1.241 and
  2.1.281 this variable is the `backgroundTasksDisabled` term above. It removes
  `run_in_background` from the tool's schema, and it forces `is_async` false
  even when the model passes `true`. The one exception is remote isolation,
  which the review does not use. That is why the fix is an environment
  variable and not a sentence in the prompt: 8 of the 20 runs had the model
  explicitly choosing background. The action passes its whole environment
  through to the SDK (`{ ...process.env }`), so a step-level `env:` reaches it.
- The self-modified test compares the merge commit with its first parent, not
  with the base as it is now, and a stale run fails instead of passing.
- Drafts skip at the job level. `ready_for_review` fires when a draft is ready,
  and a review that correctly stops on a draft posts nothing.
- A step before the review refuses to spend a run on a runner without `gh`, and
  prints the runner's name.
- One appended sentence tells the reviewer to comment when it stops at the
  plugin's first step deliberately. Silence then means only one thing.
- The guard now also fails when a subagent was still running when the result
  arrived (`spawned − completed − failed − killed > 0`). It fails as well when
  no comment from `claude[bot]` was created after the review step began. It
  prints the final result text, the subagent counts, and each comment's URL.

**What was measured, and what was not.** The guard was run as extracted from
the workflow, not retyped. Its inputs were the 26 execution files, rebuilt from
the job logs, and the comment API with every comment newer than the job's end
hidden. It passed #61 and #63 and failed the other 24. The self-modified step was run on
merge commits rebuilt locally: #90's head merged into the pre-#76 base
(`stale=true`), into master now (neither flag), #83's head into each
(`changed=true`), and a single-parent HEAD (exit 1). It was also run against
15 fabricated inputs, and each failed closed or passed as intended. The inputs
were: an empty file, an empty array, a missing `subagent_stats` (with and
without comments), a missing `completed`, a missing or garbled start time, an
API 404, one denial, no denial key, a string count, the concatenated-stream
format, a different reviewer login, a killed subagent, and a start time later
than every comment.

The environment variable's effect was read from the 2.1.241 and 2.1.281
bundles, not observed in a run, and 2.1.283 itself was not read. The local CLI could not authenticate to run it, and a pull
request that edits `claude-review.yml` is never reviewed by it. The first
review after this merges is the measurement, and the guard reports it either
way: `background=0` in its `subagents:` line, or a warning that names the
variable.

#### What follows

- **This guard has now reported success twice for work that never happened.**
  The first time it skipped itself on empty output. This time it measured the
  one input it was built for, and that input was not where the failure was.
  CLAUDE.md asks what a check does when its input is missing. For a reviewer,
  the input that matters is its output. Ask of every such gate whether it saw
  the product, not whether the process looked healthy.
- **`gh` belongs in the runner image**, so that a rebuilt container does not
  quietly bring this back. Until then, the step before the review makes a
  missing `gh` a red job instead of a green one.
- **#76 landed while this was in review**, so the first review after this
  merges runs 2.1.283. Read its `subagents:` line, and do the same after every
  later bump. A renamed variable shows up there as `background>0` and a warning
  before it shows up as an abandoned review.

### P10-19 — docling-slim 2.130 keeps table captions, and the bridge said it had lost them

The docling bridge's docstring recorded an upstream fact: the HTML backend
"drops `<table><caption>` text entirely", so the bridge re-attached captions
itself, pairing tables to snapshot captions by position. When the table counts
disagree (a nested table flattened into its parent), positional pairing would
be a guess, so the bridge skipped it and warned that the captions "could not be
re-attached".

Dependabot's bump from docling-slim 2.121 to 2.130 (#70) failed
`test_nested_tables_degrade_with_a_warning_not_a_failure` on the first two test
legs to finish (linux py3.11, macos py3.12), and the failure was the fact
changing, not the code. On 2.130 the backend keeps
`<caption>` and attaches it to its table. Measured on seven nested shapes, every
snapshot caption was carried by a table in the document, including the three
shapes where the counts disagree. In all three the bridge still warned. A
manifest warning that a caption was lost, on a document that carries it, is
the "claim exactly what was measured" defect, so the warning was wrong on 2.130
even though the capture was right.

The fix keeps the warning for the loss it exists to report and drops it
everywhere else. On a count mismatch the bridge now compares the snapshot's
captions with the captions the document's tables actually carry, as collapsed
text and counted, so two tables captioned alike need two carried captions. It
warns only about the ones no table carries, and says how many. The aligned path
is unchanged: it already skipped a table that had a caption.

`test_nested_tables_keep_their_caption_without_a_false_warning` replaces the
old test. It asserts no caption warning and that "Outer" is carried, and it
fails against the unchanged bridge. `test_a_caption_no_table_carries_is_the_one_reported_missing`
holds the decision itself. No shape measured on 2.130 loses a caption, so that
branch of the warning has no end-to-end fixture. The helper test is what
stands for it until one turns up.

The caption also moves in the document's reading order. On 2.121 the bridge
appended it after the body; on 2.130 it sits in place, before the text that
follows the table. The golden corpus and parity are the check on whether that
moves any published output.
### P10-20 — pypdf 6.16.2 reads Linux Chromium's "The" as "T he"

The Dependabot bump of pypdf to 6.19.0 (#68) failed one test, and only on
Linux: `test_an_appendix_prints_without_ligatures` asserted that
`extract_text()` finds "The first office flow affects baffled staff.", and
got "T he first …". Bisected on a single PDF rendered in an Ubuntu 24.04
container, 6.16.1 reads "The" and every release from 6.16.2 through 6.19.0
reads "T he". That is the release whose line-break change moved the golden
corpus's `pdf-text.txt` in the same PR.

The PDF did not change between the two readings. Chromium on Linux places
each glyph with its own whole-unit `Td`. At 16 pt the fixture font's "T" is
8.9 units wide by its `/W` entry, but the next glyph starts 11 units on. The
2.1-unit gap is just over half the width of the font's space glyph (260/1000
em, 4.16 units). From 6.16.2 pypdf treats a gap that size as a space. On macOS,
Chromium places each glyph at its exact advance (8.896 units after the "T"),
so there is no gap, and the test passes there on 6.19.0. `visitor_text`
receives the inferred space as well, so pypdf offers no heuristic-free
reading to assert against.

The test exists for the ligature words: "first", "office", "flow", "affects",
"baffled" and "staff" must reach the text layer as letters and not as U+FB0x
presentation forms. "The" has no ligature. The assertion now names the words
after it. It fails exactly as before if any of them prints as a ligature,
since a presentation form never equals its letters. `ligature_forms()` still
checks the CMaps.

What this leaves open, and does not fix: a pypdf-based search of a PDF that
WebShot printed on Linux can miss "The" and any other word whose first glyph
Chromium follows with a gap. That is a property of the extractor's heuristic
and the renderer's placement, not of WebShot's output, but it reaches anyone
who searches the PDF with pypdf. The bundle is unaffected, because it is read
from the DOM.

### P10-21 — `docker stop` never reached the Linux runners

*Historical: this entry concerns the private repository's self-hosted runners, which this copy does not use.*

The self-hosted Linux runners ran in containers whose entrypoint (not in this
public copy) ended in `exec ./run.sh`, the GitHub Actions runner's start
script, under a comment saying `run.sh` handles SIGTERM, "so the container
stops cleanly mid-job". It did not. Every `docker stop` waited out its timeout
and then sent SIGKILL (exit 137), so the runner never deleted its session with
GitHub, and the next container logged "A session for this runner already
exists … Conflict" and retried for about two and a half minutes, until GitHub
expired the dead session.

The cause is two facts together. `exec` makes `run.sh` PID 1, and the kernel
drops any signal PID 1 has no handler for. `run.sh` installs a TERM handler
only when `RUNNER_MANUALLY_TRAP_SIG` is set; otherwise its shell catches
SIGINT and SIGCHLD and nothing else, which `/proc/1/status` confirmed. So
SIGTERM went nowhere, and the runner's listener never heard it.

The fix sets that variable, which is the runner's own switch for this case:
`run.sh` then traps TERM and passes it on as SIGINT, the path Ctrl-C takes.
Measured on a scratch runner, stops then returned in under half a second with
no SIGKILL, and the next container connected at once. The container exits 143
rather than 0; that is a clean stop, recorded here so that nobody reads 143 as
a crash. A stop *during* a job is still not graceful: the SIGINT reaches the
job's own processes, and GitHub records the job as **failure** with "Process
completed with exit code 130." and "The operation was canceled." That pair on a
runner's leg is a runner being stopped, not a test failing.

Applying the fix taught two more things.

- **`pipefail` can turn a match into a failure.** The drain step checked for a
  running job with `docker top … | grep -q Runner.Worker` under
  `set -o pipefail`. `grep -q` exits at its first match, `docker top` is still
  writing and dies of SIGPIPE, and `pipefail` reports the pipeline as failed,
  so a job that was running read as absent and one review was cancelled. This
  is the pipe trap in CLAUDE.md turned around: there a pipe hid a failure; here
  `pipefail` manufactured one. Save the output first, then search it.
- **A fix shipped in an image has to be the copy that runs.** The containers
  mounted a volume over the directory that held the entrypoint, and Docker
  copies image content into a volume only while the volume is empty, so a
  corrected script rebuilt into the image would never have run. The entrypoint
  moved outside the volume. Because the runner's self-update rewrites `run.sh`,
  the entrypoint also warns in the container log if `run.sh` stops naming the
  variable, so a future release that drops it cannot bring the SIGKILL back
  unnoticed.

### P10-22 — the reviewer ignored the single switch and the fork routing

*Historical: this entry concerns the private repository's self-hosted runners. In this copy no workflow names a self-hosted runner or reads a variable to choose one, and `tests/test_workflow_routing.py` now asserts GitHub-hosted labels only.*

`claude-review.yml` has selected its runner with
`vars.CI_LINUX_RUNNER || 'ubuntu-latest'` since it was written (`6af77b4`,
2026-08-22). P8-2 had set the rule sixteen minutes earlier, on another branch:
`CI_LINUX_RUNNER` is read only when `CI_RUNNER` is set, so deleting
`CI_RUNNER` sends every job to GitHub-hosted runners. docs/06 and the go-live checklist state
that rule for every job. The go-live checklist makes it the escape hatch when the Mac is off
and the last step of removing the runners: "one variable, because
`CI_LINUX_RUNNER` is only read when `CI_RUNNER` is set". The reviewer never
followed it. After that deletion, every review would still have asked for
the self-hosted Linux label, and with the runners gone would have sat queued until GitHub's
24-hour limit for a self-hosted job.

The same workflow had no fork routing either. Its only condition was the bot
check, while docs/06 said a fork's pull request "is always GitHub-hosted
regardless of the variables".

#### What the fork routing never covered

P8-1 made that routing "structural", and ci.yml's comment said that on going
public "the hole is already closed". It is not. A `pull_request` run uses the
workflow file in the PR's merge commit, and a fork writes that file. A fork
that edits `runs-on` or drops the fork term gets the runner it names. The
routing covers a fork that leaves the workflow alone, and nothing more. P10-18's
stale-copy case turns on the same mechanism: the copy that runs is the one in
the merge commit.

What does protect the machine, read from the API on 2026-09-28: the repository
is private with `allow_forking: true`, and
`actions/permissions/fork-pr-workflows-private-repos` reports
`run_workflows_from_fork_pull_requests: false`. A fork can exist, but its
workflows do not run. That setting stops applying when the repository goes
public, and the public-repository approval policy takes over. The endpoint for
that policy refuses a private repository, so it cannot be set in advance. Its
default gates only first-time contributors.

#### The fix

- The reviewer's `runs-on` is ci.yml's expression for its Linux legs, fork
  term included.
- The job skips a fork's pull request, as it skips bots and drafts. A fork's
  review could not happen anyway: by default its run gets neither the OAuth
  secret nor an OIDC token, and the action refuses an actor without write
  access. Skipped says that. Red would invite a fix.
- ci.yml's comment, docs/06 and the go-live checklist now say what the routing covers, and
  the go-live checklist's list for going public gains the approval setting. The runners
  still come out before the repository is public.
- `tests/test_workflow_routing.py` reads every `runs-on` under
  `.github/workflows`. It fails if one reads `CI_LINUX_RUNNER` outside
  `CI_RUNNER && …`, or reads a variable before the fork term. It also fails if
  `ci.yml` or `claude-review.yml` has no such line, so a rename cannot leave it
  testing nothing. Against the previous `claude-review.yml`, both tests fail.

**What was measured, and what was not.** The expressions were read, not run
with `CI_RUNNER` deleted. That deletion reroutes every job of every open pull
request. The new expression is character for character ci.yml's `perf`
expression, and its Linux branch is the one ci.yml's `tests` job uses. No fork
exists, so the fork skip has not run. The 24-hour limit is GitHub's documented
limit, not an observation. This change edits `claude-review.yml`, so its own
review reads the new `if:` and `runs-on:` from the merge commit and then stops
at the action's workflow check. That run, 36382552907 on #103, is the one
measurement of the variables-set path. The job ran, so the new `if:` let a
same-repository, non-draft pull request through. It ran on the first Linux runner
under the self-hosted Linux label. The self-modified step printed `changed=true
stale=false`, the action skipped on its workflow check, and the guard took the
self-edit branch ("The reviewer skipped itself because this PR edits its
workflow") rather than P10-18's stale-run failure.

#### What follows

- **A rule that lives in one workflow's comments binds only that workflow.**
  The reviewer was written without P8-2's rule because the rule was prose in
  ci.yml. It is now a test over every workflow.

### P10-23 — MarkItDown 0.1.8 escapes the cell delimiter, so the bridge's escape became a second one

P3-1 found that MarkItDown 0.1.7's CSV converter wrote cell values into a
Markdown table unescaped: a `|` in a cell ended the cell, and a newline ended
the row. The bridge fixed it on the way in, turning `|` into `\|` and newlines
into `<br>`. `MARKDOWN_OPENER_RE` also escaped a `|` at the start of a cell.

The Dependabot update to `markitdown>=0.1.8` (#106) failed
`test_table_cells_survive_the_markdown_delimiter_and_line_breaks` on all six
test legs. The page read `a \| b` where the file said `a | b`. 0.1.8's
`_escape_table_cell` now escapes every pipe, and first doubles any run of
backslashes in front of it. The bridge's `\|` therefore came out as `\\\|`,
which renders as a backslash followed by a pipe. The same happened to a cell
that opened with `|`. 0.1.8 also flattens a newline in a cell to a space.
That keeps the row whole, but the value loses its line break.

The fix leaves the pipe entirely to MarkItDown. It is removed from
`_escape_cell` and from the opener set. The `<br>` rewrite stays, because 0.1.8
would otherwise turn a line break into a space. The HTML escape and the other
opener escapes stay, because 0.1.8 does neither: a cell reading `x<y` still
reaches the Markdown raw. The dependency floor is now 0.1.8, so no supported
MarkItDown relies on the bridge to escape the pipe.

`test_a_pipe_is_escaped_once_whatever_stands_in_front_of_it` covers the two
places a second escaper would still show through: a cell that opens with `|`,
and a pipe the data already puts a backslash in front of. It fails against
the old bridge. It also fails when only `|` is put back in the opener set.

### P10-24 — the Windows run was green for six weeks and never ran a capture

`windows-smoke.yml` says it exists "so that 'best-effort' is a measured claim
instead of an assumption". Every scheduled run it has had reported success.
None of them measured a capture. Its one job carried `continue-on-error:
true`, which turns a failed job into a successful run, and the capture step
came after the unit tests, so a failing test skipped it. Read from the Actions
API on 2026-09-28:

| Run | Date | Commit | What the job did | Run reported |
|---|---|---|---|---|
| 32701867593 | 08-24 | `c1459bf` | never started: "recent account payments have failed or your spending limit needs to be increased" | success |
| 33399977473 | 08-31 | `65a7cc4` | never started, same annotation | success |
| 34123185249 | 09-07 | `65a7cc4` | 97 failed, 10 errors, 850 passed; capture skipped | success |
| 34846131107 | 09-14 | `65a7cc4` | 97 failed, 10 errors, 850 passed; capture skipped | success |
| 35602369933 | 09-21 | `65a7cc4` | 97 failed, 10 errors, 850 passed; capture skipped | success |
| 36433115539 | 09-28 | `521e1cc` | 112 failed, 38 errors, 1,231 passed; capture skipped | success |

Three rows match because `master` did not move between 2026-08-29 and
2026-09-27. The last ran a later `master` with more tests.

Two of those are the job never existing and still reading as success. The
workflow is scheduled and dispatched, never run on a pull request, and no
required check comes from it. So `continue-on-error` did not keep Windows from
blocking anything, because nothing could be blocked. What it did do was hide
the run.

#### What the 150 were

Job 108964092253 (run 36433115539, `master` at `521e1cc`, CPython 3.12.10 on
`windows-latest`), grouped by root cause:

| Cause | Tests | Kind |
|---|---|---|
| `outputlock.py` names `os.O_NOFOLLOW`, which Windows does not have (P8-12, `3512ca8`, 2026-08-22). Every capture raised `AttributeError` at the lock and exited 5 | 100 | product bug |
| `Path(unquote(urlparse(url).path))` reads `file:///C:/x` as the drive-relative `C:x`. Every local source "does not exist", and every file inside an MCP root is judged outside it | 35 | product bug |
| The CLI reference generator printed a `Path` default with `str()`: `output\pdf` | 1 | tooling bug |
| The MCP owner-only profile check reads mode bits, which Windows reports as `0o777` | 1 | product: true refusal, untrue message |
| Tests that assumed their host: a `write_text` fixture (`\r\n`), two Tesseract language tests that did not stub the binary, three protected-viewer tests and one ligature test that need Tesseract, a `chmod`-based doctor setup, `os.mkfifo`, two audit tests whose hidden pin is the `win32` one, and a tilde test that assumed the temporary directory is not under home | 12 | test assumption |
| onnxruntime's Windows module does not read `ORT_DISABLE_TELEMETRY` | 1 | known limitation |

The 35 include every archive-bomb, zip-slip and hostile-XML test. They failed
because the source never loaded, so none of those refusals had been measured
on Windows either.

*Decided by P10-26:* WebShot now calls `disable_telemetry_events()` on
Windows. The trace shows no session event under the default; the three
events onnxruntime writes while it is imported remain.

#### The fix

- **The lock** keeps its one `os.open` where `O_NOFOLLOW` exists. Without it,
  the file is created with `O_EXCL` only when nothing has the name, not even a
  dangling link. Otherwise it is opened without `O_CREAT`, and the descriptor
  is refused unless the name is not a link and `samestat` matches it to what
  was opened. The new tests delete `os.O_NOFOLLOW` to reproduce Windows on
  POSIX CI. All four fail on the old module. The symlink and dangling-link
  cases also fail on the one-line `getattr(os, "O_NOFOLLOW", 0)` fix, which
  would have made Windows pass and quietly dropped P8-12 there.
- **The Windows byte lock** moved from byte 0 to 1 MiB, and is unlocked
  before the close. Windows byte-range locks are mandatory, so a contending
  run could not read the pid under the lock. The first dispatched run below
  measured that: the refusal never named its holder.
- **`netpolicy.file_url_path`** is the one inverse of `Path.as_uri()`. It is
  `url2pathname`, which on POSIX is exactly `unquote`. The source adapter, the
  subresource root check and the MCP source check all use it. It also refuses
  a `file:` URL that names a host other than `localhost`. All three checks read
  only the URL's path, so `file://server/<root>/page.html` passed the MCP
  check as the file inside the root. On Windows that URL is a UNC path on
  `server`. This was found by reading the code, on every platform, and the new
  test fails on the old policy. The MCP check also confines a bare
  `C:\page.html` as a path instead of refusing the URL scheme `c`; that test
  runs only on Windows, and passed in the dispatched runs below.
- **The MCP profile check** still refuses every profile on Windows, because
  owner-only is a MUST (§6.3) that mode bits cannot prove there. It no longer
  says the profile is "group- or world-accessible" or tells the caller to
  `chmod 700`. Nobody measured the first, and no Windows user can do the
  second.

  *Superseded by P10-25:* the Windows branch now reads every entry's owner
  and DACL, and refuses only a profile that is not owner-only.
- **Tests** are host-independent where they can be, and otherwise skipped with
  the reason. The telemetry test asserts on Windows what is true there: the
  1DS uploader, and P10-14's exit-time abort with it, is absent.
- **The workflow** has no `continue-on-error`. The capture and deliverable
  checks run after failing unit tests, and pytest runs on four workers, as
  in `ci.yml`. `tools/ci/junit_summary.py` writes
  the job summary from pytest's JUnit report, listing every failure and every
  skip reason. It fails on a missing, unreadable or empty report instead of
  printing an empty table. `tests/test_windows_smoke.py` holds the workflow to
  all three.
- **docs/04 §4.1** lists what Windows still does not do.

#### What was measured, and what was not

Dispatched runs of this branch on `windows-latest`, each fixing what the one before measured:

| Run | Commit | Unit tests | Capture |
|---|---|---|---|
| 36445517499 | `ca69f71` | 20 failed, 1,374 passed, 29 skipped | passed, and the deliverables exist: the first Windows capture this workflow has run |
| 36447354548 | `9138b95` | 1 failed, 1,393 passed, 29 skipped, on four workers in 7m05s (serially 11m10s) | passed |
| 36448490697 | `7ce6fa7` | 1 failed, 1,393 passed, 29 skipped | passed |
| 36449547577 | `17f4f1a` | **1,394 passed, 29 skipped, none failed** — the first green Windows run | passed |

The 20 in the first were 18 source-adapter tests whose helper turned
the URL back into a path with `removeprefix("file://")`, one embed test that
compared bytes as text, and the pid the mandatory lock hid. That embed failure
is also the measurement P10-27 acted on: the `content.html` a Windows capture
embedded ended in `</html>\r\n`. The one failure in the second
was the pid again. The lock now recorded it readably, but it named pid 7348
where `Popen` had started 7312. On Windows a venv's `python.exe` is a launcher
that runs the interpreter as its child, so the test now asserts the pid the
holder reports itself. The third failed one line later, for the same reason:
killing `Popen`'s process killed the launcher, and its child still held the
lock when the test took it again. The test now kills the holder by its own pid.
It gives Windows ten seconds for the release, because Microsoft's `LockFile`
documentation says the time the system takes to unlock a terminated
process's locks "depends upon available system resources". POSIX keeps one
immediate attempt.

Not measured on Windows before these runs, and only by them now: the `msvcrt`
branches of the lock, which the cross-process test drives. The weekly run
keeps them measured. The residual race in §4.1, a
dangling link planted between the check and the create, was not reproduced.
Nothing that needs Tesseract ran, and neither did the tests that need the
`mcp`, `office` or `ocr-rapid` extras. The 29 skips are listed by reason in
each run's summary. That the owner is notified of a failed scheduled run is
GitHub's documented default for the user who last edited the schedule. It was
not observed, because no scheduled run of the new workflow has happened yet.

#### What follows

- **A setting that softens a gate has to have a gate to soften.** In
  `ci.yml`, `continue-on-error` keeps an advisory job's red on the pull
  request and off the merge. On a scheduled workflow there is no pull
  request, so the only surface left is the run, and the setting painted it
  green.
- **A step behind a failing step is a measurement that did not happen.** The
  capture was skipped four times. A skip is not a failure, so nothing
  reported that the smoke had not run.
- **A job that never started is still an outcome to report.** A billing stop
  read as success, twice. This is the "absence" trap again, from the platform
  side.

### P10-25 — owner-only on Windows: the profile's ACLs, every entry of them

Spec §6.3 makes an authentication profile owner-only. P10-24 found neither
half of that working on Windows. `chmod(0o700)` there toggles the read-only
attribute and sets no ACL, so a profile the CLI made was as private as
whatever its parent directory passed down. The MCP check read mode bits, which
Windows reports as `0o777` whatever the ACL says. P10-24 made the MCP server
refuse every profile on Windows and say why. This entry replaces that refusal
with a check, and the `chmod` with an ACL.

#### Why the directory's ACL is not enough

On POSIX, `0700` on the directory is the whole rule. Nobody else can traverse
into it, so nothing inside needs checking. Windows grants every user "Bypass
traverse checking" (`SeChangeNotifyPrivilege`) by default, which Microsoft's
privilege-constant reference says "causes the system to skip all traversal
access checks". A file inside a directory nobody else can open can still be
opened, by path, by anyone the file's own ACL admits. A Chromium profile's
paths are well known (`Default\Network\Cookies`). So the Windows check reads
the owner and DACL of every entry, parents before children, and names the
topmost one that fails.

#### The rule

An entry passes when its owner is you, SYSTEM or Administrators, and every
allow ACE names one of those three, CREATOR OWNER, OWNER RIGHTS, or an
AppContainer package or capability SID (`S-1-15-2-*`, `S-1-15-3-*`).

- **SYSTEM and Administrators** can read any file anyway, through the backup
  and take-ownership privileges. They are allowed for the same reason root is
  outside POSIX mode bits.
- **The owner is checked** because an owner can always rewrite the DACL. An
  entry owned by someone else is access that can be granted at any time.
- **AppContainer SIDs only narrow access.** Microsoft documents an
  AppContainer's access as "the intersection of that granted by the
  user/group SIDs and AppContainer SIDs". Such an ACE admits no user that the
  rest of the DACL does not already admit. The rule came from the
  documentation, before any run. The first Windows run showed Chromium needs
  it.
- **Deny ACEs are skipped**, because they only take access away. The scan
  continues past them, so a grant after a deny still fails.
- **Everything else fails.** A NULL DACL fails, because it grants everyone
  full access; a FAT or exFAT volume reports one. So do an ACE type the check
  does not parse, an ACL it cannot read, a directory it cannot list, and a
  link or junction inside the profile, which the walk does not follow. CREATOR GROUP
  fails too, because it is replaced by the creator's primary group, which is
  not you.
- **Inherit-only ACEs count.** An `(OI)(CI)(IO)` grant to Everyone gives the
  directory itself nothing, and gives every file created in it everything.

#### The fix

- **`src/webshot/winacl.py`** is the one module that calls the Windows
  security API, through ctypes. pywin32 is in the lock only as a Windows
  transitive dependency of the `mcp` extra. Relying on it would have tied the
  base install's check to an optional extra. No ctypes value leaves the
  module: callers get `Security`, `Ace` and `Exposure`.
- **The CLI** creates a new profile with `CreateDirectoryW` and a protected
  DACL granting you, SYSTEM and Administrators full control, inherited by
  everything created inside. The directory is created with that DACL rather
  than given it afterwards, so it never carries its parent's ACL, even
  briefly. An existing profile that fails the check gets the same DACL through
  `SetNamedSecurityInfoW`, which pushes it down to every entry that inherits.
  That is the Windows `chmod 700`, and it logs a warning. What the rewrite
  cannot reach is an ACE set on an entry itself, or an owner who is someone
  else. The CLI refuses those with exit 2 and names the entry and the `icacls`
  command that fixes it. It does not strip such an ACE, because the program
  that set it may need it, and Chromium sets some (below).
- **The MCP server** runs the same check and refuses by name. When the
  problem is on the profile directory itself, the refusal says that a capture
  run from a terminal fixes it.
- **Types.** mypy treats a `sys.platform == "win32"` block as unreachable on
  any other platform, so the ctypes calls, and the `msvcrt` lock before them,
  had never been type-checked. `mypy --platform win32` now runs in the lint
  job and in `verify.py`. Over the rest of the tree it found nothing. Its only
  findings were two `c_void_p.value` reads in this change that can be `None`.
- **The weekly Windows run** captures twice into one `--auth-profile`, then
  `tools/ci/profile_acl.py` reads back every entry Chromium wrote. It prints
  each owner and ACE with a count, names each entry that carries an explicit
  ACE for anyone else, and runs the check. It fails on fewer than 10 entries,
  because an empty directory passes any ACL check.

The spec's "(warn + prompt override flag)" described a flag that never
existed. The CLI has always repaired with `chmod 700` and never prompted.
§6.3 now says what the CLI does, and that there is no override. docs/10's row
for §6.3 claimed a "unit test on profile creation". Only the refusal test
existed. `test_a_posix_profile_is_created_0700_whatever_the_umask` and its
Windows counterparts are the creation half.

#### What the tests hold, and where

| Layer | Runs on | Held by |
|---|---|---|
| The rule, one ACL at a time (`judge`) | every platform | 20 cases in `tests/test_winacl.py`, including each way to fail above |
| The walk: every entry read, topmost named, links refused, an unreadable ACL fails, a vanished entry skipped | every platform, a real tree with the reader replaced | `tests/test_winacl.py` |
| Create, repair, refuse by name (`make_owner_only`) | every platform, writers replaced | `tests/test_winacl.py` |
| The capture path uses the ACL on Windows and never calls `chmod` there | every platform, `sys.platform` patched | `tests/test_winacl.py` |
| The MCP refusal names the entry and the fix | every platform, `sys.platform` patched | `tests/test_mcp_policy.py` |
| The ctypes reader and writers | Windows only | eight tests, skipped elsewhere |
| A profile Chromium wrote | Windows only | `windows-smoke.yml` and `tools/ci/profile_acl.py` |

The Windows tests check the module against sources other than itself. The
user SID is compared with `whoami /user`. The exposures are ACEs that `icacls`
added. The refusal's remedy is tested by running the `icacls` command it
prints, exactly as printed, and checking again.

#### What was measured, and what was not

Dispatched runs of this branch on `windows-latest` (CPython 3.12):

| Run | Commit | Unit tests | The profile two captures left |
|---|---|---|---|
| 36452457959 | `63753ce` | 1,442 passed, 33 skipped | 80 entries, all owned by the runner's user. The directory carried exactly the three ACEs it was created with. Chromium had added ACEs of its own for one capability SID, `S-1-15-3-1024-395641907-…-3061173417`, and one deny-Everyone ACE. The check found nothing, and the second capture logged no repair |
| 36453784422 | `cb4c92f`, with `master` merged in through #111 | 1,464 passed, 33 skipped | The same 80 entries and the same ACEs, now named. The capability SID is granted on `Default\Cache`, `Default\Network` and `Default\Shared Dictionary`, each as an effective ACE plus an inherit-only copy, and inherited below them. The deny-Everyone ACE is on `Default\chrome_debug.log`. The check found nothing |

The first run's counts are #111's last run (1,394 passed, 29 skipped) plus
exactly this change's tests. 48 more ran, and 4 more were skipped: the two
POSIX-mode tests and the two that make symlinks. So all eight Windows-only ACL
tests ran. The second run's larger count comes from the `master` merge.

Without the AppContainer rule, the second capture would have refused every
profile Chromium had used once. The run read the capability SID, not its
name, so which Chromium process it serves is not known.

Not measured:

- **Another user reading an exposed file.** The runner has one account. The
  traverse bypass is from Microsoft's documentation. The tests show the check
  catches such an ACE, not that a second account could have used it.
- **The repair on a real Chromium profile.** It was measured on a small tree
  (`test_an_inherited_grant_is_replaced_down_the_tree`). Every profile the
  smoke made was created owner-only, so it never needed repairing.
- **An owner other than you.** Every entry the runner created was owned by
  its user, although that user is an administrator. The owner rule was
  exercised only with the reader replaced.
- **Paths past `MAX_PATH`.** Every call uses the `\\?\` form, but no entry
  measured was that long. FAT and exFAT volumes and cloud-sync placeholders
  were not measured either.
- **The MCP server end to end on Windows.** The weekly run does not install
  the `mcp` extra. `check_profile_ready` itself ran, because its tests import
  no SDK.

#### What follows

- **Proving a directory private proves nothing about its files unless the
  platform enforces traversal.** POSIX enforces it, and Windows, by default,
  does not. The same rule written as "check the directory" would have given a
  green check over readable cookies.
- **Code under a platform guard is type-checked only where it runs.** The
  guard that stops it executing elsewhere also stopped mypy from reading it.
  `--platform` puts that back for the cost of one more mypy run.
- **Write the allowance from the documentation, then measure it.** The
  AppContainer rule existed before any run, and the first run showed the
  profile depends on it. Tested only on trees the tests built, a check without
  it would have passed CI and failed every real profile on its second use.

### P10-26 — onnxruntime's Windows events are switched off by a call, and the call cannot reach all of them

Numbered into P10 as P10-14's Windows half. P10-24 recorded "onnxruntime
telemetry is not switched off" as a known Windows limitation. This entry
decides it: WebShot now calls `onnxruntime.disable_telemetry_events()` on
Windows. It also records what that call cannot do, because the docs would
otherwise claim more than the call delivers.

#### What a Windows onnxruntime writes, and when

Read from onnxruntime 1.30.0's source, the locked version.
`core/platform/windows/telemetry.cc` registers a TraceLogging provider,
`Microsoft.ML.ONNXRuntime` (`{3a26b1ff-7484-7484-7484-15261f42614d}`), in the
Microsoft telemetry provider group, and writes its events with
`MICROSOFT_KEYWORD_MEASURES`. Nothing is written unless an ETW session has
the provider enabled. The operating system's own collector is such a
session, and what it sends to Microsoft follows the user's diagnostic-data
setting (upstream's `Privacy.md`).

- **`ProcessInfo`**, once per process: the runtime version, CPU model,
  processor count, total memory, whether a debugger is attached, and the
  names of the Windows services hosted by the process. It is written by
  `Environment::Initialize`. The Python binding creates the environment in
  `PYBIND11_MODULE`, that is, while `import onnxruntime` loads the native
  module and before a single line of Python can run.
- **`RegisterEpLibraryStart`** and **`RegisterEpLibraryEnd`**, also once per
  process and at the same moment: `Environment::Initialize` registers the
  internal execution providers straight after `ProcessInfo`. They carry the
  registration name, the runtime version and whether it succeeded. The
  trace found them, and the source reading had not. With `ProcessInfo` they
  are the "minimal initialization event" `Privacy.md` warns about.
- **Per session**: `SessionCreationStart`, `ModelLoadStart`/`End`,
  `SessionCreation` and `SessionCreationEnd`. `SessionCreation` carries the
  model's file name (the name, not the path), its producer, domain, graph
  name, graph and weight hashes, metadata, and the execution providers and
  hardware used.
- **Per run**: `EvaluationStart`/`Stop`, and `RuntimePerf` with run counts and
  durations. Errors add `RuntimeError`.

Every event, the three above included, returns early when a static flag,
`enabled_`, is false. It starts true, and `disable_telemetry_events()` sets
it to false. The ETW enable callback sets it too: to `IsEnabled != 0`, each
time any session enables the provider, disables it or asks it for its state.
So the call holds until the next such notification, and no longer.

`ORT_DISABLE_TELEMETRY`, the POSIX switch (P10-14), is not read on Windows.

#### Who already makes the call

WebShot reaches onnxruntime through other packages, and they differ.

- **magika 0.6.2** calls `disable_telemetry_events()` itself, on the line
  before it builds its session. The MarkItDown route was therefore already
  quiet after the import-time events. That is magika's choice, and nothing
  checked that it stays.
- **RapidOCR 3.9.2** builds its three sessions (detection, classification,
  recognition) without the call. `--ocr-engine rapid` is the engine that
  needs no system binary, so it is the one a Windows user without Tesseract
  would choose.
- **docling** ships onnxruntime engines for its OCR and layout stages.
  WebShot's HTML backend does not reach them: P10-15's full capture ended
  with onnxruntime not loaded.
- **`webshot doctor`** imports MarkItDown, and so onnxruntime, to check it.

#### The decision: make the call

P10-14 set the POSIX switch because a capture tool must not report its
use to a third party behind its user's back. The Windows events report the
same thing: which models this process loaded and how often it ran them.
One package deciding to be quiet, and another not, is not a policy.
`Privacy.md` points to the user's diagnostic-data consent, which is real,
but it is system-wide. It is consent to Windows' diagnostics, not a choice
anyone made about this program. WebShot already overrides onnxruntime's
default on POSIX, and a Windows user is owed the same default.

So on Windows the package init installs a meta-path hook. When anything
imports `onnxruntime`, the hook asks the finders behind it for the real
spec, wraps the loader, and calls `disable_telemetry_events()` once the
package has executed. The call is made before the `import` that asked for
it returns, so magika, RapidOCR, docling or the caller's own code gets the
module back already quiet. If onnxruntime was imported before WebShot was,
the call is made at once.

- **One switch.** The hook reads `ORT_DISABLE_TELEMETRY` when onnxruntime
  imports, as onnxruntime reads it at initialization, with onnxruntime's
  parse: ASCII whitespace trimmed, case ignored, `1`, `true`, `yes`, `on` or
  `y` is off. The package init's `setdefault` makes WebShot's default off,
  and a caller's `0` opts back in on both platforms.
- **POSIX is unchanged.** The hook is not installed there. With the variable
  set, onnxruntime latches `telemetry_disabled_`, and the call would be a
  no-op. Installing it anyway would change the import path on the two gated
  platforms for nothing.
- **A missing API is reported, not fatal.** If an onnxruntime has no
  `disable_telemetry_events`, the hook warns (`RuntimeWarning`, naming the
  version) and lets the import finish. Failing the import would fail every
  document read on Windows over a privacy default. Saying nothing would
  leave the events on while docs/04 says otherwise.
- **The code moved.** P10-14's `setdefault` is now in
  `webshot/onnxruntime_telemetry.py` with the hook, so both halves of the
  policy sit in the one module that touches onnxruntime.

Rejected:

- **A call beside each importer** (the MarkItDown bridge, `ocr/rapid.py`,
  `doctor.py`). Three call sites today, docling's engines are a fourth route
  the day WebShot reaches them, and the next dependency to load onnxruntime
  would be unguarded without anyone noticing: P7-6, a guard covers what it
  covers. A post-import hook covers every route, including ones WebShot does
  not know about.
- **`ORT_RUNNING_UNIT_TESTS=1`.** It is the only switch that stops the Windows
  provider from registering, and so the only one that would stop
  `ProcessInfo`. Upstream's source calls it "an internal hard-suppression
  signal, unlike the user-facing non-Windows environment opt-out", and
  `Privacy.md` does not mention it. Setting a variable named for
  onnxruntime's own unit tests in every user's process, and every child it
  starts, means inheriting whatever it is made to mean next.
- **Relying on magika.** It covers the MarkItDown route only.

#### What the call does not do

- **The three import-time events are still written**, in every process that
  loads onnxruntime, when an ETW session has the provider enabled. Nothing
  reachable from Python runs early enough to stop them.
- **A trace session that enables the provider after the call switches the
  events back on** for the rest of that process. A diagnostic tool's trace
  started mid-capture would do it, and so would the OS collector if it
  enabled its providers again. WebShot does not repeat the call before each
  inference session, because it does not build them.
- **Sessions built before WebShot was imported** were already reported. That
  can only happen in a library caller's own process.

docs/04 §4.1 keeps a bullet for these three, in place of "not switched off".

#### Measured

On macOS (the POSIX CI runs the same), in a fresh interpreter with the
variable removed, the hook installed by hand, and a spy on the two
onnxruntime functions that matter: every `disable_telemetry_events()` call
with the module that made it, and every session built. Each route is taken
far enough to build a session.

| route | in order |
|---|---|
| MarkItDown (a CSV fixture) | WebShot's call, magika's call, 1 session |
| RapidOCR's recognizer | WebShot's call, 3 sessions |
| MarkItDown, WebShot's call removed | magika's call, 1 session |
| RapidOCR, WebShot's call removed | 3 sessions |

The third row is why the test asserts *whose* call came first. Without the
fix, "a call came before the first session" still holds on the MarkItDown
route, because magika makes one.

On Windows, `tools/ci/ort_etw.py` in the weekly run, dispatched on this
branch (run 36451146827). For each arm it started an ETW session on the
provider with every keyword and level, ran a child, stopped the session and counted
the provider's events by name in the trace. The direct arms build a session
on magika's model without magika, as RapidOCR builds its own.

| arm | at import | session events |
|---|---:|---|
| default (WebShot's call) | 3 | none |
| opted in (`ORT_DISABLE_TELEMETRY=0`) | 3 | 7: `SessionCreationStart`, `SessionCreation`, `SessionCreationEnd`, `ModelLoadStart`, `ModelLoadEnd`, `EpDeviceUsage`, `ProviderOptions` |
| trace started late | 0 | the same 7 |
| MarkItDown, opted in (magika's call only) | 3 | none |

"At import" is `ProcessInfo` and the two registration events, one each. The
decoded total `tracerpt` gave for the provider equalled the sum by name in
every arm (3, 10, 7, 3), so no event went unnamed.

Read row by row:

- **The call works.** Under WebShot's default the session left no trace.
  With the switch opted back in, the same child wrote seven events for one
  session.
- **The control saw the events**, so the empty default row is a
  measurement, not a trace that could see nothing.
- **The late trace re-enabled them.** The child made the call, then the
  trace started, then the session was built: all seven came back. That is
  the enable callback overwriting the flag, measured.
- **magika's own call holds its route** even with WebShot's opted out.
- **Per-run events were not observed.** No arm ran a session with the
  events on, so `EvaluationStart`/`Stop` and `RuntimePerf` are read from
  the source, not measured.

The run first failed. `tools/ci/ort_etw.py` counted the two registration
events as session events, so the default arm reported "2 session events".
The trace was right and the classification was wrong. They are now the
import-time group, and that run's counts are the tool's test fixture. The
run after the fix (36452379419) recorded the same counts in every cell and
passed.

Both runs' unit tests include this entry's: 1421 passed and one failed in
the first (a journey test whose page load the runner refused with
`ERR_NO_BUFFER_SPACE`), and all 1422 passed in the second. There the hook
is installed by the package init, not by hand, and the MarkItDown route's
ordering test passed. The Windows run does not install RapidOCR, so its
route was measured on macOS only.

#### The tests

`tests/test_onnxruntime_telemetry.py` holds the ordering in a child
interpreter on each route. It asserts that WebShot's call is made, once, and
before the first session, and that the route built a session at all. A route
that builds none fails as measuring nothing. Removing the call fails both
routes, as the table above shows. The same file checks the hook is installed
on Windows only, the switch's parse against onnxruntime's, the
already-imported case, and the warning for a missing API.
`tests/test_ort_etw.py` holds the Windows tool's verdict. A trace in which
the control arm saw nothing fails as measuring nothing, and does not pass as
clean.

### P10-27 — bundle text is written with `\n` on every platform

The Windows run P10-24 reports (36445517499) embedded a `content.html` that
ended in `</html>\r\n`. Every text writer in `src/webshot` called
`write_text(..., encoding="utf-8")` or `open("w"/"a", encoding="utf-8")`
without `newline=`, which writes `os.linesep`. The manifest hashes the files
as written (`checksummed_files` → `sha256_file`), so its digests described the
Windows bytes correctly. But the same capture had different bytes and
different digests depending on the machine that made it, and §4's Determinism
row requires text and JSON artifacts to be byte-identical.

**Decision: write `\n` everywhere.** A reader in text mode gets the same text
from either spelling, so `\n` costs a Windows consumer nothing. `\r\n` costs
anyone who compares bytes or digests across machines, and anyone who splits on
`\n`. `newline="\n"` does not translate, and neither does `newline=None` on
macOS or Linux, so POSIX output does not change and no golden moves. The
exception is `tables/*.csv`. They are written with `newline=""`, so the csv
module's RFC 4180 `\r\n` is the same on every platform already. They stay as
they are.

**Measured.** CPython's C text layer fixes at build time whether it translates
(only `MS_WINDOWS` builds do), so patching `os.linesep` alone does nothing on
macOS. The pure-Python `_pyio` reads `os.linesep` at each open, and pathlib
opens through `io.open`. Routing `io.open` to `_pyio.open` with
`os.linesep = "\r\n"` therefore translates the way Windows does. An unpinned
probe write of `a\n` comes out as `a\r\n`. Four captures ran through that
layer on master's code: the LMS page with its Guidde walkthrough, the caption
page over loopback, `professional_page.html` (which has a table), and the
protected-viewer fixture. Leaving out the CSV, 49 of the 51 UTF-8 files in the
four bundles carried a translated `\r\n`, for example `content.json` 639 times
and `manifest.json` 105. The two that did not were the `source/` copies of the
input pages, which are copied byte for byte. The PDFs' text attachments carried the same counts as
the files. Natively the same four bundles held no `\r` outside the CSV.

Pinning the 39 writers left one file translated: `ocr/text-layer.txt`, with
48 `\r\n`. OCRmyPDF 17.12.1 writes it, not WebShot. `merge_sidecars` opens
the sidecar with `open('w', encoding="utf-8")`, and `copy_final` copies those
bytes to the path WebShot names inside the bundle. The static scan below
cannot see a third-party writer, so it could not have found this one.
`add_text_layer` now replaces `\r\n` with `\n` after `ocrmypdf.ocr` returns.
That exactly undoes the translation, because `merge_sidecars` builds the file
from page text it has just read back with `read_text`. Universal newlines
leave none of the text's own `\r`, so every `\r\n` in the file comes from the
translation.

**The boundary.**

- *Chunk digests* (`extract/chunks.py`) are computed over the in-memory
  `text`, and `json.dumps` escapes `\n` and `\r` inside a string. A file's
  line endings reach only the record separators, never a digest.
- *Readers.* Every reader of these files opens them in text mode with
  universal newlines: `journey/index.py`, `journey/media.py`,
  `stamp_chunk_paths`, the two in-place rewrites of `content.md` in
  `bundle/videos.py`, and the MCP service. That is what hid `\r\n` on Windows.
  With `\n` on disk they read the same text. The `content.md` rewrites would
  also turn a `\r` the content carries into `\n`, which would leave
  `content.md` disagreeing with `content.txt`. So the question was whether one
  can arrive. When `docling_bridge.extract` is handed `<pre>first
  line&#13;\nsecond line</pre>`, it keeps `\r\n` in the Markdown, the text and
  the chunk. A real capture of that page does not get that far:
  `content.html` already reads `first line\nsecond line`, and no bundle file
  holds a `\r`. The readers were left alone.
- *The golden corpus cannot see this class.* `snapshot` reads bundle text with
  `read_text`, so a `\r\n` bundle and a `\n` bundle snapshot identically, and
  the corpus is recorded on macOS. Changing it to exact reads would put the
  CSVs' `\r\n` into every table golden, a re-recording with no change in
  output. The harness is unchanged, and `tests/test_line_endings.py` holds the
  property.
- *Not covered:* stdout (`webshot schema` without `--write`, the journey
  outline echo) and the writers under `tools/`. Neither produces bundle files.

**Tests.** `test_no_text_artifact_carries_a_translated_newline` runs the four
captures through the translating layer and checks every text file and every
text attachment for `\r\n`. For a CSV it checks for `\r\r\n`. It also requires
each case to have written its expected files, so a case that stopped producing
one cannot pass by checking less. `test_every_text_writer_says_which_newline_it_writes`
reads every `write_text` and every text-mode `open`/`fdopen` with a writing
mode in `src/webshot`, and requires `newline="\n"` or `""`. It also requires
that the scan found the web path's own writers, and a sibling test runs it on
a snippet with one call of each shape. Against master's code, all four capture
cases and the scan fail, and they pass with this change. The limits are
stated in the file: the emulation reroutes `io.open` and not builtin `open`,
and the scan does not see a mode computed at run time. The protected case
skips where Tesseract is absent, as on the Windows runner.

**The first Windows measurement.** Run 36463148043 (2026-09-28, on #116's
branch, which carried #111 and #112) is the first Windows run in which these
tests reached their assertions. `[captions]` passed. `[protected]` skipped,
because the runner has no Tesseract. `[tables]` and `[walkthrough]` each failed
on one file: `source/professional_page.html` with 80 `\r\n` and
`source/lms_page.html` with 79, the two fixtures' line counts. Every file
WebShot wrote itself, and every PDF attachment, came out with `\n`. The
flagged files were the fixtures as git checks them out on Windows, with
`\r\n`, copied byte for byte as §2.2 of docs/04 requires. The check had
forgotten that `source/` holds the input and is not something WebShot writes.
#112's own Windows run (a4f6169) could not have shown this: it predated #111,
so the job still had `continue-on-error: true` (P10-24).

`source/` is now exempt from the newline check, and each case compares its
copy with the fixture's bytes instead. A third test captures a page whose own
newlines are `\r\n` on every platform. It fails everywhere if the exemption
goes away, if the copy rewrites the input's newlines, or if they reach any
other file. With the two fixtures converted to `\r\n` locally, master's test
fails the same two cases the Windows run did, and the new one passes.

Found on the same boundary and not fixed here: `stamp_chunk_paths` and the
harness's `chunk_digest_failures` split `chunks.jsonl` with
`str.splitlines()`, which also breaks at U+2028 and U+0085. `json.dumps(...,
ensure_ascii=False)` writes those raw. A captured `<pre>` holding them gave 3
records and 5 lines, and the first fragment raised `JSONDecodeError`. It is
filed as its own change, P10-28.

### P10-28 — `splitlines()` cut a chunk record at U+0085, U+2028 and U+2029

Every chunk writer spells a record `json.dumps(record, ensure_ascii=False)`:
`write_chunks`, `append_chunk_records`, the protected-viewer builder, and the
rewrite in `stamp_chunk_paths`. That escapes U+000B, U+000C and
U+001C–U+001E, where `str.splitlines()` also breaks a line, but it writes
U+0085, U+2028 and U+2029 raw. The journey capture and the golden and parity
harnesses read `chunks.jsonl` with `splitlines()`, so a record whose text held
one of the three came apart.

**Measured.** A page of three `<pre>` blocks holding `one\u0085two`,
`three\u2028four` and `five\u2029six` (Python escapes), captured with
`uv run webshot page.html --output out.pdf --no-ocr`: exit 0, three records in
`chunks.jsonl`, 3 lines by `split("\n")` and 6 by `splitlines()`. `<pre>`
kept all three characters. In a `<p>`, U+2028 reached the chunk as a space
(the other two were not tried there). On that file:

- `stamp_chunk_paths`, which writes a journey module's place onto its chunks,
  raised `JSONDecodeError: Unterminated string` on the first fragment. It had
  already opened the file for writing, so the module's `chunks.jsonl` was left
  at 0 of its 786 bytes. Nothing catches the error before the journey CLI,
  which logs it and exits, so the journey stopped at that module. The module was not yet in the resume file,
  so a rerun would capture it again and meet the same record.
- The golden harness's `chunk_digest_failures` reported six "is not JSON"
  problems for a well-formed file, and `chunk_pointer_failures` raised.
- `_normalize_chunks`, which masks each chunk digest in the golden snapshot,
  kept each fragment as it stood and rejoined them with `\n`. It masked 0 of
  the 3 digests, so a recording would have pinned them (P7-13), and it
  recorded each separator as a newline.
- `compare` split both sides the same way. A golden whose only change was
  U+2028 to U+2029 printed `CHANGED bundle/content.txt:` and no line.
- Parity's `_chunk_lines` raised, and so did `break-chunk-provenance`.
  `drop-first-chunk` counted a one-record file as two chunks.

All of them now split at `\n` only, which is the separator the writers write.
Parity's readers share one splitter, `chunk_rows`. `stamp_chunk_paths` also
parses and stamps every record before it writes the file. A line that is not a
chunk record (not JSON, JSON that is not an object, or a `meta` that is not an
object) raises `ValueError` and leaves the file as it was. The first version of
this fix parsed first but still stamped after opening the file, so the last
three kinds of line emptied it one step later; the PR's review found that.

**Tests.** The fixture, `tests/fixtures/chunks_line_separators.jsonl`, is the
`chunks.jsonl` the capture above wrote, byte for byte. It holds no path.
`tests/test_jsonl_line_separators.py` has one test per defect above, and all
six fail against the old code. A seventh runs four lines that are not chunk
records (`{oops`, `null`, `[1, 2]`, and a record whose `meta` is `null`)
through `stamp_chunk_paths`. Against the old code every one leaves the file
empty. Four more guard
the fixture itself: each character is raw and inside a record's text, the file
has 3 records and 6 `splitlines()` lines, and `write_chunks` reproduces it byte
for byte. Without those, an editor that escaped the characters would leave
every reader test passing against the old code too. No golden, parity
baseline or fixture chunk file held any of the three characters, so nothing
was re-recorded.

**Left alone.**

- The MCP service iterates the file object, which splits at `\n`, `\r` and
  `\r\n`. `json.dumps` escapes the last two inside a string, and iterating the
  fixture gives 3 lines.
- The harness's OCR scrubber splits recognized text with `splitlines()` to
  make fragments to remove. That is not a record split, and a smaller fragment
  only removes more.
- Parity's `_drop_table_row` splits a table CSV with `splitlines()`. A quoted
  CSV cell can hold `\n` too, so no line split is right there. It runs only on
  the recorded baselines, and none holds a separator.
- `_as_paragraph` in `bundle/videos.py` and the legacy quote in
  `extract/legacy.py` split text with `splitlines()` to build Markdown, so a
  U+2028 there becomes a line break in `content.md`. They build text rather
  than read records.
- The tests' own readers still use `splitlines()`: 16 reads of a chunk file
  in 10 files, found by listing every `.splitlines()` receiver in `tests/`
  with `ast`. They are in `test_contracts.py` (2), `test_embed.py`,
  `test_journey_capture.py` (2), `test_legacy_bundle.py`,
  `test_mcp_service.py`, `test_ocr_engines.py`, `test_office_formats.py`,
  `test_shadow_and_frames.py` (2) and `test_video_capture.py` (5). They read
  captures of fixtures, recorded goldens, or chunks built from test data, and
  none of those holds the characters. On one that did, fifteen would raise
  when a fragment failed to parse or validate. `test_mcp_service.py` compares the service's match count
  with a line count, and the two would disagree. None would pass.
- The other `splitlines()` calls under `src/webshot` and `tools/` split a
  subprocess's output, a version string, a docstring, caption or transcript
  text, or Markdown being built. None reads a bundle's records.
- Nothing scans for a new reader the way `test_line_endings.py` scans for
  writers. Whether a string came from a JSONL file cannot be seen at the call.

### P10-29 — a byte-compared fixture needs its bytes from the checkout, not from core.autocrlf

#117 fixed master's red Windows smoke for `test_line_endings`, but its own
Windows smoke run (36466580787) still failed, on a different test:
`test_jsonl_line_separators.py::test_the_fixture_is_the_writers_own_spelling`
(from #114, P10-28). The rest passed: 1463 passed, 32 skipped. The failure
diff showed `...}}\r\n` in the fixture where `write_chunks` writes `...}}\n`.

The fixture was not wrong. The repository has no `.gitattributes`, and the
Windows runner's git checks text out with `core.autocrlf` on. That turns each
of the fixture's three `\n` line ends into `\r\n` on disk. The test reads the
fixture's bytes back and compares them with what the writer produces today;
that comparison is the point of the test, which exists to prove the fixture
still holds the raw U+0085, U+2028 and U+2029 separators. So on Windows it was
comparing the checkout's end-of-line policy, not the code.

Measured on this Mac by simulating the Windows checkout:
`git -c core.autocrlf=true clone` gives master's fixture 3 CR bytes, and the
same test fails with exactly the Windows diff. The repository blob has none.

The fix is one line in a new `.gitattributes`, which turns end-of-line
conversion off for that one file: `tests/fixtures/chunks_line_separators.jsonl -text`.
The same simulated checkout of the fixed branch gives the fixture 0 CR bytes,
and all 15 tests in the file pass. The rule names that single file on purpose.
A pattern such as `*.jsonl` would also reach `sample_records.jsonl`, a capture
input whose `source/` copy #117 now holds to the input's own bytes. A blanket
`text eol=lf` over `tests/fixtures/**` would also mark the binary fixtures
(fonts, PDFs, images) as text. The test is unchanged: it still fails if the
fixture loses a separator or drifts from the writer.

**Widened the same day (#119).** The narrow scope was argued against `*.jsonl`
and `text eol=lf`, and neither argument holds for `-text`. `-text` leaves a
binary file exactly as a binary is already left. The `source/` copy of
`sample_records.jsonl` is held to its input's own bytes, and those are now the
same bytes on every platform. What the narrow rule left exposed is the one
committed file that holds CR: `tests/fixtures/guidde/subtitles-reference.vtt`
is CRLF as captured, and a commit from a Windows checkout would have quietly
renormalized it to LF. The rule now covers all byte-compared test data:
`tests/fixtures/**`, `tests/golden/**` and `tests/parity/**`, each `-text`. After
it, `git add --renormalize .` changes nothing. A simulated
`git -c core.autocrlf=true clone` goes from 789 files with converted line endings
under `tests/` to none in those three trees, and all 726 of their files check out
byte-identical to their blobs. `eol=lf` was not an option: it marks the files as
text, and the `.vtt` would be renormalized on the next add.

## P14 — a signed-in reading page, captured from a browser export (2026-09-06)

Captures of a signed-in reading page, exported from the browser and captured for
real use rather than as a spike, and measured the same way. The recipe they
produced lives outside this repository, and the script names below are its; the findings are what the
recipe, WebShot and the site did that nobody had written down. Entries that
concerned only the recipe have been removed. The rest keep their numbers, so
citations stay valid and the numbering has gaps.

### P14-1 — an empty page was a successful capture

The site serves an empty document to a browser with no session. WebShot
captured it: **exit 0, 1 page, 35.4 KB, `text_characters: 0`, `items: 0`,
`warnings: []`**, `content.md` and `content.txt` one byte each, `chunks.jsonl`
empty. Nothing in the report or the manifest says the capture is empty. The
"absence must not read as success" trap in CLAUDE.md, in the pipeline's own
output. Flagged for a fix with a fixture; not fixed here.

### P14-2 — Tesseract on an icon-heavy diagram

A five-stage process diagram (one label per stage, each beside an icon) under
every segmentation mode, on the 1520×856 harvested screenshot and on the
8000×4500 original downscaled to 3000 px:

| psm | output |
|---|---|
| 3 | `Q Analyze` |
| 6 | `Plan / = - / 2 / it} Execute Q Analyze / - 9` |
| 11 | `v= / Plan / it} Execute / Q Analyze` (confidence 73.33) |
| 12 | `: / Plan / it} Execute / Q Analyze` |

Two of five labels never recognized; the icons read as characters every time.
The image's alt text named all five. So the recipe runs diagram readings with
`--no-ocr` and keeps OCR for screenshots of code and output, which alt text
never transcribes.

### P14-3 — alt text reaches the text surfaces only beside recognized text

With OCR on, `content.md` carried the alt sentence followed by the OCR lines.
With `--no-ocr` the alt sentence was **absent from `content.md`, `content.txt`
and every chunk** — only `<!-- image -->` remained; it survived in
`assets.json.alt` and in the figure chunk's "Visual asset" record. The cause is
in `capture/visuals.py`: the description assembled from alt, aria-label,
caption and title is written into the DOM for docling only on the OCR path.
The recipe renders alt text as the figure's `<figcaption>`, which reaches every
surface and populates `assets.json.caption`; the tool defect is flagged.

### P14-4 — two editor artifacts that become defects downstream

`<strong>Summary</strong>: The planning…` is serialized by docling as
`**Summary** : The planning…` — a space before the colon, on every text surface.
The site's editor also leaves `&nbsp;` at the end of headings, so the PDF
outline read `Plan ` and `Key takeaways `. Both are handled in the source page:
the colon moves inside the bold run, the trailing no-break spaces go.

### P14-5 — the system font embeds as Type 3

A page set in `-apple-system` produced a PDF whose every text font was
`.SFNS-Regular`, **Type 3**. The same page in "Helvetica Neue" embedded as
**CID TrueType** with a ToUnicode map. Text extraction worked in both (every
word of the source found in order by `pdftotext`), so this is portability, not
correctness — but the header/footer template's `-apple-system` stack remains
Type 3 in every WebShot PDF.

### P14-6 — the PDF holds the better copy of the image

Chromium embedded the 8000×4500 diagram at native resolution (386 KB, Flate,
with SMask) even when the figure was displayed at 533 px wide. The bundle's
harvested asset is a 2× screenshot of the rendered element: 1520×856 at full
column width, 1066×600 once the figure was capped to fit its page. An agent
that wants the diagram's pixels should take them from the PDF's page image
object, not `assets/asset-001.png`.

### P14-8 — `$PIPESTATUS` is empty in zsh

Every `EXIT=${PIPESTATUS[0]}` line printed during this work was empty: the
array is `$pipestatus`, lowercase and 1-indexed, in zsh. A guard written as
`if [ "$rc" = "0" ]` on that empty value never ran its `rm`, and reported
nothing. The pipe trap in CLAUDE.md, one layer down: the status was not
discarded by the pipe, it was read from a variable that does not exist. On
macOS, where the login shell is zsh, prefer `set -o pipefail` and the
pipeline's own exit status over any status array.

### P14-30 — `webshot exited 134` is intermittent, and it was never Chromium

**Corrected 2026-09-27 by P10-14**, which found the cause and removed it. This
entry first said three things that did not hold:

- **"SIGABRT from Chromium mid-render."** Exit 134 is the Python process's
  own abort, raised by onnxruntime's telemetry thread at interpreter exit. A
  renderer that crashes reaches WebShot as a Playwright error and a §3 code.
- **"The run leaves no PDF."** This was never measured.
  `capture_reading.py` stops at the exit code without looking. Every abort
  P16-3 examined had already written the PDF, the bundle and the report, and
  so had the one reproduced for P10-14.
- **"A plain retry is the remedy."** It was the heading. A retry worked
  because the race depends on timing, not because the race is harmless.

After P10-14, an exit 134 is a crash to diagnose. Retrying past it is not the
remedy, and the crash report macOS writes for it is the first thing to read.

Three readings have now failed with SIGABRT (exit 134): Module 3 item 08's
exemplar notebook, and Module 4 items 16 and 19. Each succeeded on a
plain retry with `--replace` and no other change — item 19 needed two, which is
worth knowing, because one failed retry looks like evidence that the input is
at fault and it is not. Item 19's third run produced 18 pages and passed every
check.

It is a crash, not a bad capture: the run exits non-zero, so
`capture_reading.py` reports FAIL and delivers nothing, whatever the run had
already written. The failure mode was loud and the work resumable, so the cost
was a retry rather than a correctness risk, and `deliver_module.py`'s
accounting pass is what catches an item left behind by one.

The same fault has a second face in `tools/verify.py`. One run reported
`FAIL golden corpus` whose own detail section said "All 26 case(s) match the
recorded goldens (strict profile)", followed by
`libc++abi: terminating due to uncaught exception of type std::__1::system_error:
recursive_mutex lock failed: Invalid argument` — a crash after the comparison,
not a difference in it. `golden corpus (second path)`, which renders the same
corpus again, passed in that very run, and re-running `tools/golden/check.py`
alone exited 0. Read the failing check's own output before touching a golden:
a step can exit non-zero having already reported that every case it compared
matched, and re-recording on that evidence would be recording a crash.

It is worth knowing how misleading the correlation looks while you chase it,
because the obvious suspect is whatever you just changed. Seven full runs, over
two commits and three checkouts:

| commit | checkout | runs | crashed |
|---|---|---|---|
| the new work | main | 2 | 2 |
| the new work | a fresh worktree | 1 | 1 |
| its parent | a second fresh worktree | 3 | 0 |
| the new work | *that same second worktree* | 1 | 0 |

After six runs the split was perfect — three crashes on the new commit, three
clean runs on its parent — which is about as suggestive as three-versus-three
can be. The seventh run is the one that matters: the **same commit** that had
crashed three times passed in the checkout where its parent had passed three
times. Identical code, opposite outcome, so the commit is not the variable.
`tools/golden/check.py` also passed seven for seven when run on its own,
including under the step's exact `uv run --frozen --extra dev` command and
under the same `subprocess.run(..., capture_output=True)` call `verify.py`
makes.

The cheap test that separates "my change" from "this machine" is a crossover:
check the suspect commit out *in the checkout where the baseline passed* and
run it there. Comparing two commits across two checkouts confounds the two
variables, and with an intermittent fault the confounded comparison is the one
that looks conclusive.

### P14-36 — a formula reached the PDF and vanished from every text surface

The first page captured with formulas showed a WebShot defect, not one in the
site: Chromium rendered the MathML into the PDF (STIX Two Math, correct
layout), while `content.txt`, `content.md` and `chunks.jsonl` all read "the
median is here." — the formula gone, no warning. The cause is the snapshot's
sanitizer. nh3's HTML allowlist has no MathML elements, and it drops a
foreign-namespace element *together with its content*:

```
sanitize_body_html('<p>the median is <math><mn>8</mn>…</math> here</p>')
→ '<p>the median is  here</p>'
```

SVG survives the same allowlist only because the snapshot turns every
harvested `<svg>` into an `<img>` before sanitizing; MathML had no such pass.
The snapshot now replaces each visible `<math>` in its clone with an HTML
element holding the formula as text — its TeX annotation where the page
carries one (the author's own source, and the form an agent can read), else its
`alttext`, else its linear text — marked `data-webshot-math="tex|alttext|text"`,
a `<div>` for a display formula and a `<span>` inline. That pass has to run
before the clone's invisible elements are removed, because an `<annotation>` is
never rendered and is therefore always marked invisible. No golden contains
MathML, so no recorded output moved; `tests/test_snapshot_math.py` fails
against the old snapshot and passes against the new.

*Corrected by P18-4:* running first also let the linear text read what the
invisible filter would have removed. An `<mphantom>` or a `display: none` term
reached `content.html` inside the formula.

Known gap, recorded rather than fixed: a general page drawn with KaTeX keeps its
`katex-mathml` visually hidden, so the snapshot drops that as invisible and
keeps `katex-html`'s glyph spans, which read as a flattened run. The capture
recipe removes `katex-html` before capture (P14-37); WebShot itself does not.

### P14-37 — one reading, two math renderers

The site draws most formulas with **KaTeX**, which embeds real MathML with the
TeX as its annotation, beside its own visual spans and a spoken-form copy for
screen readers ("left parenthesis, 8, plus, 10…") hidden by a class the
captured page does not have — so it would have printed. The wrapper is
`aria-hidden="true"`, which would also have hidden the formula from the PDF's
tags. Where KaTeX cannot parse the TeX — a literal `$` in "$1,200 ÷ 4" —
the site falls back to **MathJax v2**, which leaves only absolutely positioned
HTML-CSS spans and the TeX in a `<script type="math/tex">`; `window.MathJax` is
not exposed, so MathJax cannot be asked for its own MathML.

`build_reading.normalize_math` replaces each of the site's math-block wrappers with the
MathML its renderer produced: KaTeX's own `<math>`, and for MathJax a tree
rebuilt from its spans, which are named after the MathML elements they draw.
Token and grouping elements map one to one; anything that positions its
children (`mfrac`, `msub`, `msqrt`…) is refused rather than guessed, and the
rebuilt tokens must hold exactly the characters MathJax drew, in order. A
`texatom` holding a single operator is TeX's ordinary atom (`{$}`), so that
operator takes no spacing; as a bare `<mo>` it printed "$ 1,200". Math is set
at 1.2em, as the site's KaTeX sets it (1.21em), and the word check reads a
formula the way the surfaces now carry it — its TeX — and skips it on the PDF
layer, where it is positioned glyphs in a math alphabet. A "Formulas" row in
"About this capture" says how many formulas there are and which renderer each
came from. Measured: 4 formulas in 3 of 13 readings, 3 KaTeX and 1 MathJax.

Notebook markdown is not covered: two probability notebooks carry nine inline
TeX expressions (`$mean - 1 * std$`), and their PDFs print the source as
written. Exact and readable; not rendered as Jupyter would.

### P14-40 — Type 3 for math, and for Lucida Grande

The P14-5 check refuses a Type 3 body font. Two new cases: **STIX Two Math**,
the only OpenType MATH font on the capture machine, is CFF-flavoured, and
Chromium's PDF backend embeds every CFF font as Type 3 — so every formula
failed the check. Its ToUnicode map is present and the formula extracts, and
refusing it means refusing every formula, so a font whose face name ends in
`Math` is now expected, like `.SFNS` (`type3_body_fonts`). The other was a
fallback: "⅙" and "⅓" are not in Helvetica Neue, and Chromium's own fallback,
Lucida Grande, embeds as Type 3. "Arial Unicode MS" after Arial in the stack
supplies only the glyphs the three before it lack, and embeds as CID TrueType
(measured on the same page both ways).

### P14-45 — a prompt authored as a heading emptied the question's section

**Resolved by PR #78 (2026-09-27):** this concerns the chunker's part of the
entry. docling's `HierarchicalChunker` drops a heading whose section has no body
— a heading followed directly by one at the same or a higher level, or one that
ends the page — unless `always_emit_headings` is set. The bridge now sets it, so
such a heading gets a chunk of its own: its text is the heading path, and its
locator is the heading. The fixture is this entry's shape
(`tests/fixtures/empty_section_heading.html`). Five other shapes that empty a
section are tested beside it. The feedback builder still demotes the headings,
for the reason given below (the question's own heading is the document's), but
the chunk stream no longer depends on that. The entry below is kept as it was
written.

Once submitted, two activities have a feedback view, and both captures failed
the word check on one surface only: `chunks.jsonl` "missing 284 words in
order", every other surface clean. Their first question's prompt ("Did you
complete this activity?") is authored as an `<h2>`, which put an h2 under the
document's h3 "Question 1" and left that section with no body; the chunker
drops the heading of an empty section, so no chunk said "Question 1" and the
in-order check derailed from there. The count is the greedy matcher's, not the
loss: one heading was missing. The feedback builder now demotes any heading
inside a prompt or an explanation to a bold paragraph, since the question's own
heading is the document's. None of the eleven other feedback views had one.

One further fact from the same run: the chunker's behaviour is WebShot's, not
the site's. A heading whose section has no body reaches no chunk.

## P16 — public pages, and a print layout that loses text (2026-09-27)

Seven public pages of one site were captured with `webshot` on their live URLs,
alongside pages saved as local HTML from a signed-in browser. Each PDF's text
layer was then
compared with its own bundle's `content.md`, as the share of the Markdown's
word 5-grams found in `pdftotext` output. Pages with nothing wrong scored
0.86–0.96, the misses being image alt text, image placeholders and page chrome.
The two that scored lower were defects.

### P16-1 — a print layout that runs off the page loses text silently

The site's print stylesheet keeps a Bootstrap grid's push offset: a fixed
600 px content column pushed 75 px right, on an 816 px page. Chromium clips it
at the page edge. The first of two affected pages scored 0.63 and the second
0.74. The render cuts every line of the main
column mid-word, and the text layer loses the same words. The bundle's
Markdown, taken from the DOM, has them all. So the PDF says less than the
bundle beside it, and neither the manifest nor the exit code says so.
`--mode faithful` (0.59) and `--scale 0.75` (0.78) did not fix it, and
`--media screen` scored 0.33, with the page's accordions left closed.

A `--css` that stacks the columns in print raised the pages to 0.87 and 0.89.
What is still missing there is image alt text, OCR of a map, and table cells
that `pdftotext` reads in a different order. It is P9-7's class, text that
renders clipped while the DOM holds it whole, but horizontal and page-wide.
P9-7's guard catches only a clamped container. A check needs no new
measurement: after layout at the print width, an element with text whose
right edge passes the page width is the finding. A probe that listed them at
print media found 84 on the first affected page.

*Answered by P16-4.* The check shipped, as a warning. It needed one new
measurement after all: the width, which the paper does not give.

### P16-2 — live captures carry Chromium's ligatures into the text layer

Two of seven live captures had U+FB01/FB02 in the text layer, three glyphs
between them. A search for "first" misses those words, although the page looks
right. The recipe used for the local HTML pages already turned ligatures
off. A live capture has no such step unless `--css` supplies one. Seven
captures with `font-variant-ligatures: none` had none.

*Answered by P16-5.* Every page WebShot prints now has ligatures off, and a
ligature that still reaches the text layer is a warning. The golden corpus had
been recording the defect in five cases.

### P16-3 — the P14-30 abort, after every output was written

A second public page aborted with `recursive_mutex lock failed` (exit 134)
on four of its eight runs, the last three in a row. The first affected page of
P16-1 aborted twice in a row before a clean third run. Each abort came
*after* "Wrote capture report": the PDF, the bundle and the report were
complete, and the PDF opened with all 12 embedded files. So exit 134 does not
always mean a missing PDF, and "a plain retry is the remedy" (P14-30) did not
hold for this page. The other five live pages never aborted in any of their
runs, nor did any of the twelve local-HTML captures. A caller that treats 134 as failure retries forever; a
caller that ignores it would also ignore the mid-render crash that leaves no
PDF. To tell them apart, check the report file's timestamp against the run.

*Resolved by P10-14.* The abort is onnxruntime's telemetry uploader racing its
own static destructor at interpreter exit. The crash report macOS wrote for
each of these runs names it. Every capture loads it, so "this page aborts and
the others never do" was network timing, not the page. WebShot now sets
`ORT_DISABLE_TELEMETRY=1` before onnxruntime can start. The caller advice
above is withdrawn: an exit 134 is a crash again, to be diagnosed rather than
told apart from a good run. The "mid-render crash that leaves no PDF" came from
P14-30's wording, which P10-14 also corrects. No such crash was ever measured.

### P16-4 — the clipping check, and the width it has to ask Chromium for

P16-1's check shipped in `render/clipping.py`. After the render, WebShot finds
every element with a line of its own text past the left or right edge of the
printed page. If any are found, it adds a manifest warning with the count and
a selector for the box that runs off. The warning also reaches the QA report's
`warnings` and the `capture.json` embedded in the PDF. The exit code stays 0,
as it does for OCR degradation, because the PDF is still the page minus what
the edge cut. The fixture is `tests/fixtures/print_overflow.html`, held by
`tests/test_print_clipping.py`. On the first affected page the warning counts
24 elements in the pushed content column. P16-1's probe
found 84 because it counted every box past the edge, containers included.
With a `--css` that stacks the columns, the same page produces no warning.

**The paper does not give the width.** Letter at 0.6 in margins and scale 0.9
lays out at 779 px, not the 816 px P16-1 names. Scale 1 gave 701 px and scale
0.75 gave 935 px. All three fit 526 pt of content (the 525.6 pt between the
margins, rounded to whole points) divided by the scale. Three measured cases
move that number:

- A page's own `@page` margin changes it even with `--prefer-css-page-size`
  off. `margin: 0.5cm` gave 864 px. `@page { size: A5 }` gave 495 px while the
  PDF page stayed Letter. Media queries see none of this: they matched at
  779 px in every case.
- A page wider than 779 px is laid out once more at its own width, up to half
  again (1168 px), and scaled down to fit. A fixed 1000 px column printed all
  300 of its test words. At 1400 px it lost 46. So a check at the paper's
  width would have reported every fixed-width layout between 779 and 1168 px,
  none of which loses a word.
- The first affected page loses text anyway because its container is centred.
  At 779 px the column ends at 940. Chromium lays the page out again at
  939 px, but the centred 750 px container moves right by half of what the
  page gained, so the column now ends at 1020. Chromium does not lay it out a
  third time.

So the check does not compute the width. It prints the first page again with
a fixed-position probe whose `::before` prints `calc(100cqw / 1px)` as a
counter, and reads the number back from the text layer. That is the final
layout width, after `@page` rules and after shrink-to-fit. The viewport is
then narrowed to that width, the lines are measured, and the viewport is put
back. Both the probe and the measurement run after the PDF is written, so they
cannot change it. On this page the render stage went from 0.12 s to
0.18 s, one run each. If the probe prints nothing, the warning says the check
did not run. It never reports that nothing was cut.

**What counts.** A line that crosses an edge is cut mid-line and always
counts. A line wholly past the edge counts only when an ancestor crosses the
edge and no clipping container of its own stops inside the page. The first
condition excludes a closed off-canvas menu, and the second excludes a
carousel's other slides. The page intends both at any paper width. The layout
is emulated through the viewport, so media queries see the laid-out width,
not the paper's. The two agree unless a breakpoint falls between them.

**It warns and does not correct.** P16-1's layout is a pushed column, a pulled
sidebar and a centred container, which is not one property on one element. A
generic neutraliser would have to guess which of them to undo. If it undid the
push and left the pull, the two columns would overlap, and overlapped text is
inside the page, where no check sees it. That would trade a reported defect
for an unreported one. The clamp release (P13-3) can correct because it
restores one element's own box. Here the remedy is `--css`, written by someone
who can see the page, and the warning names it.

**Still not reported:** text that a container clips inside the page. A scroll
wrapper around a wide table loses its right-hand columns the same way, but
the wrapper cuts them, not the paper, and at any width. That is P9-7's class
turned sideways, and it remains open. Frames and closed shadow roots are not
measured either.

*Answered by P16-8* for scrolling boxes, the case the table wrapper is. *Frames are measured since P16-18
and P16-19*, for scrolling boxes and for the page edge. A box
that `overflow: hidden` clips inside the page is still not reported, because
the screen hides that text too.

### P16-5 — ligatures off at print, and what turned them back on

P16-2's workaround is now in `render/ligatures.py`, reshaped by four
measurements. Each was made on `tests/fixtures/ligatures_page.html`, drawn in a
committed Noto Sans subset so it ligates on every machine. A control test
prints the fixture with no preparation and requires ﬀ ﬁ ﬂ ﬃ ﬄ in the text
layer, so the rest cannot pass because the font failed to load.

1. **The letters are in the file, but not where readers look.** Chromium
   writes `/ActualText (fi)` around every ligature. Its `ToUnicode` map gives
   the glyph as U+FB01, though, and pypdf reads the map, as `pdftotext` did in
   P16-2. Normalizing the map after printing would also have been possible. It
   was rejected: a map entry cannot tell a ligature from a ﬁ the author typed,
   and turning ligatures off leaves nothing to normalize.
2. **`font-variant-ligatures` alone is not enough.** With the rule in place,
   ligatures still printed in four places: `font-feature-settings: "kern",
   "liga"` (common typographic boilerplate), `"liga" 1 !important`, a more
   specific `!important` on `font-variant-ligatures` itself, and an open
   shadow root's own stylesheet. The workaround's `font-feature-settings:
   "liga" 0, "clig" 0` closes the first two, but it is one property, so it
   also discards the page's tabular numerals and stylistic sets. That is
   CSS's definition, not something measured here. So the rule is a
   constructed sheet, adopted into the document and each open shadow root of
   every frame, with `none` narrowed to common, discretionary and historical
   ligatures so that `calt` survives. A computed-style pass then finds each
   element that still ligates and rewrites only its ligature tags, inline and
   `!important`: `"tnum", "liga"` becomes `"tnum", "liga" 0`. The fixture's
   four overrides, a `::before` and a `srcdoc` frame all print with plain
   letters.
3. **What the pass cannot reach is measured after printing.** Each font's
   `ToUnicode` map is read for U+FB00–U+FB06, and any it holds becomes a
   manifest warning naming them. A closed shadow root with its own `"liga" 1`
   (`ligatures_unreachable.html`) printed ﬀ and ﬃ and got the warning. The
   running header and footer are separate documents, and the appendices print
   through their own browser. Both now turn ligatures off too. On macOS the
   header's font did not ligate even without that. It is there for fallback
   fonts elsewhere, which were not measured.

   *Corrected by P18-3:* on the web path only. The protected-viewer path
   returned before the check, and now runs it on its own composed file.
4. **`<track>` has no computed style.** Chromium reports `''` for every
   property of a caption track. The first version of the pass read that as
   "ligatures on", and so logged two overrides on every captioned video. The
   `embedded-media` and `captions-read` goldens record stderr, and the
   unexplained line was the finding.

**The golden corpus had been recording the defect.** `pdf-text.txt` held ﬁ or
ﬀ in five cases: `clamped-text` (3), `clamped-noise` (1),
`form-overlapping-passwords` (1), `lms-module-videos` (1, in the video
appendix) and `protected-viewer` (8, in the transcript appendix). A golden
records whatever it is handed. Those five were re-recorded in a worktree at
HEAD holding only this change. Fourteen lines moved, each a presentation form
replaced by its letters. The re-record also moved `lms-module-videos`'s
`asset-002` raster digest and, through `assets.json`, one manifest digest. At
HEAD without this change the same digest came back in two runs. It is P7-14's
font drift, masked by the portable profile, so those two lines were restored
rather than re-pinned. `protected-viewer`'s pdf-text is masked under the
portable profile too, so its stale golden still passed. It was re-recorded
because the output did change.

**What this costs, stated rather than measured:** text is printed without
ligatures, so "fi" draws as two glyphs. An Arabic font that puts lam-alef in
`liga` rather than `rlig` would print it unjoined. No fixture covers that.

### P16-6 — shadow roots and frames printed, and missing from every text surface

Found while making P16-5's fixture. With `--no-ocr`, the PDF's text layer had
the sentence from `ligatures_page.html`'s open shadow root and the one from its
`srcdoc` frame, and `content.md` had neither. Measured on all four text
surfaces, with OCR off and on, both sentences were missing from `content.md`,
`content.txt`, `chunks.jsonl` and `content.html` either way. With OCR on, the
frame's sentence reached `assets.json` only, as OCR of the frame's screenshot.
The only warning was the one every iframe gets, which says cross-origin content
"may" be only in the screenshot.

The snapshot serialized `document.body.innerHTML`, and the records came from
`document.querySelectorAll`. Neither crosses into a shadow root or a frame.
Spec §5.6 already promised DOM-level extraction for frames, and the signed-in
platform the local pages came from keeps a page's body in shadow DOM and an
embedded web page in a same-origin frame (per notes kept outside this
repository). So a warning alone would have left its bundles holding the
navigation and a sentence saying the rest was missing. The snapshot now walks
the flat tree. A host's children are its shadow root's, a `<slot>` holds what is
assigned to it, and a frame the page can script holds its document's body. The
records, the links and the copy all come from that one walk.

Four more defects were on the same boundary:

1. **Passwords.** Playwright's aria snapshot reads open shadow roots and every
   frame. The redaction beside it asked only the top document. A password typed
   into a shadow root, a `srcdoc` frame, or a cross-origin frame served from a
   second loopback origin reached `accessibility.yaml` and the PDF's copy of
   it. That breaks spec §6.2, and it was not caused by this change. Every frame
   is now asked, and each open shadow root in it. A frame that cannot be asked
   withholds the snapshot with a warning. A closed shadow root never appeared
   in the aria snapshot at all.
2. **`display: contents`.** `visible()` needs a box, and such an element has
   none, so text sitting directly in one was dropped with it. Every `<slot>` is
   one, and that is how the fixture found it. Default-slot text and slot
   fallback text were missing after the first version of the walk. It was not new. At
   e19727e, `<p><span style="display: contents">…</span></p>` lost its text
   from `content.html` too. Such an element is now kept when its text has a
   box, and not when it is `visibility: hidden`.
3. **Locators.** For an element in a shadow root, `LOCATOR_JS` ran off the top
   of the root and answered `head > …`. It is now the host's locator, ` >> `,
   and the path inside the root. For a frame it is the same, from the frame
   document's `body`. The test resolves each form-field locator back to its
   element.
4. **Spoofed markers.** `visuals.py` strips page-authored `data-webshot-*`
   attributes from the top document only. Once shadow content reached the
   snapshot, an `<img data-webshot-asset-id>` there would have borrowed another
   asset's OCR. Markers are now dropped from anything outside the top
   document's own tree.

**Asking DevTools switched the page to screen media.** The first version of
the closed-root check opened a DevTools session and detached it, and
`test_print_clipping` then found no clipped text on a page with four clipped
elements. Detaching any session resets the page's emulated media type. On this
build `matchMedia('print')` went from true to false, while the viewport, colour
scheme and user agent survived, and Playwright, which had set print, was not
told. The print-edge check after the render measured the screen layout. The
same detach was already in `_chromium_version`, which opens a session when the
context is a persistent `--auth-profile` one. So every authenticated capture
had its print emulation reset before the render. That blinded the edge check,
and would have printed a `--media screen` capture with print media. Both now
share one session per page that is never detached, and the closed-root check
turns the DOM agent off again when it is done. A control test holds the reset
itself, so a Chromium that stops doing it will say so.

**"Same-process" was the wrong line.** Process is not script access. A
same-site, cross-origin frame shares a process, and no page script can read
it. The line the page itself can draw is whether `contentDocument` is readable,
and §5.6 now says that. Playwright could evaluate inside a cross-origin frame
too. That was not done here, because the spec keeps such frames visual.

What still cannot be read is now named. A frame whose document is unreadable
gets a warning with its locator and what the bundle holds of it: OCR text in
`assets.json`, a screenshot with none, or nothing. A closed shadow root cannot
be seen from the page at all, not even as a root. The DevTools protocol's
pierced DOM reports each root's type, so a closed root holding text is named by
its host. If that check cannot run, the warning says so. A readable frame that
is empty, like `lms_page.html`'s `about:blank` player, leaves nothing behind,
and its goldens do not move.

`tests/fixtures/shadow_frames_page.html` holds one sentence per case, and the
PDF's text layer is the control. Every sentence the bundle must carry is one the
PDF prints, and every sentence it must not carry is one the PDF does not. At
e19727e the fixture's shadow, frame and `display: contents` sentences were
missing from all four surfaces, both passwords were in `accessibility.yaml`,
and nothing warned about the opaque frame or the closed root.

**Not done:**

- An unreadable frame's OCR text still reaches `assets.json` only. An image's
  OCR becomes a figure chunk. A frame never becomes a picture in the snapshot,
  so its OCR does not.
- §5.6's per-asset provenance is still not a field.
- The iframe warning from `visuals.py` is unchanged. For a frame that was read,
  its "may only be available through the screenshot" is now unnecessary. It is
  not false.
- A frame's text is taken whole, including what is scrolled out of the frame's
  box, as the snapshot already does for scroll containers.

*Answered by P16-7*, the first three, and by P16-16 the fourth.

### P16-7 — an unread frame as a picture, its provenance as a field, and one warning fewer

P16-6 left three items open. Closing them found a fourth defect on the same
boundary.

**An unread frame stands in the snapshot as its screenshot.** Measured on
`shadow_frames_page.html` with `--ocr-engine tesseract --require-ocr`: at
fa21e54 the opaque frame's recognized sentence was in `assets.json` and in none
of `content.md`, `content.txt` or `chunks.jsonl`. An `<svg>` already had a path
out. The snapshot swaps it for `<img src="assets/…">`, and the bridge attaches
the asset's OCR to that picture by position. A frame the walk could not read is
now swapped in the same loop. The sentence is now in `content.md` and
`content.txt` once each and in `chunks.jsonl` twice. One copy is the figure
chunk. The other is the text chunk the chunker builds from the picture's caption
and annotation. Docling takes that caption from `alt`, which holds the frame's
title.

Why a picture: the screenshot is all the bundle holds of such a frame, and that
is why an `<svg>` becomes one. Kept in `assets.json` only, the frame's words
were outside retrieval while the PDF printed them. In `content.json` the text is
a `webshot-ocr` annotation, and the warning says the surfaces hold recognized
text, not the frame's own. A frame that was read is not swapped. Its document is
better text than OCR of it, and swapping it would repeat it.

**What this costs, stated rather than measured:** a loaded cross-origin video
player (YouTube, Vimeo, Guidde) is such a frame. Whatever OCR reads off its
controls now reaches `content.md` as a picture's text, as a photo's would. No
live LMS page was captured for this. A `<video>`'s OCR stays in `assets.json`,
because a `<video>` is not a picture in the snapshot.

**`frame_content` is §5.6's per-asset provenance.** Every visual asset has it:
`dom`, `screenshot`, or null for anything but an iframe. The harvest runs before
the snapshot, so it writes `screenshot`, which is what it took.
`with_frame_content` sets `dom` for each frame whose document the walk read. A
frame the walk did not read keeps `screenshot`, which is then true. Other kinds
get null rather than a value, because neither value describes them. A
background-image element or a `role="img"` element keeps its DOM text in
`content.html`, and its screenshot also shows pixels the DOM does not have. A
consumer told `dom` would drop OCR of a chart drawn as a CSS background. The
field is v3's. `legacy/content.json` keeps v2.2's shape, so
`BundleVisualAssetRecord` extends `VisualAssetRecord` for `assets.json` only.
The MCP reader validates against it. Against the old model, `extra="forbid"`
would have sent every current record down the lenient path.

**The iframe warning from `visuals.py` is gone.** Every iframe asset got it,
saying content "may only be available through the screenshot". The harvest runs
before the snapshot and cannot know which frames were read. The snapshot's
warning already named each unread frame with its asset id, so the line was
removed rather than reworded. A frame that was read gets no warning: nothing of
it was lost. The unread-frame warning now says what the text surfaces hold,
which the extraction decides: its count check can skip attaching pictures, and
then the frame's OCR text is in `assets.json` only. So the warning is built
after the extraction, from the asset ids the bridge matched, and keeps its old
place in the manifest's order.

**A frame in a shadow root could name another asset.** The unread-frame record
took its asset id from the live element. The harvest reads the top document
only, so a frame in a shadow root is never an asset, and any id on it is the
page's. `shadow_frame_spoof_page.html` has a readable top-document frame, which
is asset-001, and an opaque frame in a shadow root carrying `asset-001`. At
fa21e54 the second frame's warning said "Its screenshot is asset-001", the first
frame's. The id is now taken from the top document's tree only, the rule the
copy's markers already followed, and the warning says the frame was not captured
as a visual asset.

**Goldens.** No golden has an unread frame. `lms_page.html`'s player is
`about:blank`, readable and empty, so no `content.*` file or chunk moved in any
case. Fourteen cases with visual assets moved, in three kinds of line:

- `"frame_content": null` on each non-frame asset, and `"dom"` on the lms
  player in `lms-module` and `lms-module-videos`. The walk read its empty
  document, so `dom` is right.
- `assets.json`'s bytes and digest in `manifest.json`. Each byte delta is 29 per
  `null` and 30 per `"dom"`: the field, and nothing else.
- The iframe line, gone from the manifest, report and stderr of both lms cases.

The re-record also moved `content.json` and `legacy/content.json`, and the same
two values in `assets.json`: raster digests and OCR confidence for a canvas and
a video. That is P7-14's font drift, which the portable profile masks. So the
goldens were rebuilt from the recorded files, plus the field, the recomputed
digest and the removed line. No value from this machine was pinned, every
recorded raster digest is unchanged, and all 26 cases match.

**Not done:**

- Visuals inside a shadow root or a read frame are not harvested.
  `VISUAL_SELECTOR` runs on the top document. In P16-6's fixture the shadow
  root's image reaches `content.html` with its alt text and no asset, OCR or
  warning.
- An asset's `nearby_heading` comes from the top document's headings. The
  opaque frame's figure chunk sits under "Shadow roots and frames", and the
  chunker's text chunk for the same picture under "Shadowed heading", from the
  open shadow root before it.

*Answered by P16-9*, both.

### P16-8 — a scrolling box the PDF cannot scroll, and a carousel P16-4 miscounted

P16-4 left one gap open: text that a container clips inside the page. The
common case is a box that scrolls. Measured at WebShot's print settings, a
30-column table of no-wrap cells in an `overflow-x: auto` wrapper printed 10
of its 30 columns. A log of 30 rows in a `max-height` box with
`overflow-y: auto` printed 3, and so did a `height` box with
`overflow: scroll`. Chromium keeps the box's clip on paper and prints whatever
was scrolled into view. The bundle has every cell and row. `release_clamped_text`
(P13-3) does not help here: it releases a capped box only when the overflow is
`hidden`.

**A box that scrolls on screen is now its own finding.** The clipping check
counts every element with a line of text past the visible area of an `auto` or
`scroll` box that overflows on that axis. It adds a separate manifest warning
naming the box, because the fix is the box and not the page. A line such a box
hides is not also counted at the page edge, since the box hid it first. A box
with `overflow: auto` and room to spare does not count, and neither does a
code block, which `BASE_PRINT_CSS` already wraps. The fixture is
`tests/fixtures/scroll_containers.html`. On the golden corpus, the only
scrolling box is `definition-structures`' `pre`, which wraps and hides
nothing, and no case moved.

**The rule that `overflow: hidden` is intent needed one correction.** P16-4
counted every line that crosses the page edge. Once the carousel in this
fixture sat inside a padded body, its second slide started inside the page,
so the slide's first line crossed the edge and was counted as cut. That slide
lies wholly outside the carousel's own box, which hides it on screen as well.
So a line wholly outside the box of an `overflow: hidden` or `clip` ancestor
now counts in neither tally. P16-4's own carousel test had slides starting
exactly at the edge, which is why it passed. The test here pads the body. The
rule is *wholly* outside: a line that only crosses such a box is the layout
running over it. P16-1's page-width `overflow: hidden` wrapper is that case,
and still counts. None of P16-4's other results changed, and neither did the
first affected page (24 elements).

**It still warns and does not correct**, but the two axes differ.
Horizontally, `overflow: visible` on the wrapper raised the table from 10 to
15 of its 30 columns. The rest ran past the page edge instead, where P16-1's
tally would count them. Vertically, releasing the log's `max-height` and
overflow printed all 30 rows, the way the clamp release does for a `hidden`
box. So a vertical release is a correction that would work. It is left for its
own change, because it moves rendered output wherever a page has such a box,
and that is a different decision from reporting the loss.

*Answered by P16-10* for the vertical case.

### P16-9 — visuals in shadow roots and read frames, harvested

P16-7 left the harvest looking in the top document only. Measured on
`shadow_frame_visuals_page.html` at c31fffa, the chart in an open shadow root,
the canvas in a nested one, and the chart and image in a `srcdoc` frame had no
asset, no OCR and no warning. Only the light chart and the frame element were
assets. The PDF printed all six. Of the four, the snapshot held only the frame's
image, as its alt text and its `data:` URL, and no text surface held what any
of them drew. The light chart's `nearby_heading` was the page's `<h1>`,
although the shadow root's "Shadow section" is the last heading a reader passes
before it.

**The harvest now walks what the snapshot walks.** `FLAT_TREE_JS`, shared by
both, lists the visuals in reading order, and the last heading passed is
recorded on the way. On a page with no shadow root or frame, that order and
that heading are what `document.querySelectorAll` gave, and no golden moved. A
frame's visuals are reached through a Playwright frame locator, keyed by a
`data-webshot-frame-index` the walk sets on each frame it enters. A CSS locator
already crosses open shadow roots. The fixture's six visuals are now six assets
in reading order, each standing in `content.html` as its bundle copy. With
Tesseract, each one's recognized phrase is in `content.md`, `content.txt` and a
figure chunk.

**Markers, in every tree.** P16-6 trusted `data-webshot-*` only in the top
document's tree, because that was the one tree the harvest cleaned. It now
cleans every tree it reaches, hidden frames included, before numbering, and
`HARVEST_MARKERS` is that list. The snapshot keeps those markers wherever it
walks and still drops any other `data-webshot-*` from outside the top
document. The fixture's frame chart arrives carrying `asset-001`. The harvest
removes that and numbers it in turn, and `asset-001` is the shadow chart, the
first visual in reading order.

**Two things the top document had for free:**

1. **The print stylesheet.** Recognized text rides in the PDF in a span that
   `BASE_PRINT_CSS` shrinks to 0.1 px and keeps off the page. That stylesheet
   is the top document's, and no stylesheet reaches into a shadow root or a
   frame. Without more, each shadow or frame visual's recognized text printed
   beside it at 16 px, `position: static`. The span outside the top document
   now carries the same declarations inline (`OCR_TEXT_DECLARATIONS`, from
   which the stylesheet's rule is also built, byte for byte).
2. **Printing whole.** The top document prints whole. A frame prints its box.
   Photographing the image below the fixture frame's fold scrolled the frame,
   and the PDF then printed "Bottom of the frame" and not "Top of the frame".
   Each frame the walk entered is scrolled back once its visuals are
   captured. A frame that cannot be is a warning.

**Not done:**

- `HEADING_JS`, which the walkthrough and media scans use, still asks the top
  document. Those scans look in the top document only, so nothing they find
  has a shadow or frame heading before it that the flat tree would change.
- A screenshot scrolls a scroll container inside the top document too, and
  that is not put back. It was not before P16-9 either.

*Answered by P16-12*, both.

### P16-10 — releasing a capped scrolling box, and where the page cannot make room

P16-8 measured that releasing a capped log's `max-height` printed all 30 of
its rows where 3 had printed. `release_clamped_text` now has a third arm for
that: a box with `overflow-y: auto` or `scroll` whose content overflows it is
marked `data-webshot-clamped="scroll"`. A print rule then sets `height: auto`,
`max-height: none` and `overflow-y: visible` on it. The log in
`tests/fixtures/scroll_containers.html` now prints whole. A `height: 120px`
box with `overflow: scroll` prints all 30 of its rows too.

**Every candidate is released on trial first.** P16-4 declined a generic
correction because text that a correction makes overlap stays inside the page,
where no check sees it. So each box is released for real, inline, and measured
before it is marked. It is kept only if all four hold:

- it is in the flow (not `absolute` or `fixed`);
- it then holds all of its content;
- its parent grew by as much as it did;
- every ancestor still contains it.

The measured cases it turns away, and the condition each one fails:

- **Out of the flow:** a panel positioned over the page, which would cover the
  content under it. It grew 1,120 px on trial and its parent grew by none of
  that. *Answered by P16-15*, which puts it into the flow first.
- **Still overflowing:** an app-shell pane whose height comes from `flex: 1`
  in a 400 px column, and a cell in a grid with fixed 120 px rows. Both
  overflowed just as much with `height: auto`, because the layout sizes them,
  not their own cap.
  *The flex pane is answered by P16-13*, which releases the column with it,
  and *the grid cell by P16-14*, which leaves the grid's rows to their content.
- **Parent did not grow, and no longer contains it:** a box that fit inside a
  capped outer box until it grew. It would have pushed the outer box into
  scrolling, so the rows would be lost there instead.

All four stay capped and are still reported by the clipping check's
scrolling-box warning. Where both boxes overflow, the walk reaches the outer
one first and keeps its trial applied, so the inner one is measured in the
page it will print in. Both are released and all 30 rows print. Every trial is
undone before the print CSS goes in. Kept or not, the page gets its `style`
attribute back byte for byte. That took two corrections. A property round trip
re-serialized the attribute, which the first version of the test caught.
Then, on a box styled only by a class, removing the attribute straight after
a trial left `style=""` behind, because Chromium writes a changed inline
style back to the attribute lazily. Another session found that one. Reading
the attribute first fixes it, and a test now trials a box with no attribute.

**Only the vertical axis.** A table wrapper released sideways runs off the
page instead (P16-8), so it keeps scrolling and stays a warning.

*Partly answered by P16-11:* a table whose cells can wrap to fit the page is
now released sideways too.

**What changed on the page.** The release is measured at screen media and the
capture's viewport, like the clamp release before it. So a page whose print
stylesheet already removes the cap is released twice, harmlessly. On the
golden corpus no box qualifies: the only scrolling box is
`definition-structures`' `pre`, which does not overflow vertically. No case
moved. The capture log's "Released N element(s)" count now includes these
boxes.

### P16-11 — letting a scrolling table wrap, at the width it prints at

P16-8 left the horizontal case as a warning because releasing a table wrapper
sideways only moved the loss to the page edge: 10 of 30 columns became 15. The
table in that case had 30 columns. Measured at the 779 px WebShot prints
Letter at, what decides it is whether the table fits once its cells may wrap:

| Table in an `overflow-x: auto` wrapper | Printed | Cells allowed to wrap | Content vs box |
|---|---|---|---|
| 6 columns of short-worded phrases | 3 of 6 | 6 of 6 | 731 px in 731 px |
| 10 columns of the same | 3 of 10 | 9 of 10 | 951 px in 731 px |
| 30 short columns | 11 of 30 | 18 of 30 | 1,600 px in 731 px |

Where it fits, wrapping is the whole fix. Where it does not, the table runs
past the page edge and Chromium shrinks the entire page to take it. That is a
change to every other line, made on WebShot's guess.

**`release_wide_tables` releases a table only where it then fits.** A box that
scrolls sideways around a table is tried with `overflow-x: visible` and its
cells allowed to wrap. It is kept only if its content then fits its own box
and the box sits inside the page. The try happens at the width the page prints
at, read back from Chromium with the same probe the clipping check uses. At
the capture's 1440 px viewport the wrapper is wider, and a table that fits
there may not fit on paper. The probe costs one extra one-page print, so it
runs only when the page has a table in a sideways scroller. That is before the
bundle is extracted, so the layout the bundle records is the one printed.
`tests/fixtures/wide_tables.html` holds both sides: its 6-column table is
released and prints every column, and its 10-column table keeps scrolling and
is the one box the clipping check reports. Without the release, the 6-column
table's last column is lost and it is the box reported. That is how the test
was shown to depend on the release.

**Tables only.** Nothing but a cell can be made narrower. A code block is
already wrapped by `BASE_PRINT_CSS`, and an image or a fixed-width block in a
scroller is as wide as it is. None of those is tried, and a page without a
table in a sideways scroller pays for no probe.

**An attribute of its own.** The mark is `data-webshot-wrapped`, not a
`data-webshot-clamped` value, because one wrapper can scroll both ways. A
table box capped vertically is released by P16-10 as well, and a test holds
that both marks survive.

On the golden corpus no case has a table in a sideways scroller, so none
runs the probe and none moved. P16-8's 12-column table does not fit even
wrapped, and it still scrolls and is still reported.

### P16-12 — one reading order for headings, and every scroll position put back

**`HEADING_JS` answered in document order, and that is wrong across a shadow
boundary, not only blind to it.** It took the last heading for which
`compareDocumentPosition` says the element follows. Two nodes in different
trees are *disconnected*, and the following bit is then an arbitrary but
consistent choice. Probed on `shadow_frame_visuals_page.html` at P16-9's head,
it put the chart inside the card's shadow root under "Frame section", the
heading after it, and the chart in the hidden frame under "Frame section" too.
It put the light chart after the card under the page's `<h1>`. The walkthrough
and media scans report headings with it, so an embed after a shadow-DOM section
would have been filed under the heading before that section.

It now walks `READING_ORDER_JS`, the flat-tree walk the harvest makes, from
the top document's body to the element. The heading is the last one passed,
and an element the walk never reaches (in `<head>`, an unslotted light child,
a hidden frame) sits under none. The harvest records its headings on the same
walk, and a test compares the two on every asset. On a page with no shadow
root or frame the walk visits elements in document order, and no golden moved.
The walk is per call, so it is linear in the page for each element asked
about. The scans ask about a handful. The harvest asks once, for all of them.

**Every scroll position a screenshot moved is put back.** P16-9 restored
frames only. Playwright scrolls whatever it photographs into view, including
each scrolling box around it, and Chromium prints a box as it stands.
`scrolled_visuals_page.html` has a scrolling box in the light DOM and one in a
shadow root, each with a chart below its fold. Without the restore, the PDF
printed neither box's top sentence nor its bottom one. Now the harvest saves,
before its first screenshot,
the scroll position of each document's window and of each element with more
content than box, in every tree. It keeps live references rather than markers,
and puts back whatever moved when it is done. Both boxes then print their top
sentence, and the window is back at 0. A position that will not go back is a
warning. The frame-by-frame restore P16-9 added is folded into this one.

### P16-13 — the app shell prints one page, and releasing it as a chain

The pane P16-10 turned away is the most common layout of a web app. The root
is fixed at the viewport and hides its own scrollbar, and inside it sits a
column holding a header and one pane with `flex: 1` that scrolls. With 200
rows in that pane, the capture printed 25 of them on one page. A row shell,
with a sidebar beside the pane, printed 26. Releasing the pane, the shell,
`body` and `html` printed all 200 over 8 pages, with or without the root's
`overflow: hidden`. A grid shell (a `1fr` row in a `100vh` grid) printed all
200 with no release at all, so it is not this case.

**The pane is released as a chain.** When a scrolling box fails P16-10's own
trial, it gets a second one. The pane is released, and then each box above it
that still stops it growing, up to the root. That is a box that ends above
the pane, or one whose content overflows it. Overflowing, not only clipping:
in a row the pane stretches to its shell's height, so the shell holds it
without clipping. The first version tested for clipping only, and the row
shell stayed a single page. A child of a flex column is also marked
`data-webshot-unflexed`, which gives it `flex: 0 0 auto`, because `flex: 1`
would otherwise go on sizing it to what is left of the shell. In a row,
`flex` sets widths, so nothing there is unflexed.

**The root is special.** Its `clientHeight` is the viewport, so by the
overflow test every page taller than the screen looked held, and `html`
joined every chain. For the root, only its own box ending above the pane
counts. That is the `height: 100%` case.

**Kept on P16-10's terms, plus one.** The pane then holds all its content,
every ancestor contains it, and no released box overlaps a neighbour in the
flow. The last guard is what turns away a footer pulled up over the pane with
a negative margin. Released, the pane would run under the footer. So
everything the chain changed is put back, byte for byte, and the pane stays
reported. The chain also answers P16-10's fourth case: a box that would
overflow a capped outer box is now released with it, and all 30 rows print.

On `tests/fixtures/app_shell.html`, 120 entries in a full-viewport shell, the
capture before this change was one page. It reported 97 elements hidden in
`div#pane.app-pane`, and the last entry was missing. After it the capture is
five pages, every entry prints, and nothing is reported. The grid-row cell
from P16-10 is still turned away: the grid's fixed track sizes it, so it
still overflows. No golden case has a shell, so none moved.

*The grid cell is answered by P16-14.*

### P16-14 — a grid's fixed row sets the pane's height, and the rows are let go

The last case P16-10 turned away is a scrolling pane in a grid with fixed rows.
P16-13's chain does not help it either: `height: auto` on the pane and on
every box above it leaves the pane exactly as tall as its row track. Measured
on three shapes, a fixed `grid-template-rows`, a dashboard with fixed
`grid-auto-rows`, and a cell in a named `grid-template-areas` row, the pane
printed 3 of its 30 rows each time. With the grid's rows set to
`grid-template-rows: none; grid-auto-rows: auto`, all 30 printed, the other
cells still printed, and nothing overlapped.

**One more step in the chain.** When the chain leaves the pane still
overflowing, and the pane sits in a grid, the nearest grid above it is marked
`data-webshot-ungridded`. Its rows are left to their content; its columns,
and where each item sits, stay the grid's own. Then the walk runs again. The
same terms decide whether any of it is kept: the pane holds all its content,
every ancestor contains it, and nothing released overlaps a neighbour. A cell
that shares its grid area with a caption laid over it fails the last test, is
put back byte for byte, and stays reported.

**The chain stopped marking boxes it did not change.** The grid case showed
that a box already as tall as its content, which clips nothing, can look
"overflowing" only because the pane's own visible overflow runs past it.
`body` and the grid joined the chain that way. Growing such a box changes
nothing, so the walk now undoes that grow and leaves no mark. P16-13's
shells are unaffected: every box in their chains changes height when released.

On `tests/fixtures/grid_panels.html`, two panels in a grid of 220 px rows, the
capture before this change reported 36 elements hidden in `section#tickets`,
and both panels lost their last item. After it, both last items print and
nothing is reported. The capture log says one element was released: once the
first panel's row grew, the second panel in the same row had room without a
release. No golden case has a grid, so none moved.

### P16-15 — a panel positioned over the page, put into the flow

P16-10 turned away a scrolling panel that is `absolute` or `fixed`: grown
where it floats, it covers what lies under it. Measured, leaving it alone is
worse than it looked:

| Panel | Printed | Put into the flow |
|---|---|---|
| `absolute`, over the page's text | 2 of 30 rows, over that text | 30 of 30 |
| `fixed`, a chat widget's shape | the same 2 rows on every page, over the text of each | 30 of 30, once |
| `fixed` and centred with `translate`, a dialog | the same 3 rows on every page | 30 of 30, once |

Chromium prints a fixed element on every page, so a fixed panel repeated its
first rows through the whole document and covered text on each page.

**Released by putting it where it sits in the document.** A visible
positioned panel that scrolls and overflows is marked
`data-webshot-unpositioned`: `position: static`, `inset: auto`,
`transform: none`, `float: none`. It is then tried like any other box, first
on its own and then as a chain, on the same terms. In the flow it pushes the
following content down instead of covering it. The `transform` goes too,
because a `translate` that centred a dialog would carry it off the page once
it is static. A trial that fails is undone, byte for byte, and the panel
stays reported.

**Moving it is declared.** Growing a box changes how much of it prints.
Moving one changes where it prints. So the capture log names it: "Moved N
positioned panel(s) into the page flow, so each prints whole where it sits in
the document."

**Only a panel the screen shows.** A panel parked off the page
(`left: -9999px`), or one that is invisible, is hidden from the screen too,
and surfacing it in print would be wrong, so it is never a candidate. The
clipping check had the same blind spot from the other side: it counted the
rows below the visible area of such a panel as hidden in a scrolling box. It
reported 28 elements for an off-page panel that no reader could see. A
scrolling box off the page is now treated like `overflow: hidden`, which hides
from the screen by design, and that panel reports nothing.

On `tests/fixtures/positioned_panels.html`, a floating contents list and a
fixed help widget, the capture before this change reported 32 elements hidden
in `nav#toc.toc`. Both panels' last entries were missing, and the widget's
first answer printed on both pages. After it, the log says 2 panels were
moved, every entry prints exactly once, and nothing is reported. The test runs
in `--mode faithful`, because clean mode removes a `nav` as page chrome. No
golden case has a positioned scrolling panel, so none moved.

### P16-16 — a frame prints its box: grown to its document, or named

The last item P16-6 left open. The snapshot reads a frame the page can script
whole, including what the frame's box does not show, and a frame prints only
its box. So the bundle held text the PDF did not, and nothing said so.
`scrolling_frames_page.html` has eight frames. Seven hold a first sentence the
box shows and a last one below it, and the eighth holds one line wider than
its box. Without this change, the PDF printed none of the seven last
sentences, and no warning was raised.

**Grown for print, on trial.** Before the bundle, `render/frames.py` narrows
the viewport to the width Chromium prints at, read back with the clipping
check's probe, and grows each frame to its document's height. The frame's text
wraps into more lines where the print is narrower, so a height taken at the
capture's width would fall short. A growth is kept only if the frame then holds
its document, its parent grew by as much, and no ancestor ends above it. A
`100vh` shell, which grows with its frame, fails the first check. A frame in a
fixed-height wrapper fails the second. Frames are grown innermost first, so an
outer frame is measured around the inner one's new height. The fixture's tall
frame, its nested pair and its shadow-root frame now print their last sentence.
The frame is grown before the harvest, so its screenshot is the frame the PDF
prints. A frame the reader cannot scroll (`scrolling="no"`, or a document with
hidden overflow) shows what its author chose, and is not grown: what it hides,
it hides on screen too. It is still named, as below.

**Named, when it cannot grow.** Then, at the same width, each frame the reader
could scroll is checked line by line for text outside its box. The fixture's
capped frame, below its box, and its wide frame, whose line does not wrap and
runs past the side, get a warning each, naming the frame. The bundle holds both
frames' whole text, as the warning says. A frame is never grown sideways, since
a wider frame runs off the page instead. A frame that cannot be scrolled gets a
warning of its own, with the reason (`scrolling="no"`, or its document hiding
its overflow). The text it will not scroll to is in the bundle, and neither
the page nor the PDF shows it. The first version of this change stayed silent
there, as an author's choice. That left the bundle holding text no reader of
the page or the PDF could see, with nothing saying so. The fixture's two
unscrollable frames are each named.

**The cost is a print.** Measuring at the printed width takes the clipping
check's one-page probe. It runs only when some frame the page can script shows
anything. `lms_page.html`'s `about:blank` player is the golden corpus's only
frame, and it is empty, so no case pays for the probe and none moved. A test
holds that a page like it never reaches the probe.

**A trial restores the attribute, not a copy of it.** In Chromium, removing
`style` straight after `style.setProperty` left `style=""` behind, because the
changed declaration is written back to the attribute lazily. Reading the
attribute first makes the removal stick. So a frame whose trial failed has no
`style` attribute afterwards, as before the trial, and a test holds that.

**`HEADING_JS` inside a frame.** A locator inside a frame evaluates there, and
the reading-order walk started from that frame's body. Evaluated on the framed
chart of `shadow_frame_visuals_page.html` at 0efa4e4, it returned no heading at
all, where the page puts the frame under "Frame section". The walk now starts
from the top document wherever it runs. That is reachable from any frame the
page can script, and those are the only frames the walk enters.

### P16-17 — a scrolling box inside a frame, released like the page's own

Every release so far walked the page's own document. A capped log inside a
same-origin frame therefore printed 2 of its 30 rows. That is P16-8's loss,
and none of the releases saw it. The clipping check did not report it either,
because it does not measure frames (P16-4). On
`tests/fixtures/framed_log.html` the capture printed 2 entries of 30, with no
warning at all.

**The same release runs in every frame the page can script.** A frame
qualifies when every frame between it and the top can be reached through
`window.frameElement`, which is null across an origin, and none of them sits
inside what the capture is removing. That is the same test the frame-fitting
work uses. In each such frame the release script runs unchanged, with all its
arms and all its trials. A frame that got marks is given the rules that act on
them, `RELEASE_PRINT_CSS`, and nothing else of WebShot's print CSS. The rules
go in as an adopted stylesheet, because a frame's `style-src` policy can
refuse a `<style>` element but not a constructed sheet. The page's own
`BASE_PRINT_CSS` is byte for byte what it was; the release rules were only
carved out of it so a frame can be given them alone. On the fixture every
entry now prints. A frame inside a frame is reached the same way. A frame the
capture hides, and one from another origin (two loopback ports are two
origins), are left untouched.

*Frames from another origin are decided frame by frame since P16-21.*

**What this does not do.** A released box can still be cut off by its
frame's own box. In a 1000 px frame the log printed 25 of 30, the rest past
the frame's bottom. Growing the frame to its document is the frame-fitting
work (P16-16). Its author describes it as running before the bundle, at the
printed width, which is after this release, so it would measure the released
height. And a box in a frame that the release declines is still not reported,
because the clipping check still does not measure frames.

*Answered by P16-18* for scrolling boxes: the check now measures every frame
that prints.

### P16-18 — the scrolling-box report reaches into frames

The clipping check (P16-4, P16-8) measured the page's own document, so a
scrolling box inside a frame lost its text with no warning. P16-17 releases
such a box when the page can script its frame. That left two losses silent:
a box that the release declines, and any box in a frame from another origin,
which the release may not touch. Measured with no release, a capped log in a
same-origin frame lost more than 20 of its 30 rows and the check said nothing.
The same held for a frame on a second loopback origin, where the release
changes nothing.

**Every frame that prints is measured.** Measuring is read-only, so frames
from another origin are included. Those are exactly the frames whose loss
nothing else can show. A frame is skipped when it, or any frame above it,
sits inside what the capture removes or has no box (`display: none`). In each
frame the same script runs against the frame's own width, and only its
scrolling-box tally is kept. A frame that cuts off its own document, like a
short frame over a long page, is the frame-fitting work's to report (P16-16),
not this check's, and a test holds that it stays out. The frame's rows join
the page's in the one scrolling-box warning. The sample names the box and
its frame: `div#log in iframe[title="Desk log"]`. The frame's title is used
when it has no id or class, because frames are rarely given either.

**What the tests show.** A frame's log is reported and named. Once P16-17 has
released it, nothing is left to report, so the release and the report agree
about the same box. A frame from another origin is reported even after the
release has run. The capture's manifest names the box and its frame end to
end. The same tests run against the previous check's code fail the three
report cases and pass the three that expect silence.

### P16-19 — the page edge cutting a frame, and the capture's own layer moving it

The clipping check measured the page's own text. A frame's text lives in the
frame's document, so the page edge could cut it with no report. The case is
P16-1's layout with an embedded frame in the pushed column, such as a booking
form or a calendar. Measured with 200 distinct words in the frame, 168
printed. The rest were cut at the page edge, and the check reported nothing.

**The page's edges, moved into each frame.** Every frame that prints is
measured, as in P16-18, now for the page-edge tally too. The printed page's
left and right edges are moved into the frame's own coordinates. The offset
comes from the frame element's box on the page plus its border and padding,
because the frame's document starts at its content box. An edge counts only
where it falls inside the frame's own box. Where the frame ends first, the
frame is what cuts, and that is the frame-fitting work's to report (P16-16).
A narrow frame clipping its own wide text therefore reports nothing here. A
frame parked off the page reports nothing either, because none of it is
visible on the page. The check's page bounds became parameters to do this:
the edges that cut, and the span that is visible. For the page itself they
are what they always were, [0, width], and the earlier tests all still pass.
The frame's count joins the page-edge warning, and its sample names the frame:
`p in iframe[title="Appointment form"]`. A frame pulled past the left edge is
reported the same way. With the frame tally switched off, exactly the three
page-edge tests fail.

**The capture's own OCR layer moved the layout.** Through the whole pipeline,
the same fixture reported nothing, and its printed width was 1168 px, not the
954 px measured on the bare page. The cause is WebShot's own layer. The
visual-asset step inserts an invisible OCR text span after each iframe, which
`BASE_PRINT_CSS` makes `position: absolute` and 600 px wide. Sitting after the
frame, it widened the document enough for Chromium's shrink-to-fit to lay the
page out at 1168 px. That carried the pushed column back onto the page. With
the span added to the bare page, the printed width went from 954 px to
1168 px. So an invisible layer WebShot adds can shrink the whole printed page
by about 18%. That is a defect of its own and is left for its own change. Here
it means the fixture's print stylesheet hides the root's sideways overflow,
P16-4's second clipping shape, so no shrink-to-fit can rescue the column, and
the check is measured on the layout that really prints. The end-to-end test
asserts the warning, not missing words, because the OCR layer carries the
frame's text, read from a screenshot at the screen's width, where nothing is
cut.

### P16-20 — the OCR text layer moved the page it describes, and lost its own text

Found while building P16-19's page-edge check for frames. The visual-asset
stage puts a span holding each asset's recognized text right after the asset,
so a search of the PDF finds text that exists only as pixels. `BASE_PRINT_CSS`
made it `position: absolute`, 600px wide, 0.1px text, with no offsets, so it
started at its static position: the asset's right edge. It is invisible, and it
was not inert. Measured at 0017b0f with `printed_width` (P16-4), the stage run
over each fixture with an engine that returns fixed text:

| Fixture | Printed width, bare | With the layer | Layer's text in the PDF |
|---|---|---|---|
| `ocr_layer_right_edge.html`, an image against the right edge | 779 px | 1168 px | no |
| `ocr_layer_framed_column.html`, P16-1's column holding a frame | 954 px | 1168 px | yes |

The layer ran past the page, so Chromium's shrink-to-fit laid the whole page out
at 1.5 times the paper's width and scaled it down to two-thirds. After the image
at the edge, the layer was still past the edge and cut, so the page was shrunk
for text that then did not print. After an inline asset, any asset whose right
edge is within 600px of the page's right edge did this.

**The goldens recorded it.** Eight cases were printed wider than the paper
and scaled down. For `svg-chart` that was measured: its caption text is 6.3pt
at 0017b0f and 9.45pt now, two-thirds. Each moved
`pdf-text.txt` is now exactly the same case run with `--no-ocr`, plus one line,
the layer's text. `svg-chart` is the one that gains a line, because its layer
had been cut off the page.

**Candidates, measured** on five layouts: the two fixtures, a floated image, an
image in a narrow positioned box at the right edge, and an image that ends the
document.

- Spanning the containing block (`left: 0; right: 0`) kept the width on all
  five, and the text printed on all five. The containing block is a box the page
  already lays out, so a layer inside it cannot widen anything.
- Anchoring the layer to the asset's box with CSS anchor positioning still
  printed the framed column at 1168 px, and extraction lost part of its text.
- Keeping the static position but matching the asset's width still widened
  four of the five.
- A zero-size wrapper with `overflow: hidden`, or `contain: paint` on a
  zero-width layer, kept the width to within 1px, but Chromium printed none
  of the text.

The layer now spans its containing block and keeps its static `top`, so it
stays in the asset's band of the page. The layers that the harvest adds inside
shadow roots and frames carry the same declarations inline (P16-9), so they
get the same geometry.

**A second loss, at the top of a page.** With the width fixed, `article-faithful`
printed its chart at the top of page 2, and the layer read "et ecog ed t sua
asset asset00: eeue 06 ($)". The glyphs are missing from the content stream, and
pdftotext misses them too. What survives is every glyph that dips below the
baseline: e, c, o, g, s, u, a, 0, the brackets. Swept 0.1px at a time across
three page breaks at the default scale, 0.1px text lost the other glyphs in a
band 1 CSS px deep that starts within 2px below each page's top edge. Why
Chromium drops them was not established.
The layer's static `top` is the top of the asset's line, so it lands in that
band whenever the asset was pushed to a new page, which `break-inside: avoid`
does to any image that does not fit. This predates the width change. Over 231
positions of a figure 13px apart, 0017b0f's layer geometry lost glyphs in 30 at
the default scale and in 62 at `--scale 2`. A 2px top margin moves the text
out of the band. Over 1,848 positions at scales 0.1, 0.25, 0.9 and 2.0, it
lost none. `ocr_layer_page_top.html` holds that case. Without the margin, the
test's sentinel is lost there and nowhere else.

**What the margin costs, stated, not measured.** After an inline asset, the
layer's line is the asset's, at least 24px tall, and the text stays inside it.
After a block-level asset, the layer starts 2px below the asset. So it can
reach 2px past the end of a document that the asset ends, and a block-level
asset that ends 1–2px above a page break would put the text back in the band.
Neither appeared in the sweep, which used inline images.

**Tests.** `test_ocr_layer.py` runs the real stage over the three fixtures,
and asserts the printed width with and without the layer, and that the text is
on the asset's own page. Two controls restyle the layer with its old geometry
and without its margin. Each fixture has to fail under the one it is about. At
0017b0f, 7 of the 15 tests fail.

**Goldens.** Eight cases moved, in three kinds of line, all from printing at
the paper's width instead of 1.5 times it:

- Rewrapped lines in the six `article-*` cases that OCR the chart. Apart from
  `article-faithful`'s new page, the words are the same once whitespace is
  removed. Where the old extraction joined two lines with no space
  ("abrowser", "Avoidreintroducing"), they are now two lines.
- A page more for `article-faithful` (2 to 3) and `lms-module` (1 to 2): in
  `manifest.json`, `pdf.json`, `report.json` and `stderr.txt`. The new page
  repeats what every page carries: the fixed banner faithful mode keeps, and
  the header and footer. In `lms-module` the checklist moved to page 2, so the
  video's recognized text now ends page 1 instead of following the checklist.
- `svg-chart`'s layer line, printed for the first time.

The re-record also moved `assets.json`, `content.json` and
`legacy/content.json`, and the `manifest.json` digests of those files. The
values were raster digests and OCR confidence, which is P7-14's font drift. So
those files are unchanged from HEAD, and the goldens hold only the lines above.
All 26 cases match.

### P16-21 — frames from another origin, decided, released and checked frame by frame

P16-17 stopped at the spec's line: a frame no page script can reach is
captured visually. So a scrolling box inside a frame from another origin was
reported (P16-18) and never recovered. A user's `--css` cannot reach such
a frame either, so the report was the end of it. WebShot now decides this
itself, frame by frame, and checks each decision afterwards. Extraction still
stops at the line (item 6 of the spec); only print preparation crosses it.

**The decision, for each printed frame from another origin.**

- *Never a sign-in or payment frame.* A frame holding a password,
  card-number (`autocomplete="cc-*"`) or one-time-code field is left as it
  was, whatever the gain. Those are the widgets least safe to rearrange. When
  such a frame hides text, the log says why it was left.
- *Only where there is something to release.* The release script runs in the
  frame with every trial it has on the page. Nothing marked means nothing
  changes.
- *Only if it fits the frame.* Nothing grows a frame from another origin
  afterwards, because the frame-fitting work (P16-16) stops at the same line.
  A released box that ran past the frame's bottom would move the loss from a
  scrolling box, which the clipping check reports, to the frame's own edge,
  which it does not. So the frame is given the release rules, and if any
  released box ends below the frame's box, everything is undone: the sheet
  and every mark. The log says why, and the box stays reported. A 200 px
  frame over a 30-row log is turned away this way. A 1600 px frame is
  released, and all 30 rows print.
- *Declared.* Each release and each refusal is logged with its reason and the
  frame named, e.g. "Released 1 scrolling box(es) inside iframe#frame, a
  frame from another origin: it holds no sign-in or payment field, and every
  released box still fits inside the frame."

**The check afterwards.** A kept release marks the frame's document. After
the render, the clipping check measures every printed frame on the layout
that printed. For a marked frame it runs the decision's test again: every
released box still inside the frame, and nothing still hidden in a scrolling
box there. If either fails, a manifest warning says the release did not
hold, names the frame, and says what went wrong. To prove the check can
fail, a test shrinks the frame after the decision, as a print stylesheet
might. The check catches it: "iframe#frame (1 released box(es) run past the
frame's own bottom edge)".

**What moved.** Three earlier tests assumed a frame from another origin is
never released. Two were tests of the report and now use a sign-in frame,
which the release still never touches, so the loss stays and must still be
reported. The third, P16-17's "left alone", now asserts the per-frame
decision's release. With the decision switched off, all five new tests fail.

## P18 — the #71 review's core and harness follow-ups (2026-09-27)

The pre-merge review of #71 (at `ad6109e`) found five defects in the core and
in the golden and parity harness. None was a blocker. Each one is a check that
did less than it said it did. They were fixed on one branch cut from `master`
after #71 merged. No golden and no parity baseline was re-recorded, and
`git status tests/golden tests/parity` stayed empty.

### P18-1 — a raster digest the portable profile masked and nothing checked

P7-14 added `bundle/legacy/content.json` to `_OCR_BEARING_ARTIFACTS`, the tuple
the portable profile masks. `asset_digest_failures` read its own list,
`("assets.json", "content.json")`, so the legacy file's raster `sha256` was
masked and never asserted. `article-legacy` could have published a legacy
digest that did not describe its raster and still passed both profiles. That is
the drift `_raster_records` was written to prevent, one level up: it shared the
record shapes between the masker and the assertion, but not the artifact list.

A record that carried a `sha256` and no `file` was also skipped. That is the
same absence read as success, and it hit the value the profile masks, so
nothing looked at it at all.

The assertion now iterates the masker's tuple. A published digest with no file
is a failure. Every `file` is resolved against the bundle root, the legacy one
included: `legacy/` holds only `content.json` and `chunks.jsonl`, and the
`article-legacy` manifest lists `assets/asset-001.png` at the root. A live
`article-legacy` run asserted one record in each of the three artifacts and
reported nothing. The same run with a wrong digest planted in
`legacy/content.json` failed and named it. Of the four new tests in
`tests/test_golden.py`, three fail against the old harness. The fourth guards
against a false positive: a truthful legacy record must resolve against the
bundle root.

### P18-2 — the font fingerprint was "none" on every Linux machine

`font_fingerprint` read each font directory with `iterdir()`, which does not
recurse. Measured in the private repository's self-hosted Linux runner image
(Ubuntu 24.04.4): `/usr/share/fonts` has **0** fonts at its top level and **43**
below it, in `truetype/dejavu` and `opentype/urw-base35`, and
`/usr/local/share/fonts` has none. On such a machine the fingerprint was
`"none"` whatever was installed. A recording and a run after a font update
both read `"none"`, so `profile_for` reported "environment matches the
recording", which is P7-14's failure on the platform the fix never covered.
CI's golden job then ran on the self-hosted Mac, so CI itself was not
affected.

It now uses `rglob`, and a font reached twice through nested roots or a symlink
is counted once. `Supplemental/` loses its separate entry because the recursion
covers it. On this Mac the value is unchanged: the recursive read finds the
same 369 fonts and gives the same digest, `369 fonts, 9bedbc3ec25e`, as the old
two-directory read, because `Supplemental/` is the only subdirectory. The
values the Mac records do not move. `rglob` does not descend into symlinked
directories on 3.11 or 3.12 (read in their `pathlib` source), and 3.13
documents the same default, so the value does not depend on the interpreter.

### P18-3 — the protected-viewer path never checked its text layer

Spec §5.11 says the text layer MUST be checked after printing. The check,
`ligature_forms`, runs in `capture_open_page` on the composed file. The
protected-viewer branch of `_capture` calls
`build_protected_viewer_artifacts` and returns before reaching it, so a
protected capture was never checked.

Whether it would tell the truth on that file was measured before wiring it
in. `ligature_forms` reads each font's `ToUnicode` map, and it counts a
`bfrange` that covers U+FB00–U+FB06 (a parametrized test holds that). A
text-layer font mapped by one identity range would therefore have named all
seven forms on every protected capture. The OCRmyPDF 17.10.0 layer is not
built that way. It is a Type0 Noto Sans subset in a Form XObject, and its
`ToUnicode` is a `bfchar` list of the glyphs it uses, 52 on the viewer fixture.
The list includes `<0019> <00660069>`, its own fi ligature glyph mapped to the
two letters. On the two-page viewer fixture the check found nothing.

The check now runs inside `build_protected_viewer_artifacts`, after `_verify`,
on the composed file (appendix and embedded files included). That is the file
that gets published, and it is the last point before the manifest is written.
Its warning goes into the manifest's `warnings`, and the pipeline takes its
own list from there, so the QA report and the log carry it as well. The
embedded README and JSON are built before composition on this path, and none
of them lists warnings, so the manifest is the only record, as it is for every
other protected-path warning.

Fixture: one viewer page and a document title containing U+FB01. The title is
printed in the appendix, and turning ligatures off cannot reach a presentation
form that the text itself contains. With a plain title there is no warning
(the control). With the U+FB01 title, the published manifest names ﬁ. Without
the change the test fails with `[] == [warning]`. The title is one source of
such a character on this path. OCR text that recognizes a ligature as its
presentation form would be another. That second source was not measured here.

### P18-4 — hidden MathML reached content.html inside a formula

P14-36's MathML pass reads a formula's linear text with `textContent` when the
page gives no TeX and no `alttext`. `textContent` reads every descendant,
drawn or not, and the pass ran before the clone's invisible elements were
removed. Measured on the fixture, `<math><mi>a</mi><mphantom><mi>phantomterm</mi></mphantom><mo>+</mo><mi style="display:none">undrawnterm</mi><mi>b</mi></math>`
reached `content.html` as `aphantomterm+undrawntermb`. It now reads `a+b`.

After reading the TeX annotation, the pass removes from inside the formula the
same set the clone filter removes from the page. One `excluded` selector
serves both removals, so what a formula may say and what the page may say
cannot drift apart. The TeX and `alttext` sources are unaffected, because
neither reads descendants. The first test in `tests/test_snapshot_math.py`
gains the new formula, and a second asserts both hidden words are absent. Both
fail against the old snapshot. No golden contains MathML.

### P18-5 — the parity `assets` metric is a loosening

P7-14 said that keying parity's `assets` on `(kind, width, height, alt)`
instead of the raster digest was "not a loosening". It is one, even though the
threshold stayed at 1.0. The identity never reads pixels. A raster whose
pixels are wrong but whose kind, size and alt text are right scores 100%: a
blank or stale capture, the wrong crop at the right size, or two same-sized
assets swapped. P7-14 also said the digest's property had "moved to"
`asset_digest_failures`. That function proves each digest describes its own
file. It does not prove the file is the right picture, and a wrong raster with
a correct digest of itself passes it.

What still reads pixels is the golden corpus. Its OCR sentinels (for example
"Revenue 2026", read out of the canvas in the article cases) run on every
profile. Its digest comparison runs only under the strict profile, and strict
runs nowhere until the goldens are re-recorded with a font fingerprint (P7-14,
"what this costs"). `parity.toml` says a loosening needs a docs/09 correction.
This entry is that correction, and the comment there now says what the metric
can no longer detect. No threshold and no metric code changed.

## P20: a new user's first captures (2026-10-03)

A test of the public copy as a brand-new user would meet it: install, read
the guide, capture mirrored Wikipedia and MDN pages, read the bundle. It found
defects in capture quality rather than in the rules: text the bundle presented
as the page's when WebShot or OCR had written it, a wait that always timed out,
an asset cap spent on visuals that were then skipped, a count of failed
requests that was a sample, and table padding. Each was reproduced on a local
fixture. The mirrored pages themselves are not in this repository, so their
figures are quoted as measured there and are not re-measured here. The goldens
were not re-recorded on this branch: they were recorded on macOS and this work
was done on Linux. Each entry says which cases its change moves. That was
measured by recording every case on Linux before these changes, twice with identical
results, and again with all of these changes, then giving each changed line
to the change that accounts for it; no line was left over.

### P20-1: WebShot's placeholders and OCR text stood as the page's text

Two paths put text into the bundle's reading surfaces as if the page had
written it.

**Placeholders.** The snapshot replaces each harvested `<svg>`, and each frame
it could not read, with an `<img>` of its screenshot, and gave that image an
alt the page never wrote when it had no label: "Vector graphic" and "Embedded
frame". A canvas is replaced in the live page with an image whose alt is
"Rendered canvas visual" when it has no description. docling reads an image's
alt as its caption when there is no `<figcaption>`, so each placeholder reached
`content.md`, `content.txt` and a chunk as a caption, while `assets.json` said
`alt: ""`. The `<svg>` fallback to its own `<title>` never worked: a `<title>`
is never rendered, so the clone filter had removed it before the rewrite read
it.

**Recognized text in a media title.** `embedded_media[].title` was read from
`aria-label` after the visuals pass had written its description there, which
the pass does wherever the page wrote none. For a video or frame with no title
or label, the title became "Text recognized within visual asset: …".

The harvest already reads each asset's own `aria-label` before it writes
anything, and records it in `assets.json`. The snapshot now takes those values
(`own_labels`) and uses them for the `<svg>` and frame alt and for the media
title, falling back to cutting the OCR segment off the attribute for an element
it has no record of. An `<svg>`'s `<title>` is read from the live element. With
no label, the alt is empty. The canvas image keeps its placeholder alt in the
live page, which is what the PDF's structure tree reads, and is marked
`data-webshot-rendered-canvas="placeholder"` so the snapshot empties it.

**Fixture and test.** `unlabeled_visuals.html` holds an unlabeled `<svg>`, an
`<svg>` named by its `<title>`, a video with no label and one the page
labelled, an unlabeled canvas, and two sandboxed frames. A stub engine reads
text in some and none in others. `test_placeholder_text.py` runs
`create_ai_bundle` over it. Before the fix, 6 of its 7 tests fail; the seventh
checks the fixture. No golden case has an unlabeled `<svg>`, canvas or unread
frame, or a media element without its own title or label, so no golden moves.

### P20-2: the wait for images waited for images that could never load

Before printing, the capture waits for web fonts and for every `<img>` that is
not `complete`, up to 30 seconds. A lazy image with no box (`display: none`, a
collapsed region) never starts loading, so it fires neither `load` nor
`error`, and the wait always ran out. The run then warned "Some fonts or
images were still loading when the PDF was created", which named nothing and
was not true of anything that printed. On a mirrored Wikipedia page the
new-user test measured a capture at 39.6 s. With the first two parts of the
change below (the box filter and the switch to eager loading) applied as a
patch, the same capture took 9.2 s and printed the same number of pages.

The wait now considers only images with a box (`getClientRects()` not empty):
an image without one is never printed. An image with a box that is still lazy
is switched to eager loading. The scroll pass moves the window only, so a lazy
image in a scrolling box or carousel, which the print may release (P16-10), or
any lazy image under `--no-scroll`, is never near the viewport. Measured on
`lazy_images.html` served over loopback: with `--no-scroll --no-ai-bundle` the
image below the first screen was never requested, and the PDF had no image in
it. The bundle hid this in the default run, because the visual harvest's
screenshot scrolls each visual into view and so loads it.

The deadline moved into the script, so when it passes the script can say what
is still loading. The warning counts those images and names up to five, each
without its query or fragment, because the manifest carries the warning and a
signed image URL keeps its token in the query. Fonts still loading get their
own warning. If the page never answers the script, the warning says the wait
could not be checked, rather than nothing.

Measured on this Linux machine, which was running other work at the time:

| `lazy_images.html` | before | after |
|---|---|---|
| default run | 39.9 s, false warning | 9.8 s, no warning |
| `--no-scroll --no-ai-bundle` | 33.9 s, false warning, 0 images in the PDF | 3.7 s, no warning, 1 image |

**Tests.** `test_image_wait.py` serves the fixture and calls the wait
directly. Before the fix its three browser tests fail: the wait for the unshown
image runs out, the image below the first screen is never fetched, and the
warning names no image. No golden fixture has a lazy `<img>`, so no golden
moves.

### P20-3: the asset cap was spent on visuals that were then skipped

The harvest lists every candidate visual in reading order: images, SVGs,
canvases, videos, frames and CSS backgrounds, hidden or not. `--max-assets`
was applied to that list, and only then was each candidate checked: one that
was not visible, or smaller than 32 by 24 pixels, was skipped. Hidden menus and
icons took places and gave them back to nothing, and every visual after the
fiftieth candidate was dropped. A mirrored Wikipedia page warned that it
"contains 65 visual assets; only the first 50 were captured", and the bundle
held 8. The warning counted candidates, not visual assets, and its number
read as a fact about the page. With a cap of 0 it also said "1 visual assets".

Each candidate is now checked before it takes a place, and the cap counts
visual assets. The candidates after the cap are checked too, so the warning
can count the visual assets it left out: "The page shows 15 more visual assets
than the limit of 50 (--max-assets); they were not captured." The OCR engine
check moved to the first asset to be captured, which is still before any
recognition.

Checking each candidate past the cap the way the harvest checks the ones it
captures, two round trips apiece, made a page of 1,000 qualifying images take
13.3 s to harvest at the default cap, against 4.7 s before the fix. The candidates
past the cap are now counted in one call per frame, by the same test written
in the page (what Playwright's `is_visible` checks, and the 32 by 24 floor):
4.6 s. On both fixtures below, at every cap tried, the batched count and the
one-at-a-time count agree. A frame whose candidates cannot be counted makes
the number "at least" and gets a warning of its own.

**Fixture and test.** `hidden_and_tiny_visuals.html` has 40 hidden pictures
and 25 icons before three charts: 68 candidates, three visual assets.
`test_asset_cap.py` harvests it with caps of 50, 2 and 0. Before the fix all three
tests fail: with the default cap no chart is captured and the warning counts
68. `visuals_past_cap_in_frames.html` puts two charts, a hidden chart and an
icon inside a frame the page can script, after two charts in the page; it is
harvested with caps of 0, 1 and 3, so the count past the cap crosses into the
frame.

**Goldens.** Only `article-capped-assets` (`--max-assets 0`) changes: its
warning, in `manifest.json`, `report.json` and `stderr.txt`, now reads "The
page shows 1 more visual asset than the limit of 0 (--max-assets); it was not
captured."

### P20-4: recognized text stood bare in the reading text

docs/11 principle 7 says machine-recognized text is labeled as OCR in the
artifacts, with its confidence. `content.json` did that: the recognized text
is a picture annotation with provenance `webshot-ocr`, beside a record that
holds the confidence. But `content.md`, `content.txt` and the chunks are
serialized from that document by docling, whose annotation serializer prints
a description's text and nothing else, and the figure chunk appended
`asset.ocr.text` as it was. So the recognized words stood between the page's
paragraphs with nothing to set them apart. On mirrored Wikipedia pages the
new-user test found text recognized at 20% to 39% confidence printed that way,
and one page's `content.md` opened with about 130 lines read out of a mockup
image.

Every surface serialized from the document now goes through one annotation
serializer that writes a `webshot-ocr` annotation between two lines, and the
figure chunk uses the same function:

```text
[OCR text from asset-001, machine-recognized by Tesseract, mean confidence 27%:]
lI1 ~~ Ww
settngs | | dashbrd
= 0 o
[End of OCR text from asset-001]
```

The opening line names the asset, so a reader can find the image, and the
engine and confidence the asset record holds. The closing line says where the
recognized text ends, which nothing else in `content.txt` or a chunk would.
`content.json` keeps the annotation unmarked, because its provenance already
says what it is. `content.md` and `content.txt` were `export_to_markdown()`
and `export_to_text()`; they are now the serializers those calls build, with
their default parameters and the marking annotation serializer, so a document
with no recognized text serializes byte for byte as before: the markdown, text
and chunks of all 36 HTML fixtures, extracted with no assets, were identical
before and after. The golden
harness masks the confidence figure in the opening line where it masks the
recognized words, since both move with the Tesseract build (P7-13).

**No confidence threshold.** The task allowed keeping low-confidence text out
of the reading text, provided it stayed in `assets.json` and the decision was
recorded. It is not done. Nothing here measures where a cut-off should sit:
Tesseract's figure is a mean over words, RapidOCR's a mean over lines, and
neither has been compared with how often the text is right. A cut-off would
also make the reading text depend on the machine for any asset near it,
because the confidence moves with the Tesseract build: the `lms-module`
golden's video frame reads at 45.63% on this machine. With the figure in the
opening line, a consumer that wants only confident text can apply its own
cut-off, and the text stays where the image is.

The v2.2 layout under `legacy/` keeps the form v2.2 wrote, where a visual
asset's chunk has type `visual` and opens "Visual asset asset-001".

**Fixture and test.** `recognized_text_snapshot.html` is a snapshot as the
capture writes it: one harvested picture between two paragraphs.
`test_ocr_marking.py` runs the bridge over it with a low-confidence reading
for that picture and checks `content.md`, `content.txt`, the text chunk and
the figure chunk each hold the reading only inside its marker, and that the
golden harness masks the figure. Before the fix the bridge printed the reading
bare in all four places.

**Goldens.** Every case whose recognized text reaches a reading surface gains
the two marker lines in `content.md`, `content.txt` and the chunks that hold
the text: `article-auto-selector`, `article-clean`, `article-faithful`,
`article-geometry`, `article-legacy`, `article-selector`, `article-untagged`,
`markdown-report` and `svg-chart`. With them move `manifest.json`'s digests of
those three files and `text_characters`, which is the length of
`content.txt`, in `manifest.json` and `report.json`. `lms-module` has
recognized text only for a video, which no reading surface carries, so it does
not move.

### P20-5: "(1 pages, …)"

The line a capture ends on, "Created … (N pages, … KB)", and its protected-path
twin in MB, counted one page in the plural, as did the protected path's
"Building … (N pages)…". Twelve golden cases print one page. All three now
take the count from one helper, `page_count`.

**Fixture and test.** `test_created_line.py` captures `sample_notes.txt`, the
`text-notes` case's input, without a bundle and reads the line from the log.
Before the fix it read "(1 pages, 29.8 KB)".

**Goldens.** The `stderr.txt` of the twelve one-page cases changes in that
line only: `captions-read`, `clamped-noise`, `clamped-text`, `csv-data`,
`embedded-media`, `form-fields`, `form-overlapping-passwords`, `json-data`,
`jsonl-records`, `markdown-report`, `svg-chart` and `text-notes`.

### P20-6: a count of failed requests that was a sample

The capture's `requestfailed` handler kept the first 20 distinct URLs and
dropped the rest, and the CLI printed the length of that list as the total:
"20 resource request(s) failed; see --report for details", on a page that lost
more. The count was capped at the size of the sample, and nothing outside
`--report` and the MCP result recorded any of it: a bundle read on its own
gave no sign that a resource was missing. The journey walker passed an empty
list to the capture, so no module's capture counted anything.

A `RequestFailures` tally now counts every distinct URL, by Playwright's
resource type, and keeps the first 20 as the list. Counting stops once the PDF
is printed, before the clipping check narrows the viewport, so the count and
the warning made from it cannot disagree. The warning goes into the manifest
with the count and the kinds and no URL, since a URL can carry a token:
"28 requests for the page's resources failed (image 25, stylesheet 2,
script 1), so the PDF and the bundle may lack what they would have loaded."
The QA report and the MCP result gain `counts.failed_requests`, from the one
`counts()` both use; `failed_requests` stays the list of the first 20. The new
count is optional in the schema, because a v2 report written before it
existed must still validate (P8-62). The CLI prints the warning and then says
where the URLs are: "The capture report (--report) lists the first 20 of the
28 failed request URLs." The journey walker counts each module from the click
that opens it.

Measured on `failed_resources.html`, served by a loopback server that closes
each resource's connection without answering: 25 distinct images (one asked
for twice), two stylesheets and a script.

| | before | after |
|---|---|---|
| CLI | "20 resource request(s) failed; see --report for details." | the warning above, then "lists the first 20 of the 28" |
| manifest `warnings` | nothing | the warning above |
| report `failed_requests` | 20 URLs | 20 URLs |
| report `counts.failed_requests` | absent | 28 |

**Test.** `test_failed_requests.py` captures the fixture over loopback and
checks the count, the list, the manifest warning, the report's count and the
CLI's line, and checks the tally on its own: a URL counted once, nothing
counted after `close()`, the singular. Before the fix the count was the list's
length, 20.

**Goldens.** Every case that writes a `report.json`, which is all but
`protected-viewer`, gains `counts.failed_requests`. It is 0 in every case but
`embedded-media`, where it is 1: that page's caption track, a `file:` URL,
fails to load. That case's `manifest.json` and `report.json` gain the warning
"1 request for the page's resources failed (texttrack 1), so the PDF and the
bundle may lack what it would have loaded.", and its `stderr.txt` prints that
warning and "The capture report (--report) lists the failed request URLs."
where it printed "1 resource request(s) failed; see --report for details."

### P20-7: table cells padded to the widest in their column

docling writes a Markdown table through `tabulate`, which pads every cell to
the widest in its column. One long cell widens every row: on a mirrored
Wikipedia page the new-user test found 71% of `content.md` was spaces, and an
agent reads those as tokens and pages through them over MCP. docling-core
offers `compact_tables`, which rewrites the padded table with one space either
side of each cell and keeps the delimiter row's alignment marks; `content.md`,
`content.txt` and the chunks now use it. Each serializer keeps its own
defaults and only that parameter changes.

Whether the result is still the same GitHub-flavored Markdown table was
checked, not assumed. Every HTML fixture was extracted before and after, and
both versions of each `content.md` that changed were rendered with
markdown-it-py's GFM table rule: the HTML was identical for all four
(`padded_table.html`, `professional_page.html`, `scroll_containers.html`,
`wide_tables.html`), and no line outside a table moved. `tabulate`'s GitHub
format writes no alignment colons, so the padding was the only place a right
alignment of numbers showed, and nothing in the parsed table changes.

| `padded_table.html` | before | after |
|---|---|---|
| `content.md` | 9,872 bytes, 84% spaces | 1,546 bytes, 24% spaces |
| `content.txt` | 9,870 bytes, 84% spaces | 1,544 bytes, 24% spaces |
| `chunks.jsonl` | 12,183 bytes, 70% spaces | 2,315 bytes, 18% spaces |

**Fixture and test.** `padded_table.html` has 24 short rows and one long note,
an empty cell and a cell with a pipe in it. `test_compact_tables.py` parses its
`content.md` table with markdown-it-py and compares every cell with the
fixture's, and checks that no table line in `content.md`, `content.txt` or the
table chunk has padding. Before the fix the cells matched and every padding check
failed.

**Goldens.** The table lines of `content.md`, `content.txt` and the table
chunks change in every case with a table: `article-capped-assets`,
`article-clean`, `article-faithful`, `article-geometry`, `article-legacy`,
`article-no-ocr`, `article-selector`, `article-untagged`, `csv-data` and
`markdown-report`. With them move `manifest.json`'s digests of those files and
`text_characters`, in `manifest.json` and `report.json`. This change moves no
line outside a table.

### P20-8: which readers find the OCR text layer (investigated, not changed)

The new-user test found that pypdf extracts the recognized text the capture
prints beside each visual asset, but PDFium (through pypdfium2) missed words on
one page and garbled another ("recogn zed w th n v sua"), and poppler's
pdftotext found none of it on a third.

**How the layer is written.** The span after each asset is styled by
`OCR_TEXT_DECLARATIONS` (P16-20): `font-size: 0.1px`, `line-height: 0.1px`,
`color: rgba(0, 0, 0, 0.01)`. Chromium prints it as ordinary filled text: text
render mode 0, the page's own font, `Tf` 0.1, and an `ExtGState` with fill
alpha `ca` 0.0118. CSS cannot ask Chromium for render mode 3 (invisible text),
and the visual-asset engines return text without word boxes, so a layer sized
to each word, as OCRmyPDF writes one, is not available on this path.

**Measured** with Chromium 1234 (Playwright 1.62), pypdf 6.19.0, pypdfium2
5.13.0 (PDFium 153.0.7999.0) and pdftotext 24.02.0. An engine stub returned
"Revenue illustrated filling quarterly within visual millimetre" for every
asset, and each reader was asked for ten words: the layer's prefix ("Text
recognized within visual asset") and the stub's words.

| Layer size | Fixtures | pypdf | pypdfium2 | pdftotext |
|---|---|---|---|---|
| 0.1px, as shipped | all four | 10 of 10 | 3 of 10 | 10 of 10 |
| 0.15px | two | 10 of 10 | 3 of 10 | 10 of 10 |
| 0.2px, 0.25px, 0.3px, 0.4px | two | 10 of 10 | 10 of 10 | 10 of 10 |
| 0.5px, 1px, 2px, 4px | all four | 10 of 10 | 10 of 10 | 10 of 10 |

The four fixtures are `professional_page.html` and the three P16-20 fixtures;
the two are `ocr_layer_right_edge.html` and `ocr_layer_page_top.html`. Each
row's counts were the same on every fixture it names. At 0.1px PDFium reads "Text recogn zed w th n v sua asset
asset-001 Revenue ustrated f ng quarter y w th n v sua m metre": it drops the
narrow glyphs (i, l) and the colon, the garbling the new-user test saw. Over
P16-20's sweep, a figure moved 13px at a time through 231 positions, at scales
0.1, 0.25, 0.9 and 2.0, with four words to find ("OCRLAYERSENTINEL
illustrated filling millimetre"), pypdf and pdftotext found every word at
every position and size. PDFium lost words at every position at 0.1px, except at
scale 2.0, where the text prints at 0.2px. At 0.25px, 0.5px and 1px no reader
lost a word anywhere. pdftotext finding nothing was not reproduced.

**Why it is not changed.** A larger size fixes PDFium, but the layer then
grows taller, and a layer after a block-level asset that ends the document
can push the document onto a new page. Measured with 8,336 characters of
recognized text after a block-level image that ends the document, moved 1px at
a time through 60 positions around the point where the document needs a second
page:

| Column | 0.1px | 0.25px | 0.5px |
|---|---|---|---|
| 700px wide | 2 positions add a page | 2 | 3 |
| 150px wide | 2 | 3 | 8 |

The two positions at 0.1px are the case P16-20 stated and did not measure: the
layer's 2px margin past the end of a document. A larger size widens that
window with the layer's height, which grows with the amount of text and the
narrowness of the column, and 0.25px, the smallest size that read cleanly,
sits close to the 0.15px that did not, with only the fonts tried here. Fixing
the window as well means moving where the layer starts after a block-level
asset, which reopens every geometry P16-20 measured. Neither change is clean,
so the layer stays at 0.1px. What can be said is what was measured: pypdf and
pdftotext read the layer, and PDFium, at the default scale, drops words that
hold an i or an l.

### P20-9: the loopback server's queue was smaller than a browser's burst (2026-10-04)

On the third CI run of P20-2's code, the macOS py3.13 job failed
`test_the_warning_names_the_images_still_loading`: it reported 6 images still
loading where the test stalls 7. The same job had passed on the two runs
before, and the other five test jobs passed on the same commit, which changed
only a document. No stalled image can finish within the test's 1-second wait,
so one had failed: an `<img>` whose request errors is `complete`.

The test's server is `tools/golden/loopback.py`, a `ThreadingHTTPServer`, which
listens with socketserver's queue of 5. Chromium opens six connections to one
host at once. A threading server accepts quickly, so the queue only fills
while its serve loop is waiting for CPU. The job that failed shared one Mac
with two other CI jobs, so that can happen there. What happens then depends
on the platform. Measured with seven clients connecting to a socket that
accepts nothing for 3 seconds:

| Platform | Queue 5 | Queue 64 |
|---|---|---|
| macOS (the maintainer's Mac, Python 3.12.9, `kern.ipc.somaxconn` 128) | 5 answered, 2 reset at once (`ECONNRESET`) | 7 answered |
| Linux (the development container) | 7 answered | 7 answered |

Linux holds a connection that does not fit and lets the client retry, so it
cannot show this; with `net.ipv4.tcp_abort_on_overflow` set to 1 it resets as
macOS does, which is how the test below was shown to fail without the fix.

The server now listens with a queue of 64. The golden harness captures from
the same server, so a fixture making several requests at once could have
failed one on a loaded Mac the same way, as a failed request in the report
rather than as a test failure.

**Tests.** `test_loopback.py` connects seven clients before the serve loop
starts and requires every one to be answered. With a queue of 5 and
`tcp_abort_on_overflow` set to 1 on Linux, one client was reset and the test
failed; with 64 it passes. Without that setting only a macOS run can fail it.
No golden moves.

### P20-10: simultaneous Tesseract runs stalled on Linux (2026-10-04)

When the public repository's six Dependabot pull requests ran CI, every Linux
test job failed, including the one that changes only a GitHub Action's
version. The Action update's job log shows the cause: Tesseract killed at
its 45-second limit on `tests/fixtures/viewer/page-001.png`, in
`test_tesseract_recognizes_the_fixture`. Master's run on the same runner image
(ubuntu-24.04 20260927.320.1, Tesseract 5.3.4 from the Ubuntu package) had
passed the same morning.

The Ubuntu package is built with OpenMP, which starts a thread per core in
every process. The suite runs four pytest workers, and WebShot itself
recognizes pages and assets in parallel, so several Tesseract processes can
run at once. Measured in the development container (Linux, 4 cores, Tesseract
5.3.4) on that page:

| Runs at once | `OMP_THREAD_LIMIT` unset | `OMP_THREAD_LIMIT=1` |
|---|---|---|
| 1 | 0.54 s | 0.54 s |
| 4 | 3 of 4 still running when killed at 200 s | all 4 done in 0.59 s |

Whether a job stalls depends on whether runs overlap, so a pass like master's
does not show the problem is absent. It is not only a test problem: in the
same container, `test_a_second_assembly_announces_what_it_replaces` hit the
90-second page limit in the product's own page recognition.

Every Tesseract run now gets `OMP_THREAD_LIMIT=1` unless the user set one,
which is what OCRmyPDF does for its own Tesseract runs.

**Tests.** `test_every_tesseract_run_is_limited_to_one_openmp_thread` checks
the environment `recognize_page` passes, and that a value the user set is
kept. `test_simultaneous_recognitions_of_one_page_all_finish` runs four
recognitions of the fixture at once with a 30-second limit. Without the change
both fail, the second by timing out; with it they pass in under a second. No
golden moves.

### P20-11: two version strings the portable profile did not mask (2026-10-04)

The OCRmyPDF 17.12.1 to 17.13.0 update failed the golden corpus on
`protected-viewer`, and `check.py` called the difference a real output change.
Only two lines differed: the manifest's `text_layer_engine`
(`OCRmyPDF 17.12.1`) and the PDF's `/Producer` (`pikepdf 10.12.0`, written by
pikepdf when OCRmyPDF saves; the update moved it to 10.16.0). docs/06 says
tool-version strings are normalized before comparison, and the portable
profile already masked the Chromium producer (`Skia/PDF m151`). It now masks
these two the same way and keeps the tool names, so a different engine still
fails. The strict profile still compares them exactly; it applies only when
the environment matches the recording, and an OCRmyPDF update there still
needs a re-record.

**Tests.** `test_the_portable_profile_masks_the_ocr_path_versions` compares two
snapshots that differ only in those versions, and one that names a different
engine. It fails without the mask. No golden moves.

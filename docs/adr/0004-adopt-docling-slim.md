# ADR-0004 — Adopt docling-slim as the document model, serializers, and chunker

**Status:** accepted · **Date:** 2026-08-19

## Context
ai_artifacts.py hand-rolls extraction, Markdown/JSON serialization, table CSVs,
and chunking. Docling (MIT, LF AI & Data) owns this problem, but
the full metapackage pulls torch (~1.3 GB venv). Its HTML backend is
BeautifulSoup-based — static, no JS — the exact complement to our browser.
docling-slim (v2.120.x, extras incl. format-html; Python 3.10–3.14) removes
the torch objection — real installed weight ≈ 197 MB venv (spike-measured;
the advertised ~50 MB refers to the base wheel set), still ~7× lighter than
the full metapackage.

## Decision
Sanitized captured HTML → docling-slim HTML backend → DoclingDocument.
content.json becomes lossless DoclingDocument JSON; md/txt/html/doctags via its
serializers; tables via TableItems; our OCR text attaches to PictureItems as
annotations; chunking via HierarchicalChunker (+ chonkie token-budget splitting; see
ADR-0008 — HybridChunker was refuted as default by spike S4). One bridge
module imports docling, and it MUST use `HTMLDocumentBackend` directly: the
top-level `DocumentConverter` crashes on slim installs (spike S3). Full `docling` metapackage is explicitly not a
dependency.

## Consequences
≈ −480 LOC; bundle becomes an ecosystem format (upstream LangChain/LlamaIndex/
MCP integrations); breaking content.json change managed by bundle_format: 3,
`--legacy-bundle`, and a migration guide. Churn risk R1 managed by pin + seam. The chunker gate is resolved — see
ADR-0008.

## Adoption result (Phase 2, 2026-08-19)

Executed as decided, with the seam earning its keep on day one: the pinned
versions (docling-slim 2.120.3 / docling-core 2.92.0) needed four corrections,
all absorbed inside `extract/docling_bridge.py` and pinned by bridge contract
tests — the `<title>`-tag title item, dropped `<table><caption>` text,
pre-heading content misfiled as skipped furniture, and a doctags crash on
docling's own JSON code label (docs/09 P2-3). Pictures carry no image URIs, so
asset matching is positional with a count check that degrades to a warned
skip on mismatch (P2-4/P2-9, risk R16). The
`html` serializer named above is not used: `content.html` stays the capture
stage's sanitized snapshot (single producer, docs/02). Parity across the full
corpus: headings/tables/links/asset hashes 100%, text coverage ≥ 99%, fidelity
records 100%, chunk provenance 100% — with the harness's mutation self-test
proving those numbers can fail.

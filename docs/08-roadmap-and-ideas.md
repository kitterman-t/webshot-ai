# 08 — Roadmap & ideas (post-v3)

Ranked by leverage; none are v3 commitments.

## Near (v3.x)

1. **OCRmyPDF plugin for shared OCR** — reuse its hOCR to emit our TSV word
   coordinates; removes the double-OCR on the protected path (R9).
2. **veraPDF gate goes blocking** + published per-release conformance report.
3. **Site-specific readiness recipes** — tiny data-driven YAML (selector +
   waits) for common hosts, community-contributable; explicitly *not* a crawler.
4. **Batch mode** — `webshot --from-list urls.txt` with a manifest-of-manifests;
   trivially parallel per-URL.
5. ~~**Office inputs polish**~~ **Done in v3.0.0:** DOCX/PPTX/XLSX via the
   `office` extra, and EPUB/ZIP in the base install, are documented features
   with fixtures and tests.
6. **Ship `py.typed`** — spec §7 declares a public Python API
   (`convert_url_to_pdf()`, `main()`), but the wheel carries no PEP 561 marker,
   so a consumer type-checking against it gets `Any`. One empty file plus a
   release note. Noticed during Phase 5's wheel-install gate and deliberately
   *not* done there: it is a compatibility-surface change, not a documented
   MUST, and the release phase is the wrong place to widen scope (docs/09 P5-6).

## Mid

7. **WACZ sidecar** (`--wacz`) — record the capture's network traffic with
   warcio (Apache-2.0) and package py-wacz style, giving replayable,
   archival-grade provenance next to the PDF. Positions WebShot alongside
   Webrecorder's ecosystem instead of competing with it.
8. **C2PA content credentials** (`--sign`) — sign the PDF and manifest with
   c2pa-python (Apache-2.0/MIT): cryptographic capture provenance (who/when/
   what tool), verifiable offline. Strong fit with the compliance persona.
9. **VLM picture descriptions** — docling-slim's VLM extras or a remote model
   to caption charts/figures beyond OCR ("what the chart shows"), stored as
   additional picture annotations; the audit found OCR-only is the current gap
   everywhere.
10. **Embedding-ready chunk export** — optional `--embed-model` emitting vectors
   alongside chunks.jsonl (offline sentence-transformers extra), or a
   documented LlamaIndex/LangChain loader recipe (DoclingDocument already has
   upstream loaders).
11. **REST facade** — thin FastAPI wrapper of the same options model, or
    documented deployment behind Gotenberg for orgs that want HTTP; decision
    deferred until MCP adoption data exists.

## Far / exploratory

12. **Image-PDF auto-tagging** via OpenDataLoader when its OSS tagging path
    matures — would make protected-viewer PDFs structurally tagged, closing the
    last accessibility gap.
13. **Diff/re-capture mode** — re-run a capture and produce a semantic diff
    (changed blocks, added/removed assets) using DocItem identity; useful for
    monitoring authorized document sources.
14. **Interop adapters out** — emit LlamaIndex `Document`s / LangChain loader
    natively; publish the MCP server to registries.
15. **Non-SharePoint protected viewers** — generalize the viewer registry
    (Google Drive preview, Box preview) under the same never-fetch-native-file
    invariant; each is its own legal/product review before code.
16. **Locale/RTL OCR quality pass** — per-script Tesseract configs, fixture
    corpus expansion.

## Explicit never-list (re-affirmed)

Access-control bypass of any kind; credential harvesting; CAPTCHA defeat;
site-wide spidering; hosted multi-tenant capture of third-party credentials.

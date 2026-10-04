# ADR-0003 — Adopt OCRmyPDF for the searchable-PDF sandwich (protected path)

**Status:** accepted; **implemented in Phase 1** (2026-08-19) · **Date:** 2026-08-19

## Context
protected_viewer.py hand-builds image-backed PDFs with an invisible positioned
text layer via reportlab — a re-implementation of OCRmyPDF's core (MPL-2.0,
long-established, `ocrmypdf.ocr()` API with sidecar text, PDF/A
output types, multi-core).

## Decision
Protected-path assembly becomes: img2pdf (lossless pack) → `ocrmypdf.ocr()`
(text layer, sidecar, optional PDF/A) → HTML-rendered transcript appendix →
pypdf merge/bookmarks/embeds. Page capture, enumeration, resumability, and the
count-verification invariant remain custom. WebShot's own Tesseract subprocess
pass is retained for word coordinates (OCRmyPDF emits no TSV); double-OCR
accepted for v3 (R9), plugin optimization on the roadmap.

## Consequences
reportlab removed; PDF/A-3 capability gained. Pin the 17.x major.

**Measured in Phase 1 (docs/09 P1-7):** the protected package went from 1,078
to 723 code lines (−355), not the ≈ −660 estimated here — the estimate counted
the drawing code that disappeared but not the composition contract that
remains (invariant checks, atomic publication, sequencing).

**Two consequences the decision did not anticipate.** OCRmyPDF 17.x requires
**Python ≥ 3.11**, which raised the project's floor (docs/09 P1-1); and because
the invisible text layer is not optional, OCRmyPDF and img2pdf are **core
dependencies**, not the `pdfa` extra Phase 0 filed them under (P1-2).
Ghostscript is optional for OCRmyPDF ≥ 17 (pypdfium2 rasterization) and used
for PDF/A when present — `webshot doctor` checks presence AND version
(≥ 10.7). Transitive deps arrive (pikepdf, fpdf2, pypdfium2) — tracked in the
lockfile. MPL-2.0 is file-level copyleft — using as a library is compliant;
we never vendor/modify its files.

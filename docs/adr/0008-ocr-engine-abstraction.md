# ADR-0008 — OCR engine protocol: Tesseract default, RapidOCR extra

**Status:** accepted (chunker gate resolved — see below) · **Date:** 2026-08-19

## Context
v2 hard-codes pytesseract. Tesseract is the right default (Apache-2.0, ubiquitous,
100+ languages, TSV word boxes) but requires a system binary — a real friction
on locked-down machines.

## Decision
Define an `OcrEngine` protocol (`recognize(image) -> words[text, conf, bbox,
lang]`). Engines: `tesseract` (default, pytesseract) and `rapid` (RapidOCR,
Apache-2.0, ONNX — pip-only, no system binary) behind `webshot[ocr-rapid]`.
EasyOCR/PaddleOCR/Surya rejected as defaults (torch/Paddle weight; Surya's
commercial terms) but remain addable behind the protocol. Engine choice,
version, and language are recorded per-asset in the manifest.

## Chunker gate — RESOLVED 2026-08-19 (spike S4, docs/09)
Empirical result: docling's default HybridChunker downloads a HuggingFace
tokenizer (fails offline) and its extra adds ≈140 MB. docling's
HierarchicalChunker needs no tokenizer, works offline, and yields chunks with
headings + doc_items provenance. Decision: **default = HierarchicalChunker,
with chonkie (RecursiveChunker, character tokenizer — offline by default) for
token-budget splitting of oversized chunks; chonkie becomes a core dependency;
HybridChunker demoted to an optional extra** for users who want
tokenizer-aware chunking and accept the download.

## Chunker adoption note (Phase 2, 2026-08-19)

The decision stands, with one correction (docs/09 P2-2): S4's "no extra, no
tokenizer" was true of HierarchicalChunker's *runtime* but not its *import* —
docling-core's chunker package eagerly imports `tree_sitter` and
`huggingface_hub`, which the spike venv happened to satisfy transitively.
Both are now small, permissive, imports-only dependencies; nothing downloads,
and `tests/test_footprint.py` holds the no-torch/no-transformers line. The
chunker is configured with docling's markdown table serializer because the
default triplet serialization drops row-header cells (the parity harness
caught it), and chonkie splits at a 1,800-character budget as v2 did.

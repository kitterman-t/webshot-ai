# ADR-0005 — Adopt MarkItDown for local-format adapters

**Status:** accepted · **Date:** 2026-08-19

## Context
source_adapters.py hand-parses MD/JSON/JSONL/CSV/TSV/XML/YAML/text/images.
MarkItDown (MIT, Microsoft) converts these plus DOCX/PPTX/XLSX/EPUB/ZIP/audio
to Markdown with a plugin architecture.

## Decision
Non-HTML local inputs route through MarkItDown → Markdown → the existing
MD→HTML renderer → the normal pipeline. WebShot keeps: MIME sniffing, the
100 MB guard, defusedxml hardening, image wrapper pages, and sanitization.
Office/EPUB/ZIP inputs ship behind a `[office]` extra.

## Consequences
≈ −210 LOC; new input formats nearly free; one more pinned dep. Pandoc (GPL,
heavyweight binary) rejected; bespoke parsers deleted.

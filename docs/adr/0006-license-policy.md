# ADR-0006 — License policy for dependencies and tools

**Status:** accepted · **Date:** 2026-08-19

## Policy
- **Linked/imported dependencies:** MIT, Apache-2.0, BSD, PSF, MPL-2.0, and
  LGPL (standard dynamic use) are acceptable. GPL, AGPL, SSPL, and
  research/commercial-restricted licenses are not.
- **External binaries invoked as subprocesses** (Chromium, Tesseract,
  Ghostscript-via-OCRmyPDF, veraPDF, optionally SingleFile CLI): any license,
  user-installed, never vendored, never linked.
- **Design references** (Browsertrix behaviors/profiles, Firecrawl API shapes):
  read and learn; never copy code.
- CI enforces the linked-dependency rule with pip-licenses (deny
  GPL/AGPL/SSPL); docs/03-components.md is the allowlist of record.

## Scope note (added during Phase 0, 2026-08-19)
This ADR governs what WebShot may **depend on**. WebShot's own license was
decided by the owner on 2026-08-19: **Apache-2.0** (canonical LICENSE file,
`license = "Apache-2.0"` + `license-files` in `pyproject.toml`). Chosen for
the explicit patent grant and alignment with the adopted stack's norm; it is
compatible with every dependency this policy admits.

## Rationale
Keeps the project adoptable by downstream commercial users and avoids
copyleft obligations while still using best-of-breed GPL/AGPL *tools* the way
their authors intend (as programs).

## Notable applications
img2pdf (LGPL-3.0) — acceptable as a normal import. OCRmyPDF (MPL-2.0) —
acceptable; never modify its files in-tree. veraPDF (GPL/MPL dual) and
SingleFile (AGPL) — external tools only. Marker/MinerU/PyMuPDF4LLM — excluded.

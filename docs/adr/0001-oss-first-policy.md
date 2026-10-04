# ADR-0001 — OSS-first policy: custom code requires a documented gap

**Status:** accepted · **Date:** 2026-08-19

## Context
WebShot v2.2 is 3,343 LOC maintained by one person. A two-pass landscape audit
(Aug 2026) found that ~2,200 of those lines re-implement functionality owned by
large, funded, actively maintained projects (OCRmyPDF, Docling, MarkItDown,
nh3), while ~1,100 lines implement capabilities with no open-source equivalent
(in-page visual OCR folded into the print PDF; protected-viewer capture with
count verification; the synchronized dual deliverable).

## Decision
Custom code is written only when a capability (a) does not exist in a
maintained, permissively licensed project, or (b) exists only in a form whose
adoption would surrender the project's differentiated core. Every adopted
component gets exactly one bridge module; every rejection is recorded in
docs/03-components.md. New dependencies require a components-doc entry and a
license-gate pass before merge.

## Consequences
Less code to maintain; upstream fixes inherited; the remaining custom code is
precisely the project's reason to exist. Cost: dependency-churn risk (R1, R8),
managed by pins, seams, and contract tests.

# ADR-0002 — Keep the Playwright capture core; do not rebase on Crawl4AI

**Status:** accepted · **Date:** 2026-08-19

## Context
Crawl4AI (Apache-2.0) covers scrolling, lazy-load, hooks,
storage_state, iframes — much of our capture layer. Rebasing on it was the
audit's "Option B."

## Decision
Keep direct Playwright control. The capture layer *is* the differentiated
product: deterministic DOM preparation, clean/faithful modes, selector
isolation preserving inheritance, canvas rasterization before pagination, and
the visual-asset/OCR harvest all require page-level control that would have to
be tunneled through another framework's abstractions and release cadence.
Browsertrix-behaviors (AGPL) is a design reference only — never bundled.

## Consequences
We own ~760 LOC of capture code permanently — accepted as the core asset. We
track Crawl4AI for ideas, not code. Chromium-only PDF (`page.pdf`) is locked in.

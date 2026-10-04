# Frozen v2 parity baselines

These are the v2.2-format golden bundles, copied verbatim from `tests/golden/`
at the last commit before Phase 2's extraction swap (the corpus audit had just
added the fidelity fixtures). They are the **baseline side** of the parity
harness in `tools/parity/`: after the swap there is no v2 extraction path left
to regenerate them from, so this directory is the durable record of what v2
extracted from every fixture.

Do not edit or re-record anything here. When `--legacy-bundle` is removed at
v3.1 and the parity harness retires with it, this directory goes too.

**And do not edit the fixtures these were recorded from.** The baseline side
can never be regenerated, so a change to a fixture under `tests/fixtures/`
that a case here was recorded from produces a divergence nothing can repair —
the metrics are right to report it and will report it forever. A case needing
different markup gets its own fixture file. See docs/09 P10-8, where a
one-word `src` change to `media_page.html` failed four parity self-tests that
had nothing to do with the change.

`protected-viewer` is deliberately absent: the protected path is untouched by
the extraction swap, and its invariance is enforced byte-for-byte by the golden
corpus job itself.

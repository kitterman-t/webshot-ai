# WebShot

WebShot captures a web page into a PDF plus an AI-readable bundle: the rendered
pages, the extracted text and structure, the assets and their recognized text,
and a manifest describing all of it. The PDF carries the bundle as embedded
associated files, so one file is the whole record.

Two properties are load-bearing, and most rules here protect one of them:
**output does not change by accident**, and **credentials never reach a
deliverable**.

The full rules are [CONTRIBUTING.md](CONTRIBUTING.md); the specification is
[docs/04-spec.md](docs/04-spec.md). Every measured finding is a numbered
P-entry: most live in [docs/09-spike-report.md](docs/09-spike-report.md), but a
series that amends one design document lives with that document and is indexed
from docs/09 — P9 is in [docs/13-lms-capture-proposal.md](docs/13-lms-capture-proposal.md),
next to the recommendations it reverses. Cite the number, not the file. This
file is the short version — what changes behaviour, not a summary of the rest.

`CLAUDE.md` and `AGENTS.md` are one document under two names, byte for byte,
so it does not matter which one your tool looks for. `CLAUDE.md` is the one to
edit; `uv run python tools/docs/agent_instructions.py --write` copies it over
the mirror, and `pytest` fails when the two have drifted.

## Before pushing

```bash
uv run python tools/verify.py          # the CI gauntlet, in CI's order
uv run python tools/verify.py --fast   # the same minus the browser-driven checks
```

`--fast` skips the checks that notice a change to captured output. The full run
is what "ready to push" means.

## Rules that are absolute

- **Upstream types stop at the bridge.** Each adopted library sits behind one
  module, and only that module imports it. What crosses the boundary is
  WebShot's own dataclasses. An upstream type in a stage module needs a bridge
  function, not an exception.
- **Never re-record a golden to make a check pass.** A failing golden means the
  output changed. That is sometimes correct, but it is always a decision with a
  written reason — never a cleanup step.
- **The security invariants in docs/04-spec.md §6 are MUSTs.** Auth state never
  reaches a bundle, password values are redacted, MCP stays inside its roots.
  Change the spec first if you think one is wrong; never weaken one in passing.
- **No silent degradation.** A stage that did less than the caller asked for
  says so in the manifest's `warnings` and, where it is fatal, through an exit
  code from the §3 taxonomy. Returning an empty list where something failed is
  a bug, not a fallback.
- **Every bug fix brings a fixture.** The smallest input that reproduces it,
  under `tests/fixtures/`, held by a golden or a unit test. No test touches the
  public internet.
- **No GPL/AGPL/SSPL in the dependency tree.** Such tools may be invoked as
  external binaries the user installs; they may never be linked.

## Traps this codebase has actually hit

Each of these cost a round-trip at least once. They recur because each one
looks correct while being wrong.

- **Green is not correct.** A golden records whatever it is handed. It proves
  output stopped changing, never that output is right. Read a re-recorded diff
  line by line; an unexplained line is a finding.
- **Never record a value derived from content the harness normalizes** (P7-13).
  A digest or a length taken over unmasked text is a fact about the recording
  machine. Assert the derivation instead — `chunk_digest_failures` is the
  pattern — so the property holds everywhere and the machine-specific number is
  pinned nowhere. A local golden run cannot see this class by construction.
- **A guard covers what it covers.** `context.route` filters page-initiated
  traffic only; an `APIRequestContext` request goes straight past it (P7-6).
  Before trusting a guard, check the path actually crosses it.
- **A limit applied after the read bounds nothing** (P7-9). A size cap checked
  once the body is buffered has already paid the cost it exists to refuse.
- **A count whose job is to be summed must be complete** (P7-8). A tally that
  silently omits a case is worse than no tally, because it reads as authority.
- **A fix has a boundary — ask what else sits on it** (P7-12). Five of nine
  defects in one review round were introduced by the fix pass before it: one
  half of a pair changed, the input guarded but not the accumulation, the writer
  updated but not the reader.
- **Absence must not read as success — ask what a check does when its input is
  missing.** Five times a gate in this repository has reported success for work
  it never did: `gh release create` without `contents: write`, a perf budget
  that could not fail, a reviewer with no tool to read code, the guard written
  to catch that reviewer, and that guard again when the reviewer ended its
  session to wait for subagents it had started (P10-18). Three of the first four
  were found by someone asking that one question. A check that cannot find its
  input has **failed**, not passed, and a default like `next((...), 0)` turns
  "no answer" into "a good answer".
- **A pipe discards the exit code you are testing.** `cmd | tail` returns
  `tail`'s status, so a failing command reads as green. This has bitten three
  times: the release perf budget, a crash triage where piping `verify.py`
  through `tail` made an intermittent failure look pipe-correlated, and a
  five-case verification of the guard above — which reported every case passing,
  inside the check written to prove a check works. Use `set -o pipefail`, or do
  not pipe the thing whose status you need.
- **Print what the check saw, not a sentence about what it wanted.** A guard
  that failed eight times over one bug cost seven of those rounds to
  diagnostics that described the failure. The two that ended a question in a
  single run both printed data: a line whose *absence* proved a step had not
  executed, and a dump of the JSON record's keys when an expected key was
  missing. Put the diagnostic where the failure is, not where the last one was —
  a message inside the failing component cannot report that component's absence.
- **Empty is not missing.** `if count is None and denied:` treated an empty
  denial list as "unknown" rather than "zero", so a clean result failed the
  check written to certify clean results. Test presence of the key, never
  truthiness of its contents; the same shape as the `next((...), 0)` default in
  the trap above, inverted.
- **Claim exactly what was measured.** "Complete narration" is not "full
  transcript"; a structured field saying `authored` must not contradict a
  sibling field saying `unverified`. Where a docstring, a manifest field, and
  the code disagree, that is a defect — not a wording preference.

## Reviewing

Correctness first: what breaks in production, what leaks, what silently records
something untrue. This tool's output is evidence, so a claim the code cannot
support ranks with a logic error, not with style. Formatting is already enforced
by `ruff`; type errors by `mypy`. Do not spend a finding on either.

## Conventions

- Conventional commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`,
  `build:`, `ci:`, `chore:`) with a body saying *why*.
- `docs/guide/cli.md`, `schemas/*.schema.json`, and `AGENTS.md` are
  **generated**. Regenerate them in the same commit as the change that moved
  them; do not hand-edit.
- Comments say why, not what. The repository's existing comments are the style
  reference: they explain the decision a reader would otherwise reverse.

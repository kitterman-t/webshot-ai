# Contributing to WebShot

WebShot is a capture tool: its output is a record of what a page looked like and
what it said. That makes two things unusually important here — output does not
change by accident, and credentials never end up in a deliverable. Most of the
rules below exist to protect one of those two properties. WebShot is maintained
by Tim Kitterman, and everyone taking part is expected to follow the
[code of conduct](CODE_OF_CONDUCT.md).

The design is written down in [docs/](docs/README.md), and
[docs/05-implementation-plan.md](docs/05-implementation-plan.md) records how the
v3 rebuild was phased. A reference such as "docs/09 P7-13" points at a numbered
entry in the findings log, [docs/09-spike-report.md](docs/09-spike-report.md):
each entry is one measured finding, and code comments cite the number instead
of repeating the story.

## Getting set up

```bash
uv sync --extra dev
uv run playwright install chromium
uv run webshot doctor          # tells you what is missing and how to fix it
```

Tesseract is optional but strongly recommended: without it, captures still work
and OCR degrades to a manifest warning, which is exactly what `doctor` reports.

## The checks

```bash
uv run python tools/verify.py          # everything CI runs, in CI's order
uv run python tools/verify.py --fast   # the same minus the browser-driven checks
```

`verify.py` installs each step's CI extras itself, so it does not depend on
what `uv sync` last left in the environment. Or the individual checks — these
do, and the office, RapidOCR and MCP tests skip without their extras:

```bash
uv sync --extra dev --extra docs --extra office --extra ocr-rapid --extra mcp
uv run pytest                          # unit + contract tests (no browser)
uv run pytest -m golden                # the golden corpus (browser + Tesseract)
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run python tools/golden/check.py    # same as -m golden, readable report
uv run python tools/golden/second_path.py  # the same check from another path
uv run python tools/licenses/check.py  # dependency-license gate
uv run python tools/audit/check.py     # every locked pin against OSV
uv run mkdocs build --strict           # the docs site; --strict fails on a bad link
```

Three files are **generated** and are checked by tests rather than reviewed by
eye. Regenerate them in the same commit as the change that moved them:

```bash
uv run python tools/docs/cli_reference.py --write       # docs/guide/cli.md
uv run webshot schema --write schemas                   # schemas/*.schema.json
uv run python tools/docs/agent_instructions.py --write  # AGENTS.md, from CLAUDE.md
```

`AGENTS.md` is `CLAUDE.md` under the name other tools look for, byte for byte.
Edit `CLAUDE.md` and regenerate; an edit made in the mirror is lost the next
time anyone runs that command.

CI runs all of these, on GitHub-hosted runners, and runs them on pull requests
from forks too — with a read-only token and no secrets, which no CI job needs.
The *goldens* are the recorded output of the fixture corpus under
`tests/golden/` (rule 2 below). The golden job runs on a hosted macOS runner
while the goldens were recorded on the author's Mac, so it compares on the
portable profile — see below.
The automated Claude review needs a secret, so it runs only for branches in
this repository and says in its run summary why it skipped any other.
[docs/06](docs/06-quality-and-testing.md#runners) says which runner each job
uses and what it installs there.

## Rules

### 1. Upstream types stop at the bridge

Every adopted open-source component sits behind exactly one module (a *bridge*),
and that module is the only file allowed to import it. What crosses the boundary
is WebShot's own dataclasses, never a `DoclingDocument`, an `ocrmypdf` argument
object, or a `MarkItDownResult`.

This is what makes a component replaceable: swapping docling for something else
should mean rewriting one file against an unchanged internal contract. A single
leaked import turns that into a refactor of the whole pipeline.

Bridges are marked in the module table in
[docs/02-architecture.md](docs/02-architecture.md). If you find yourself
importing an upstream type in a stage module, the fix is a bridge function, not
an exception.

### 2. Never re-record goldens to make a test pass

`tests/golden/` is the recorded output of the fixture corpus. A failing golden
check means WebShot's output changed. That is sometimes correct — but it is
always a decision, never a cleanup step.

- Read the diff `tools/golden/check.py` prints. Every changed line must have an
  explanation you can write down.
- If the change is intended, re-record with `tools/golden/record.py` and put the
  explanation in the pull request body.
- If the change comes from a **pinned browser, Tesseract, or dependency
  upgrade**, it goes in a *dedicated pull request containing the upgrade and the
  re-recording and nothing else*. Mixing a re-record into a feature PR hides the
  one diff a reviewer most needs to see.
- Never re-record a case you did not intend to change. If an unrelated case
  moved, stop and find out why.

**A local golden run cannot see a path-dependent recording.** It compares this
checkout against goldens recorded from this checkout, so anything derived from
the repository's own location agrees with itself and stays green until CI
checks out elsewhere. That has cost a round-trip twice (docs/09 P6-3, P7-13),
so `tools/verify.py` runs the corpus a second time from a throwaway worktree,
and `tools/golden/second_path.py` does it on its own. If you are re-recording,
run it before you push.

The rule it enforces is worth stating directly: **never record a value derived
from content this harness normalizes, or from the environment that produced
it.** A digest or a length taken over unmasked text is a fact about the
recording machine; so is a digest taken over pixels, which moves with the fonts
that drew them (docs/09 P7-14). Assert the derivation instead —
`chunk_digest_failures` and `asset_digest_failures` are the pattern — so the
property is checked everywhere and the machine-specific number is checked
nowhere.

**If a dimension can move the output, it belongs in the fingerprint.** A macOS
font update failed twelve cases under the heading "environment matches the
recording", which was true of everything `PROFILE_KEYS` knew about. A key the
recording never wrote counts as drift, not as a match.

Two comparison profiles exist, and `check.py` prints which one it used:

| Profile | When | What it compares |
|---|---|---|
| `strict` | the environment matches `tests/golden/environment.json` | everything, byte for byte, including recognized text |
| `portable` | Tesseract, Playwright, the platform, or the system fonts differ | everything except OCR text, OCR-derived counts, raster digests, and the browser build |

On both profiles, OCR is proven by per-case **sentinels** — strings that exist
only inside a canvas or a page image, so they can only reach the bundle through
recognition. If you add a fixture with text in a raster, add its sentinel too.

### 3. Security invariants (docs/04-spec.md §6) are not negotiable

Every one of these is a MUST. A change that touches capture, bundling, or the
protected-viewer path has to leave them true:

1. Storage-state files and auth profiles are never copied into a bundle or a PDF.
2. `type=password` input values are redacted from every semantic artifact.
3. Auth profiles are created `0700`; a group- or world-readable profile is
   refused.
4. The protected-viewer path captures only what the viewer already rendered for
   an authorized user. It never fetches the native file and never inspects
   credentials. (It does produce a copy of what the viewer shows, so the docs
   tell users to treat an owner's download or copy block as covering it.)
5. Sanitized HTML keeps no `<script>`, no event handlers, and no URL scheme
   outside http(s)/data/mailto.
6. All XML is parsed through `defusedxml`.
7. Local inputs above 100 MB are rejected.
8. MCP tools take no credential-bearing parameters, and filesystem access stays
   inside the configured roots. The full list — roots confinement, the
   server-chosen output root, default-deny private networks with a `final_url`
   re-check, profile *names* only, interactive-profile refusal, and one capture
   at a time — is docs/04-spec.md §6.8a–f, and each one has a denied-case test.
   A change to `src/webshot/mcp_server/` that does not add or keep such a test
   is not ready.
9. The installed dependency tree contains no GPL/AGPL/SSPL package.
10. A settings file is read only from `--config` or `WEBSHOT_CONFIG`. Adding
    working-directory discovery of `webshot.toml` would let an untrusted
    directory choose WebShot's settings, and is refused on that ground.

If you believe an invariant is wrong, say so in an issue and change the spec
first. Do not weaken one in passing.

The same goes for the [capture ethics policy](docs/11-capture-ethics.md), which
sets what WebShot will and will not be made to do: authorized views only, one
requested page at a time rather than crawling, and a permanent never-list
(bypassing access controls, DRM or CAPTCHAs, harvesting credentials). A pull
request that adds something on that list is declined on policy, however well
it is built.

### 4. Dependencies

- Permissive and weak-copyleft licenses only: MIT, BSD, Apache-2.0, MPL-2.0,
  PSF, ISC, LGPL (dynamic use). GPL/AGPL/SSPL tools may be *invoked* as external
  binaries the user installs; they may never be linked
  ([docs/adr/0006](docs/adr/0006-license-policy.md)).
- A new runtime dependency needs an entry in
  [docs/03-components.md](docs/03-components.md) saying what it replaces and
  what was considered instead — and it lands pinned in `uv.lock`.
- Prefer deleting custom code over adding it. That is the whole thesis
  ([docs/adr/0001](docs/adr/0001-oss-first-policy.md)).

### 5. Failures are specific

A new failure path needs an exit code from the taxonomy in
[docs/04-spec.md](docs/04-spec.md) §3 and an entry in the QA report. Silent
degradation is a bug: if OCR, an asset, or a stage did less than the user asked
for, the manifest says so in `warnings`.

### 6. Every bug fix brings a fixture

If it broke once it can break again. Add the smallest fixture that reproduces
it under `tests/fixtures/`, and let the golden corpus or a unit test hold the
line. No test may touch the public internet.

## Commits and pull requests

- Conventional commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`,
  `build:`, `ci:`, `chore:`), with a body that says *why*.
- One change per pull request into `master`; a release is a `v*` tag on it.
- Fill in the pull-request template. The checklist is short because every line
  on it has caused a real problem in this codebase or in the plan review.
- Keep the tree green: a pull request that needs "and then re-record the
  goldens" as an unexplained step is not ready.

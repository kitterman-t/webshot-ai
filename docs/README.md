# WebShot v3 design documents

Looking for how to use WebShot? Start with the [user guide](guide/index.md).
This directory is the design record behind it.

WebShot turns a web page or local document into a searchable PDF that carries
an AI-ready bundle inside it. These documents are the design package for
**WebShot v3**: a rebuild of WebShot as a well-tested composition of
open-source components, with custom code kept **only** where nothing in the
open-source ecosystem does the job. The documents were written as the plan and
then corrected as the build measured them; the corrections are part of the
record.

The plan was produced from a two-pass landscape audit (August 2026) of the
web-capture, document-AI, OCR, PDF, and archiving ecosystems. Facts about specific
tools (APIs, licenses, versions, install weight) were verified against primary
sources at the time of writing and are flagged for re-verification at
implementation time where they may drift.

## The one-sentence thesis

> Keep the browser capture engine, the in-page visual OCR pass, and the
> protected-viewer capture — the three things nothing else does — and delegate
> PDF assembly, semantic extraction, chunking, format adapters, sanitization,
> and validation to mature upstream projects.

## Reading order

| Doc | What it answers |
|---|---|
| [01-scope.md](01-scope.md) | What is this project, for whom, what's in and out |
| [02-architecture.md](02-architecture.md) | Target architecture, pipeline, module map, data contracts |
| [03-components.md](03-components.md) | Which open-source component fills each slot, alternatives considered, licenses |
| [04-spec.md](04-spec.md) | Functional spec sheet: CLI, config, outputs, schemas, exit codes, security |
| [05-implementation-plan.md](05-implementation-plan.md) | Phases, tasks, acceptance criteria, LOC budget, git strategy |
| [06-quality-and-testing.md](06-quality-and-testing.md) | Test strategy, golden corpus, validation gates, CI |
| [07-risk-register.md](07-risk-register.md) | Risks, likelihood/impact, mitigations |
| [08-roadmap-and-ideas.md](08-roadmap-and-ideas.md) | Post-v3 ideas: WACZ, C2PA, VLM captions, REST, crawl mode |
| [09-spike-report.md](09-spike-report.md) | The findings log: empirical validation of every load-bearing assumption, then every numbered finding (P-entry) from the build |
| [10-traceability-matrix.md](10-traceability-matrix.md) | Goals → spec → phases → tests; security requirements → proving tests |
| [11-capture-ethics.md](11-capture-ethics.md) | Capture ethics & compliance policy (authorized-view-only, robots.txt stance, privacy) |
| [13-lms-capture-proposal.md](13-lms-capture-proposal.md) | Embedded-video capture and the `webshot journey` walker for a learning-management system, with the P9 findings that amended it |
| [adr/](adr/index.md) | Architecture Decision Records — the "why" behind each choice, one decision per file |

There is no document 12. It covered the self-hosted CI runners of the private
repository this one was copied from, and is not part of this copy; CI here runs
on GitHub-hosted runners ([06 §Runners](06-quality-and-testing.md#runners)).

## Ground rules that shaped every decision

1. **OSS-first.** Custom code is a liability owned by one maintainer. Before any
   feature is written by hand, the ecosystem is searched; custom code needs a
   documented gap (see [adr/0001](adr/0001-oss-first-policy.md)).
2. **Permissive licenses only in the dependency tree.** MIT / Apache-2.0 /
   BSD / MPL-2.0 / LGPL (dynamic use) are acceptable. GPL / AGPL projects are
   used only as *external optional binaries* the user installs themselves, or as
   design references — never as linked dependencies
   (see [adr/0006](adr/0006-license-policy.md)).
3. **Every adopted component sits behind a seam** — a single bridge module —
   so it can be replaced without touching the pipeline.
4. **Contracts over code.** The bundle format, manifest, and chunk records get
   published JSON Schemas and versioned compatibility guarantees; consumers
   depend on the schema, not on implementation details.
5. **No regression in the security posture** established in v2.2: credentials
   never enter bundles, password values are redacted, and protected-viewer
   capture copies only what the viewer has already rendered for the signed-in
   user and never fetches the native file.

## Status

- **Stage:** v3.0.0 was released on 2026-08-21, from the private repository
  this one was copied from, and `master` has continued as 3.1 development (see
  the [changelog](https://github.com/kitterman-t/webshot-ai/blob/master/CHANGELOG.md)).
  [09-spike-report.md](09-spike-report.md) records what each phase corrected
  in this package, and those corrections are the reason to read the documents
  rather than assume them.
- **Baseline:** WebShot v2.2.0, an unpublished 3,343-line tool (four modules
  of 3,329 lines plus two 7-line entry-point shims) with 25 passing tests.
- **Target:** WebShot v3.0.0 per [05-implementation-plan.md](05-implementation-plan.md),
  reached. What the code measures now against the plan's size budget is in
  [05 §LOC budget](05-implementation-plan.md#loc-budget).

These are design documents. What WebShot does *today* is the user guide —
start at [Home](guide/index.md).

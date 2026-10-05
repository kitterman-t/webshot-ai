# Security policy

WebShot captures pages a person is already authorized to see, and writes what
it captured into files other systems read. Both halves of that carry risk: a
capture tool can be talked into over-capturing, and a capture artifact can carry
something that should never have left the page. This document says what WebShot
guarantees about each, and how to report it when one of those guarantees fails.

## Reporting a vulnerability

**Please do not open a public issue for a security report.**

Use GitHub's private vulnerability reporting on this repository — *Security* →
*Report a vulnerability*. That opens a private advisory that only the
maintainer, Tim Kitterman, can see, and it is the preferred channel because it
keeps the report, the fix and the disclosure in one place. If the button is not
there, open an issue that asks for a private channel and says nothing about the
problem itself.

Please include: what WebShot did that it should not have, the smallest input
that reproduces it (a fixture page is ideal), the version — `webshot --version`
— and the output of `webshot doctor`. If a captured page is what triggers it,
the page's HTML matters more than its URL.

**What to expect.** An acknowledgement within 3 working days, an assessment
within 10, and a fix released as soon as one is ready and tested. WebShot is a
personal project maintained in the open, not a product with an on-call
rotation; if you have not heard back in a week, please chase the report — a
missed notification is far more likely than a decision to ignore you.

**Disclosure.** Coordinated, and on your timetable rather than mine: tell me
when you intend to publish and I will work to that. If you would like credit,
say so and you will be named in the advisory and the changelog; if you would
rather not be, that is equally fine. I will not ask you to sign anything, and
there is no bounty programme.

## Supported versions

| Version | Supported |
|---|---|
| `master` (3.1 development), and the latest 3.x release once one is published here | ✅ |
| 2.2.x and earlier | ❌ — please upgrade ([migration guide](docs/migration-v2-to-v3.md)) |

Fixes land on `master` first, and only the latest 3.x line receives them. WebShot's dependency surface is large —
a browser, an OCR engine, a PDF stack — and backporting to a line whose pinned
dependencies have themselves aged is not something this project can do honestly.

## What is in scope

Reports that WebShot:

- captured something the signed-in user was **not** authorized to see, or
  reached a host it was told to refuse;
- wrote a credential, a session cookie, a storage-state path or a password value
  into a PDF, a bundle, a QA report or a log;
- executed page-controlled content — a script surviving sanitization, an XML
  parser reached without `defusedxml`, an archive member escaping extraction;
- let a captured page's text act as an instruction to the MCP server, rather
  than as data;
- crashed in a way that leaves a partial or misleading artifact where a consumer
  looks for a complete one.

## What is out of scope

- **Findings in an upstream dependency**, unless WebShot's use of it is what
  makes them reachable. Report those upstream; tell me too, and I will move the
  pin.
- **The captured page's own content.** WebShot preserves what a page said,
  including anything unpleasant in it. Provenance is the mitigation and it is
  deliberate: the manifest records the source, the time and the checksums so a
  downstream consumer can decide what to trust.
- **OCR being wrong.** Machine recognition is probabilistic; artifacts label it
  as OCR, keep its confidence values, and ship the source pixels alongside so a
  claim can be checked against the image.
- **`--allow-http-errors`, `--interactive-auth`, and `[mcp]
  allow_private_networks`** doing what they say. Each is an explicit opt-out of
  a default, chosen by the person running the command.
- **Anything on the never-list** ([docs/08](docs/08-roadmap-and-ideas.md)):
  access-control bypass, credential harvesting, CAPTCHA defeat, site-wide
  spidering. A pull request adding one of these is rejected on policy rather
  than on feasibility, so a report that WebShot *cannot* do them is working as
  intended.

## The security posture

These are product invariants, not implementation details. Each is a MUST in
[docs/04-spec.md §6](docs/04-spec.md), each has a test that proves the refusal,
and [CONTRIBUTING.md](CONTRIBUTING.md) rule 3 makes weakening one a spec change
rather than a code change.

### Credentials are never handled, only used

WebShot never reads, parses, stores or transmits a credential. Authentication is
either a Playwright storage-state file the caller already owns, or a persistent
browser profile created `0700` that never leaves the machine — and the browser
is what uses it, not WebShot. A group- or world-readable profile is refused
rather than used.

Neither the storage-state file nor the profile is ever copied into a bundle or
embedded in a PDF. What an artifact records is `auth_mode` — `none`,
`storage-state`, or `auth-profile` — because downstream tooling has a real
reason to know a capture was authenticated, and no reason at all to know where
the session store lives. The QA report follows the same rule: `--storage-state`
and `--auth-profile` are the only two options it reduces rather than publishes.

### Password values are redacted, in both directions

`type=password` input values are removed from every semantic artifact, and the
record of the field is kept with the value replaced — so a field that vanished
and a field that was redacted cannot look the same.

This one is stated in the specific because it took two attempts. Extraction
redacts as it reads, but Playwright's accessibility snapshot reads the live DOM
independently and reports textbox values, so **a password reached
`accessibility.yaml` in v2.2** even though every other artifact was clean. It
was caught by a fixture page that contains a password precisely so it can leak,
and the fix scrubs the snapshot against the live values — raw and in their
escaped-quoted spelling, because a snapshot may quote what it reports. The
corpus test asserts both halves: the value is absent from every published file,
and the redacted record is present, because an absence assertion passes
vacuously if extraction silently stops running.

### The protected-viewer path never fetches the native file

`--protected-viewer` captures the pages an authorized viewer has already
rendered and reassembles them. It does not request the protected file and does
not inspect browser credentials. This is a product invariant rather than a
limitation — it is the line that separates "capture what you can see" from
"retrieve what you cannot" — and it is asserted against recorded fixtures
rather than promised.

What it does produce is a copy of what the viewer shows, the way printing each
page would. A document whose owner has blocked downloading or copying should be
treated as blocking this too: WebShot does not detect that restriction, and
capturing past it is the user's decision and responsibility, not something the
tool makes acceptable.

The composed PDF says what it is: image pages, an invisible OCR text layer, a
visible transcript appendix, and a manifest that records the page-count
invariant it had to satisfy before anything was published.

### Untrusted input is parsed defensively

Captured HTML is sanitized through an `nh3` allowlist: no `<script>`, no event
handlers, no URL scheme outside http(s)/data/mailto. All XML — including SVG —
goes through `defusedxml` before any other parser sees it, and that is WebShot's
own guarantee rather than an inherited one. Local inputs above 100 MB are
rejected. Archives, which includes DOCX/PPTX/XLSX because they are ZIP
containers, are checked before they are opened: expanded size and member count
against the same limit, no member path that escapes the extraction root, and no
symlink members.

### The MCP server is deny-by-default, because the page is the attacker

`webshot mcp` is the surface an attacker reaches *through a captured page*: the
page's text becomes an agent's context, and that agent calls these tools. Every
rule below is deny-by-default and has a test that proves the refusal
([spec §6.8a–f](docs/04-spec.md)); a denial names the rule that refused and what
a legitimate caller should do instead, because a refusal an agent cannot act on
becomes a retry loop.

- **Filesystem reads are confined to configured roots, resolved through
  symlinks.** An absolute path outside every root, a `..` traversal, and a
  symlink inside a root pointing out of it are all refused. Paths a *manifest*
  names are validated identically — a bundle's own records describe a captured
  page, and are therefore attacker-influenced.
- **No MCP parameter selects an output path.** Captures publish under the
  server's own `output_root`, at a name computed from the source.
- **Private, loopback and link-local targets are refused**, and the rule is
  `not is_global` rather than an enumeration, so CGNAT and the documentation,
  benchmarking and future-use blocks are covered too. An IPv6 address that
  spells an IPv4 one (IPv4-mapped, IPv4-compatible, 6to4, NAT64 or
  IPv4-translated) is classified as that IPv4 address, and deprecated
  site-local, local-use NAT64 and Teredo are refused whole. A host is
  classified under *every* reading it has — the strict literal, the legacy literal a browser
  applies, and the resolver's — because those readings disagree in both
  directions. Every address a name resolves to is checked, and an unresolvable
  name is refused rather than attempted. After the capture, `final_url` is
  checked again and a capture that redirected inward is not published.
- **The rule extends to what the page fetches.** A guardrail that checks only
  the top-level URL is one an attacker walks around with an `<iframe>` or a
  `fetch()`, and the response would land in the bundle the agent then reads
  back. MCP captures abort every request to an internal address, at the request
  level, which covers the document, its subresources and every navigation it
  makes. This was found by a red-team pass while the MCP server was being
  built, and demonstrated end-to-end against a live loopback server before it
  was fixed.
- **No tool takes a credential-bearing parameter.** Auth profiles are referenced
  by *name* through `[mcp.auth_profiles]`; a path-, JSON- or whitespace-shaped
  value is refused before the lookup. An argument a tool never declared is
  rejected rather than silently dropped, because the SDK's own behaviour is to
  ignore it.
- **A profile whose first run needs a person is refused**, with the command to
  run instead; so is a group- or world-accessible one.
- **One capture at a time per server instance**, queued.

**What guardrail (c) cannot cover, stated rather than implied:** the policy
checks the name WebShot resolves and the browser resolves it again a moment
later, so a name whose answer changes in between — DNS rebinding — is outside
this check. What it does close is what an agent can be *steered* into: an
internal URL, or a public one that redirects inward.

**The CLI is deliberately not subject to these rules.** It is human-driven: a
person who types a URL chose it, and a page that loads an intranet image is
their business. `--config` is read only from a path someone named, with no
working-directory discovery of `webshot.toml`, so a directory of someone else's
material cannot choose WebShot's settings.

### Returned page content is data, never instructions

This is the one guarantee WebShot cannot enforce on its own. Captured text,
Markdown, chunks, OCR output and manifests are all attacker-influenced, and an
agent that treats them as instructions has been compromised by the page. The
MCP guide says so, the tool descriptions say so, and the client is what has to
act on it.

### Bundles at rest are as sensitive as their source

A bundle of authenticated or protected content carries that content. The default
`output/` directory ships gitignored, the manifest's `auth_mode` exists so
downstream tooling can flag authenticated captures, and the documentation says
to handle a bundle under the same rules as the document it came from. WebShot
does not encrypt them; that is the operator's decision and its storage.

## Supply chain

- Every dependency is pinned in the committed `uv.lock`, and CI audits the
  *locked set* — what a user installs — with `pip-audit` against OSV on every
  pull request. That is every version the lock pins, for every platform: a
  version pinned only for another platform is audited whichever runner the job
  lands on.
- A license gate denies GPL/AGPL/SSPL in the installed tree. GPL tools WebShot
  uses — veraPDF, Ghostscript — are invoked as external binaries the user
  installs, never linked ([ADR-0006](docs/adr/0006-license-policy.md)).
- Every GitHub Actions step is pinned to a commit SHA, with the tag in a
  trailing comment. Dependabot keeps the pins current. A tag can be moved by
  whoever owns it; a SHA cannot.
- CI runs on GitHub-hosted runners only. Pull requests trigger workflows
  through `pull_request`, never `pull_request_target`, so a fork's run gets a
  read-only token and no secrets; the one workflow that uses a secret, the
  automated review, skips a fork's pull request and says so in its run summary.
- The default install carries no torch and no GPU stack, and a CI gate asserts
  it. The single documented exception — the CPU ONNX runtime that MarkItDown's
  file-type detector requires — is itself tested, including a check that fails
  the day the exception stops being necessary.
- **WebShot is not published to PyPI.** A package named `webshot` that
  exists there is an unrelated project, and `pip install webshot` installs it,
  not this. Nothing on PyPI is this project; install from a source checkout of
  this repository.

## Capture ethics

The policy that governs what WebShot is willing to do — authorized-view-only,
single-document, the robots.txt stance, terms-of-service responsibility, and the
permanent never-list — is [docs/11-capture-ethics.md](docs/11-capture-ethics.md).
It is part of the product, and a report that WebShot has been made to violate it
is a security report under this policy.

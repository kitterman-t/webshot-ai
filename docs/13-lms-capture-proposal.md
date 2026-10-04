# 13 — Capturing an LMS for an AI agent (Continu + Guidde)

**Safety rules first.** `webshot journey` is for capturing a training journey
that the signed-in user has already completed, as their own reference copy. It
reaches modules only through the app's own interface, by clicking the rows the
app renders, so it cannot open anything the app has not unlocked for that user.
It opens only modules typed `article` unless a person widens the list with
`--allow-module-type`, and it never opens an `assessment`, which no flag can
permit, so it creates no quiz attempt. It never presses "Next in Journey" or
"Next in Track". It reads the journey's progress before and after the walk and
stops with an error if the figure moved, because progress is a training record
other people can see. Completion is the operator's precondition rather than
something the walker checks: on a completed journey nothing is left locked. The
binding rules and the reasons for them are in
[The walker's safety rules](#the-walkers-safety-rules-binding-before-any-of-it-is-written)
below.

**Status:** both halves are **implemented**. The embedded-video half shipped
first (2026-08-22). The journey walker is
`webshot journey URL` (`src/webshot/journey/`): it enumerates the journey twice
and diffs the two walks, and — unless `--dry-run` stops it there — captures
exactly the module set the enumeration listed, opening only module types on an
allowlist (`article` by default, `assessment` never), resuming a previous run
of the same outline, and writing the corpus index and the deduplicated
`media/` store beside the module bundles. Its binding contract is *The journey
walker — the specification* at the foot of this document. Speech-to-text for
video that ships no authored transcript (L3) is not built and is out of scope
for this repository. What was measured while building is in docs/09
P7-1..P7-5, and three of the claims this document originally made were wrong —
they are corrected in place. A second measurement pass, against a **completed**
journey rather than the 13% one the body was written from, is **P9-1..P9-9**
below; where the body and P9 disagree, P9 is the record and the specification
follows it, and P11–P13 correct P9 in the same way.

## The goal

Turn a training journey on Continu — pages, and the videos embedded in them —
into a corpus an AI agent can learn the trained product from. The source
measured throughout this document is a software vendor's customer-training
journey hosted on Continu. The capture has to include what the *videos* say,
because on this LMS the videos carry most of the procedural knowledge and the
surrounding page text is often one sentence.

## Before anything else: this capture can alter the user's training record

Continu tracks completion, and that progress is a real record the organization
that assigned the training can see. A walker that clicks through a journey could therefore certify someone as
having completed training they never watched — an outward-facing, hard-to-
reverse side effect, and the one risk in this feature that is not about data
quality.

**Measured:** opening tracks and one article left `Your Progress` unchanged at
13%. So *viewing* appears safe. **"Next in Journey" is untested** and is the
likely mutator, since it is the control the product uses to advance.

Design rules that follow:

- **Prefer opening a module by clicking its row** over pressing "Next in
  Journey". Viewing is the verified-safe motion.
- **Read the journey's progress percentage before and after every walk, and
  record both in the manifest.** If it moved, that is a defect to surface, not
  a footnote.
- **Never open, start, or submit an Assessment.** Quiz modules may create an
  attempt on entry, which is worse than marking a page viewed. Record that the
  assessment exists — title, type, duration — and skip it. It is the one module
  type the walker must refuse.
- **Ship `--dry-run`**: enumerate what *would* be captured (modules, types,
  locked state) and touch nothing. That is the first thing anyone should run
  against a real journey.
- If the journey cannot be walked without changing progress, **stop and say
  so.** Whether to accept that is the operator's decision, not the tool's.

## What the research found

Two discoveries decide the whole design. Both were verified against the live
services, not inferred.

### 1. Guidde exposes the entire walkthrough as JSON

The videos are Guidde playbooks. The share page is a client-rendered SPA — a
static fetch of `app.guidde.com/share/playbooks/<id>` returns essentially
nothing, which is why a naive scrape of this LMS produces empty documentation.
But the page hydrates from one call:

```
GET https://app.guidde.com/c/v1/quickguidde?id=<playbookId>
```

**That call requires authentication** — a bearer token the app obtains
(Guidde runs on Firebase). Verified: plain `curl` returns 401, and so does a
`fetch` issued from the page's own context without the app's header. What
*does* work is **intercepting the response the page makes itself**, which is
exactly what a browser-driven capture is positioned to do. The extractor must
therefore navigate to the share page and capture the response, not replay the
request. It returns, per step:

| Field | What it is |
|---|---|
| `description` | the written instruction — the prose a reader actually wants |
| `subtitles[]` | `{text, start, end}` — **a real transcript with timings** |
| `audioNote.markdown` | the narration script |
| `docScreenshot`, `drawnScreenshot`, `previewScreenshot` | per-step stills, including the annotated one |
| `videoUrl` | that step's video clip |
| `layers[].firstFrameUrl` / `lastFrameUrl` / `thumbnail` | frame-level stills |
| `sourceCaptureVideo.url` | the full original recording |

Plus `title`, `duration`, `orgName`, `lastUpdatedBy`, `language`.

**This is better than anything OCR or frame-sampling could reconstruct**: it is
the authored text, not a guess at it.

### What "transcript" actually means here — measured, not assumed

Audited on one playbook from the journey: the raw playbook holds **11 steps** (the UI numbers only the 6 that are narrated
content; the others are cover, end, and silent transitions), with **13
subtitle segments covering 41.1 s of an 81.4 s video**.

That ~50% is not missing transcript — **it is silence**. `audioNote.type` is
`textToSpeech` on narrated steps and absent on the rest, so the gaps are
screen action with no speech to transcribe. Three steps carry no subtitles at
all for that reason, and they still carry screenshots.

Two consequences worth designing around:

- The honest description is **"the complete narration, plus per-step
  instructions"**, not "a full transcript" in the sense of a lecture recording.
  For a Guidde whose audio is generated from an authored script, the text *is*
  the source and the audio is the derivative — so there is nothing to recover
  that the JSON does not already have.
- **Do not assume every step has text.** An extractor that requires
  `description` or `subtitles` on each step will crash on a normal playbook.
  Silent steps are legitimate and should emit a screenshot with a note, not an
  error.

Whether a human-recorded Guidde (rather than a TTS one) yields the same
fidelity is **unverified** — that is a question for the first playbook that
turns out not to be `textToSpeech`.

### 2. Continu is a GraphQL application

A Continu tenant (`<tenant>.continu.co`) loads its content from
`https://usw2-api.continu.co/graphql` (with `/api/v3/config` for bootstrap).
The journey page's own DOM is a rendering of that data.

So the LMS structure — journeys, their contents, the media inside each — is
available as structured data behind the user's existing session, rather than
something to be recovered from HTML.

## The LMS structure — measured on the real journey, and it changes the design

Explored one journey while signed in. Four findings, in
descending order of how much they hurt:

### 1. There is one URL for an entire journey

Clicking from the journey into a track **does not change the URL**. The title
changes, the content changes, the address bar does not. Every level below the
journey is client-side state.

So *"give WebShot the URL of each training page"* has no referent: those pages
have no URLs. A journey has exactly one address, and everything inside it is
reached by interacting with the page. This is not a capture of N URLs; it is
**one scripted walkthrough of one URL**, and the tool has nothing like it
today.

### 2. The hierarchy is four levels deep

`Journey → Sections → Tracks → Modules`, where a module is an **Article**
(the content) or an **Assessment** (a quiz). The measured track held six
modules — four articles, two assessments — inside one section, and the journey
holds three sections. Articles are where the videos and the outbound links
live; everything above them is navigation.

### 3. Content is progressively locked, and that is an access control

Two of the three sections showed **Locked**, as did one track inside the
unlocked section, gated on completing what precedes them. Progress was 13%.

**Locked content cannot be captured, and must not be.** The GraphQL API might
well return it, and reaching for that would be bypassing a gate the product
put there — which `docs/11` forbids in the same breath as everything else this
project refuses to do. The honest scope is: *capture what is unlocked, record
what is locked and why, and re-run after progressing.* A complete corpus is
something the user unlocks over time, not something a tool extracts in one
pass.

### 4. Navigation is buttons, not links

`Next in Journey`, `Back to Journey`, `Cancel`, and the track and module rows
are buttons and click handlers. The whole journey page rendered **four**
anchors, all of them app chrome (`/explore`, `/dashboard`, `/workshops`).
Link-following finds nothing; the walker has to click.

## Outbound links to the documentation portal

Articles link out to the vendor's documentation portal (`support.example.com`
in the examples below) — a separate portal behind its own login. The requirement is **preserve them as working hyperlinks, do not
follow them**, which happily matches the no-spidering non-goal exactly.

Chromium's `page.pdf` turns real `<a href>` elements into PDF link
annotations, and `links.json` already records resolved link text and
destinations, so *if* those links are true anchors this works with no new code.
**That is unverified**: no article was opened during this pass, and this LMS
has already shown a habit of rendering clickable things as buttons. If the
resource links turn out to be click handlers, the href has to be recovered
from the handler or the underlying data and re-attached — which is real work,
not a free property.

**Spike it on a real article before assuming either way**, and assert on it:
a captured article's PDF should contain a link annotation whose URI is the
portal target, and `links.json` should list it. Relative
URLs must be absolutised so they still resolve outside the LMS.

## Opening an actual Article — two answers, one of them unwelcome

The earlier pass never opened an Article; it stopped at the journey shell and a
track's contents list. Opening one Article (the *sampled article* below) settles
both open questions.

### The outbound links are real anchors — this part is free

They are genuine `<a href target="_blank">` elements:

```
<knowledge-base article>
  → support.example.com/…/Login.aspx?ReturnUrl=…/<kb-article-path>?ID=<n>
<knowledge-base article>   → …/<kb-article-path>?ID=<n>
<service request>          → …/<service-request-path>?ID=<n>
```

Chromium will emit PDF link annotations for these and `links.json` will record
them, so preserving working hyperlinks costs no new code — only a test that
asserts it. Note the shape: they route through `Login.aspx` with a `ReturnUrl`,
so the captured link lands a reader on the portal's login and then at the
article. That is the correct thing to preserve; do not attempt to unwrap it.

### Not every video is a Guidde — and the others have no transcript

The video in this article is **not** a Guidde playbook. It is a plain
`<video>` served from Continu's own storage:

```
https://usw-input-videos.continuassets.com/…/<uuid>.mp4   (251.6 s)
```

No `<track>` elements, no `textTracks`, no caption or transcript reference
anywhere in the page. The article's entire body is ~1,060 characters — the
Navigation, Notes and Resources visible on screen — while the video runs **four
minutes**. For this module, the video *is* the content, and there is no
authored text behind it the way there is for a Guidde.

So there are at least two kinds of embedded video, and they are not
interchangeable:

| | Guidde playbook | Continu-hosted video |
|---|---|---|
| Identified by | a Guidde player with a share link | a native `<video>` from `continuassets.com` |
| Written walkthrough | yes — authored steps | none |
| Transcript | yes — narration with timings | **none available** |
| Stills | yes — per step, annotated | none; frames only |
| What capture gets today | everything | a URL and a duration |

**This is the gap the plan did not know about.** The Guidde work documents one
class of video completely and the other class not at all, and the sample article
suggests the second class is not rare.

Three honest options, none free:

1. **Capture the file and say so.** Download the MP4, record it in the manifest
   with its duration, and state plainly in the bundle that this video is not
   transcribed. Cheap, truthful, and leaves a four-minute hole in what an agent
   can learn.
2. **Frame sampling plus OCR.** Sample frames, OCR on-screen text, and emit
   timestamped stills. WebShot already has the OCR machinery. Recovers what is
   *shown* but nothing that is only *said* — and these are narrated product
   demos, so that is a real loss.
3. **Speech-to-text.** The only route to what is actually said. It is a new
   dependency and a new class of correctness problem (a wrong transcript that
   looks right is worse than none), and it was explicitly ruled out for
   Guiddes — where it was unnecessary. Here it would not be unnecessary.

**Recommendation: (1) now, and decide (2) versus (3) only after counting how
many modules are Continu-hosted rather than Guidde.** If most videos are
Guiddes, the hole is small and honestly-labelled. If most are not, the corpus
is materially incomplete and that count is what should drive the decision —
not an assumption made in advance.

## The user must never have to copy a URL

The natural instinct — open the page you want, copy the address, hand it to
WebShot — **cannot work here and never will.** Confirmed twice: the address bar
holds the journey URL no matter how deep you are, and `contextId` does not
address a module. Copying the URL from an article returns the journey overview,
which is exactly what happened. This is not a workflow to fix; it is a workflow
to abandon.

**The interface is therefore: one journey URL, once.**

```
webshot journey https://<tenant>.continu.co/journey/<id> --auth-profile ~/.webshot/profiles/work
```

Everything below that — sections, tracks, modules, both kinds of video — is
WebShot's job to enumerate and visit. The user never navigates, never copies,
never keeps a list in step with a course that changes.

### Navigating by id rather than by clicking

> **Reversed by P9.** The specification keys the walk on a **synthetic path**
> instead — see *Guaranteeing nothing is missed*. The route below is not
> disproved; the ids needed to use it are simply not observable during a walk.

The app bundle contains a **`/track/`** route, so tracks are addressable even
though the UI never shows their addresses (ids arrive over GraphQL; the
journey page's HTML exposes only the journey's own id).

That matters for reliability, not for the user:

- A walker that **navigates to a known id** recovers from a failure by going
  straight back to where it was. A walker that **clicks its way down** has to
  replay every click, and a changed layout breaks it.
- It makes the walk **resumable and idempotent** — keyed by module identity
  rather than by position, which is what a long journey needs.

So the design is a hybrid, and the split is deliberate:

| Job | Mechanism | Why |
|---|---|---|
| Enumerate the structure | GraphQL | Reliable ids, titles, types, lock state — the same list the UI renders |
| Render a module for capture | Browser, by id where a route exists | Fidelity, and it is what the user is authorized to see |
| Reach anything locked | **Neither** | Locked is an access control (see above) |

Enumerating locked modules is fine — the UI lists them too, and the index needs
to say what was skipped. *Fetching their content* is the line.

### How the output has to be organized

The hierarchy is the product, not an implementation detail: an agent asked
"how do I refresh a test environment?" needs to find that answer *and* know which track
it belongs to.

```
<journey-slug>/
  index.md · index.json        the map, and what is missing from it
  01-basics/                           (section)
    01-getting-around/                 (track)
      01-the-support-portal/           (module → PDF + bundle)
      02-navigating-a-project/
      ...
  02-configuration/            LOCKED — recorded, not captured
  videos/                      deduplicated by content hash, referenced from modules
```

Numeric prefixes preserve teaching order, which prose loses. Every module keeps
its own PDF and bundle (so one module can be handed to an agent alone), while
`index.json` carries the whole tree, and every chunk names its journey, section,
track and module so a retrieved passage can always be placed.

## Transcripts for the non-Guidde videos — the spike is answered

The cheapest outcome would have been Continu serving its own captions. It does
not. Measured against the sampled article's video:

| Probe | Result |
|---|---|
| `<track>` elements, `video.textTracks` | none |
| `transcript` / `caption` / `vtt` vocabulary in the rendered app | none |
| `…/<id>.mp4` (range request) | **206** — publicly fetchable, no auth |
| `.vtt`, `.srt`, `.json`, `.txt`, `.mp4.vtt`, `/captions.vtt`, `-captions.vtt`, `_en.vtt` | **403 on every one** |

So: **the video is downloadable and there is no authored transcript to find.**
That closes the cheap option and makes speech-to-text the only route to what is
actually said.

### That is a measurement, not a rule

Do not encode "Continu videos are never transcribed". The implementation is a
**preference order**, and each video decides which branch it takes:

| | Source | Provenance |
|---|---|---|
| 1 | `<track>` / `textTracks` on the element — read through the page | `authored` |
| 2 | A provider API with authored steps and subtitles (Guidde) | `authored` |
| 3 | Neither — local speech-to-text | `asr`, naming the model |
| 4 | Frame OCR, **alongside** any of the above and never instead | `ocr` |

**Path 1 is built (L2b-b).** Three things it settled that this section did not
anticipate:

- **Read through the page, never out of band.** Setting `track.mode = 'hidden'`
  makes the browser fetch and parse the VTT itself. Measured: that request
  crosses a `context.route("**/*")` gate like any other subresource, so it
  inherits the capture's network policy and needs no host allowlist — where an
  `APIRequestContext` fetch would have sailed past it (P7-6). `hidden` rather
  than `showing`, so the cues are parsed and never painted. This is the P7-1
  shape applied a second time, and it is what makes docs/11 principle 2's "that
  page's own subresources, as any browser does" true rather than argued.
- **Only `kind="captions"` is a transcript**, as an allowlist. `subtitles` are a
  *translation* — a German subtitle track is not a transcription of English
  speech — and a `<track>` with **no `kind` defaults to `subtitles`** in HTML,
  so a denylist of "not subtitles" would let exactly the ambiguous case through
  wearing the word transcript. The translation is kept as its own artifact and
  is deliberately **not** inlined into `content.md` or chunked: those are the
  retrieval surfaces, and text that enters them is indistinguishable from source
  at the point it is retrieved. The *transcript* goes into all of them, for the
  same reason inverted — words an agent cannot retrieve are words the bundle
  does not have (P10-10). It is inlined where the player appeared and chunked
  as `media_transcript`, in groups of cues bounded by
  `TRANSCRIPT_CHUNK_CHARS` so a question about ten seconds does not retrieve
  twenty minutes.
- **A declared track that cannot be read is a third state.** Over `file://`
  every document is an opaque origin, so the browser fetches the caption file
  and discards the cues (`readyState === 3` plus an `error` event). Reporting
  that as "no caption track" would send a reader away from a transcript that
  exists. There are now three notes where there were two.

That last one decided the test strategy, and it is worth stating because it is
this repository's signature defect in a new coat: **`embedded-media` captures
from a `file://` URL, so it can never exercise path 1** — it would read zero
cues, record `transcribed: false`, and pass. A case that cannot fail. It is kept
as the recorded evidence of the unreadable state, and `captions-read` serves the
same page over loopback for the readable one.

### The design that follows

> **Not built here.** Speech-to-text transcription of non-Guidde video is out of
> scope for this repository (L3 in *Where this leaves the phasing*). The design
> is kept as the record of what the measurements above imply.

1. **Fetch the video** — it needs no credentials, so this is ordinary.
2. **Transcribe locally**, behind a `webshot[transcribe]` extra:
   **faster-whisper** or **whisper.cpp**. Both are MIT, both run entirely on
   the machine, and neither needs an API key or a subscription — a hard
   constraint here, not a preference. The model downloads once; `webshot
   doctor` should report whether it is present.
3. **OCR the frames as a complement, never a substitute.** WebShot already has
   the OCR machinery, and what a narrated demo *shows* is frequently what its
   audio omits — menu paths, field names, exact URLs, the operational detail an
   agent most needs. Speech and screen are different channels; capture both.

### Provenance is not optional here

A machine transcript that reads like an authored one is **worse than no
transcript**, because nothing downstream can tell it might be wrong. Every
transcript artifact therefore records how it was produced:

- `authored` — supplied by the source (Guidde's steps, or captions if some
  future module has them)
- `asr` — machine-transcribed, naming the model, carrying per-segment
  confidence where the engine reports it
- `ocr` — read off frames

That marker belongs in the artifact, in `manifest.json`, and in the embedded
README, so a reader meets it before acting on the text rather than after.

Output shape stays identical to the Guidde case — `transcript.txt`,
`transcript.vtt`, timed segments in JSON, frames as assets, chunks carrying
video id and timestamp — so an agent sees one format however the words were
obtained. Only the provenance marker differs.

## Enumeration must intercept on first load — the cache hides it otherwise

Attempted to capture the journey's GraphQL traffic by hooking `fetch` and
`XMLHttpRequest` and then navigating around the app. **Nothing fired.**
Continu caches its GraphQL results client-side (Apollo-style), so moving
between journey, track and module — even to a section not visited this
session — is served from memory. The queries run on the *initial page load*
and effectively never again.

Two consequences for the walker:

- **It cannot provoke a query by navigating.** Any design that expects to
  observe traffic while clicking around will observe nothing.
- **It must intercept during the first load**, which is precisely the
  mechanism the Guidde extractor already uses. So this is not new machinery —
  the same response-interception path serves both, and that is an argument for
  making it a shared capability rather than a Guidde-specific one.

Worth stating plainly because the alternative reading — "GraphQL enumeration
did not work" — is wrong. It works; it is just only observable at the moment
the application boots.

## What this means architecturally

The naive approach is to point a browser at each page and scrape. The research
says don't: **capture the page for fidelity, and fetch the structure for
meaning.** WebShot already does exactly this pairing — a rendered PDF beside a
machine-readable bundle — so this is the existing architecture applied to a new
source, not a new architecture.

Three pieces of work, in dependency order.

### A. Batch capture (roadmap item, now the blocker)

`docs/08` already lists `--from-list urls.txt` with a manifest-of-manifests.
An LMS journey is a list of pages. This is that feature, and everything else
depends on it.

**This is bounded enumeration, not spidering.** The list comes from an explicit
source — a file, or a journey's own contents — never from following links
outward. The non-goal in `docs/01` ("site-wide crawling / spidering") stands;
per-host pacing from `docs/11` §2 applies.

### B. A Guidde extractor — **built**

`webshot <page-url>` now reads the walkthrough of every Guidde player embedded
in the page and writes it into both deliverables. What shipped, and the three
places it differs from what this document originally proposed:

- **The playbook is intercepted, not fetched** (docs/09 P7-1). The endpoint is
  authenticated; WebShot navigates a page to the share URL and reads the
  response the Guidde app makes for itself. There is no HTTP-client path and no
  token replay, deliberately.
- **The id is a path segment on every Guidde surface** (docs/09 P7-2), so
  detection is an attribute scan over the prepared DOM. Nothing has to be
  clicked — which is what the L0 spike was for, and it came back cheaper than
  feared.
- **Timings are per-step and are offset onto the playbook's clock**
  (docs/09 P7-3), which reproduces Guidde's own published `.vtt` cue for cue.
  One intercepted response is therefore enough.

Per video the bundle gains `videos/<playbookId>/` with `walkthrough.md`,
`steps.json`, `transcript.txt` and `transcript.vtt`, the annotated step stills
under `assets/`, per-step chunks in `chunks.jsonl` carrying playbook id, step
number, timestamp and screenshot, and a record in `manifest.json`. The PDF
gains an appendix with one bookmark per video and one per step. `content.md`
inlines each walkthrough in the section the player was in, so reading order
matches the page. `--no-videos` reproduces the previous output exactly;
`--video-assets` adds the clips.

**Both kinds of video are recorded, and what is documented depends on what
the page shipped.** A plain `<video>` gets its source URL, duration, poster and
declared caption tracks in the manifest. If one of those tracks is
`kind="captions"` and the browser can read it, its cues *are* the transcript
and the record says `transcribed: true` with `transcript_provenance:
"authored"` — the publisher wrote those words, the same standing a Guidde
walkthrough's own steps have. Otherwise the record says `transcribed: false`
and states which of the three reasons applies: no track at all, a track that
could not be read, or a track that is a translation rather than a record of
what was said. The reason is said plainly in `content.md` and in the PDF's
embedded README.

A `subtitles` track is kept as a file and named a translation, and is
deliberately never inlined into `content.md` nor chunked, so nothing
downstream can retrieve it as though it were the source.

`manifest.video_tally` counts each kind — `documented_walkthroughs`,
`transcribed_media`, `untranscribed_media` — so the journey-wide count of
Guidde versus Continu-hosted is a sum of manifests rather than a separate
measurement. The three parts are exhaustive and sum to `total`; they have to,
because a reader deriving one by subtraction would silently absorb whatever
the tally forgot (docs/09 P7-8, and P10-9 for the time it did).

### C. A Continu source — **built**

Enumerate a journey into an ordered list of modules and capture each through
the pipeline that already existed. Both halves are built — `webshot journey`,
specified at the foot of this document — and the walk carries a safety problem
the per-module work did not; *The walker's safety rules* below are the answer
to it.

## What "optimized for an AI agent" has to mean concretely

The goal is a corpus an agent learns from, which constrains the output beyond
"capture the pages".

- **A top-level index** (`index.md` + `index.json`) across everything captured:
  journey → section → track → module, each with its type, duration, whether it
  has a Guidde walkthrough, whether it carries an untranscribed video, and its
  locked/skipped status with the reason. This is the first thing an agent
  reads, and — more importantly — **the only thing that tells it what it does
  not have.** A corpus that cannot describe its own gaps invites confident
  answers built on absent material.
- **Deduplicate media by content hash.** The same video may appear in several
  modules; store it once, reference it many times, and record every referencing
  module so provenance survives the deduplication.
- **Make the walk resumable and idempotent.** A journey is long; a failure at
  module twenty must not require redoing nineteen. Key state by module
  identity, never by position.
- **Full provenance on every chunk**: journey, track, module, and — for video
  chunks — the playbook id, step number and timestamp, so a claim can be traced
  to the second of the video it came from.

## Expectations, so correct behaviour is not "fixed"

Article bodies on this LMS are genuinely short — the sampled article
is about 1,060 characters against a 4-minute video. **A thin `content.md` on
such a module is correct, not a capture failure.** What makes the bundle
valuable there is the video documentation attached to it, which is exactly why
the two-video-type gap above matters more than it might first appear.

## Ethics and terms — stated once, then it is the operator's call

This is training material the user has been granted access to, captured for
their own learning and reference. That is the same act as taking notes on it,
and it is squarely what `--auth-profile` exists for: rendering what the
signed-in user is authorized to see.

Three things stay true regardless (`docs/11`):

- **Authorized-view only.** Nothing here bypasses an access control; if a page
  will not render for the signed-in user it is not captured.
- **The vendor's terms govern the content**, and reviewing them is the
  operator's responsibility, not the tool's. WebShot's contribution is that
  every artifact records where it came from and when, so downstream use can be
  audited rather than guessed at.
- **Redistribution is a different act from capture.** A corpus assembled for
  one person's learning is not a corpus to publish.

## Suggested phasing

| Phase | Work | Why this order |
|---|---|---|
| L0 | Spike: resolve a playbook id from an embedded player in a Continu page | It is the one unknown, and it is small |
| L1 | **Journey walker** — drive one journey by clicking, capturing each unlocked module, and stop at locked ones | Replaces batch capture: modules have no URLs, so a list of URLs cannot exist |
| L2 | Guidde extractor + bundle shape | Highest value per unit of work — it is a JSON fetch |
| L3 | Continu enumeration + Guidde attachment | Ties the two together |
| L4 | Corpus assembly: one merged artifact set an agent can be pointed at | The actual goal |

L2 is worth doing even alone: it would already let the user hand an agent
complete written walkthroughs of every video in the LMS.

## The walker's safety rules: binding, before any of it is written

> **Superseded in one place by P9.** The rules below still bind, but they were
> written for a walk over a partly-locked journey. **Completion is now a
> precondition** (*Progress safety, restated with evidence*), so "capture only
> what is unlocked" describes a situation the walker must not be run in at all.

Continu tracks completion, and progress feeds a **real training record that
the organization assigning the training can see**. An automated walk can mark
modules complete that the user never watched. That is an outward-facing,
hard-to-reverse side effect on a person's training record, and it outranks
every other requirement here.

These are requirements on the implementation, not suggestions:

1. **Read the journey's progress percentage before and after the walk, and
   record both in the manifest.** If it moved, that is a defect to report
   loudly — not a footnote. Nothing else in this document is worth shipping
   without this check, because without it a regression is invisible.
2. **Open a module by clicking its row, never by "Next in Journey".** Measured:
   viewing tracks and opening one article left progress unchanged at 13%.
   "Next in Journey" is **untested** and is the likely mutator — its whole
   purpose is to advance.
3. **Never open, start, or submit an assessment or quiz.** Clicking one may
   create an attempt. Record that it exists — title, type, duration — and skip
   it. Assessments are the one module type the walker must refuse outright.
4. **`--dry-run` first.** It enumerates what *would* be captured — modules,
   types, durations, locked state — and touches nothing: no navigation into a
   module, no capture, no download. It is what the user should run before the
   first real walk, and what a reviewer should read to see what the walk
   intends to do.
5. **If the walk cannot avoid changing progress, stop and report it.** That is
   the user's decision to make, not the build's. Shipping a walker that mutates
   a training record because the alternative was hard is not an acceptable
   trade.

Locked content interacts with this and the rule is unchanged: **capture only
what is unlocked.** Enumerating locked modules is fine and the index must
record them with names and reasons, so a later run can fill them in. Do **not**
use GraphQL to retrieve locked content — it may well work, and that is
precisely the reason not to (docs/11: nothing here bypasses an access control).

## Before writing the transcription: five things that will bite

> Out of scope for this repository, like the design it qualifies (L3); kept for
> whoever builds it.

1. **Golden determinism.** ASR output is not reproducible, and this project's
   test discipline is byte-comparison goldens. No raw ASR text may enter a
   byte-compared golden. Mask transcript bodies in the harness the way raster
   bytes and `tool_versions` already are — docs/09 P6-3 set that precedent and
   this is the same category, environment rather than output — and/or drive the
   golden path from a recorded fixture transcript. **CI must never depend on a
   model being present.**
2. **Hallucination on silence.** A documented Whisper failure mode, and these
   videos are known to have silent stretches: the Guidde sample measured 41.1 s
   of speech in 81.4 s. Enable VAD filtering and force `language="en"` rather
   than auto-detect, which mis-fires on short or quiet clips. A confidently
   wrong transcript is the exact failure this feature must not produce.
3. **Download is not keep.** Transcription needs the bytes; the bundle does not
   have to retain them. Fetch to a temp path, transcribe, discard unless
   `--video-assets` was given — otherwise transcription silently turns on the
   large-payload behaviour that flag exists to keep off.
4. **Transcribe once per video, not once per module.** Key the cache by content
   hash. The same video appears in several modules and ASR is minutes of CPU,
   not milliseconds. This rides on the media dedupe.
5. **Model choice is a correctness decision, not a performance one.** This
   content is dense with proper nouns — the vendor's product name, its own
   terms for configuration objects, menu labels — and small models mangle
   exactly those. Default to a model that gets them right even though it is slower,
   make it configurable, and record the model name in the provenance. Note the
   tradeoff in the docs rather than silently choosing speed.

### One refactor comes first

The output shape has to be identical whatever produced the words, and it was
not reachable: `bundle/videos.py` and `render/video_appendix.py` read
`GuiddePlaybook`/`GuiddeStep`. Those are WebShot's own dataclasses, so the
bridge rule held — but the bridge's promise, that a second provider means
rewriting one file, did not. **Done (L2b-a):** they are `Walkthrough` /
`WalkthroughStep`, and `Walkthrough` carries `provider` (which service produced
it) and `id` (that service's own identifier for it, formerly `playbook_id`).

The published vocabulary deliberately did **not** move with them.
`manifest.videos[]` and `assets.json` still say `playbook_id`, because those are
the contract and renaming a wire field would be a format change wearing a
refactor's clothes. `mypy` is what kept the two apart: renaming the dataclass
attribute made every stale access an error while leaving the six contract uses
valid.

One thing the rename did not reach, said here rather than left to be noticed:
`bundle/videos.py` still calls its local variables `playbook`, in a module
docs/02 describes as knowing nothing of Guidde. The *types* are neutral, which
is what this section asked for; the identifiers are not. It was left out on
purpose — the word also appears inside log messages and locator strings that the
golden corpus records, so a blind rename would have moved recorded output on a
change whose entire argument is that nothing moved.

## Where this leaves the phasing

| Phase | Work | State |
|---|---|---|
| L0 | Resolve a playbook id from an embedded player | **done** — docs/09 P7-2 |
| L2 | Guidde extractor, bundle shape, PDF appendix | **done** — this branch |
| L1 | Journey enumeration (`--dry-run`) | **done** — `webshot journey URL --dry-run`, `src/webshot/journey/` |
| L1b | The capture pass the enumeration is the contract for | **done** — `journey/capture.py`; refuses assessments by allowlist, resumes on an outline digest, paces per host |
| L4 | Corpus index, media dedupe by hash, resumable state | **done** — `journey/index.py` and `journey/media.py`; index, chunk paths, `media/` with back-references, cross-references with ambiguity kept |
| L2b-a | Generalize the bridge's types | **done** — `Walkthrough`/`WalkthroughStep` with `provider` and `id`; corpus byte-identical across all 25 cases |
| L2b-b | Read `<track>` caption files — preference-order path 1 | **done** — `bundle/captions.py`; allowlist on `kind="captions"`, three states, read through the page |
| L3 | Local ASR behind `webshot[transcribe]`, then frame OCR alongside it | **out of scope for this repository** |

**docs/13 is otherwise complete.** L0, L1, L1b, L2, L2b and L4 are built. The
rows are ordered by what happened rather than by number, because the number
ordering is a record of what was *proposed* and this table is meant to say what
is *true*.

L3 is not planned here: speech-to-text transcription of non-Guidde video is out
of scope for this repository. A video that is not a Guidde playbook and ships
no readable `kind="captions"` track is recorded as untranscribed, with the
reason, in the manifest, `content.md` and the PDF's embedded README — the gap
is stated rather than filled. The L3 design and its five hazards above stay as
the record of what the measurements imply for anyone who builds it elsewhere.

The rename was the mechanical job the section above called it — three files
referencing the old names, none in `src/webshot/journey/`, so the capture work
did not enlarge it. Doing it before a second producer arrives is the whole
point: it is mechanical while there is one.

## P9 — the walk, measured against a completed journey (2026-08-22)

Everything above about the walk was designed against a *partly complete* journey
and a locked structure. One journey was then completed end to end — `Your
Progress 100%` — and it was driven live. Nine findings
change the design. Three reverse a recommendation made earlier in this document
— the hierarchy's depth (P9-1), counting modules by video type (P9-2), and
navigating by id (P9-4 with P9-6) — and one (P9-7) is invisible to every check
the capture makes about itself.

### P9-1 — the hierarchy is five levels, not four

This document records `Journey → Sections → Tracks → Modules`. Measured:

    Journey
      Section            ×3
        Track            ×8
          Numbered group ×N     "1. Introduction", "2. …"
            Module              Article | …

The numbered group is a real level with its own title, its own description
prose, and its own collapse state. That description appears nowhere else, so it
is content the capture must take. A walker written to the four-level model
descends into a track and finds nothing, because the modules are one level
deeper than recorded.

The three sections hold eight tracks between them — three, three and two.

### P9-2 — the type badge cannot tell you which modules hold video

This document recommends counting modules by video type during the walk and
letting that number decide between frame OCR and ASR. **That count is not
obtainable.** Continu types video content as `Article`:

| | Article A — the sampled article above | Article B |
|---|---|---|
| type badge | `Article` | `Article` |
| `<video>` | 1 — `usw-input-videos.continuassets.com`, 251.6 s, **no `<track>`** | 0 |
| Guidde iframe | 0 | 1 — `embed.app.guidde.com/playbooks/<playbookId>?mode=videoOnly` |
| body text | 1,064 chars | 506 chars |

Both are `Article`. The badge carries no signal, so the only way to know what a
module contains is to open it. The count is not cheaper than the walk, and the
OCR-versus-ASR decision cannot be deferred to it.

### P9-3 — detection needs no new code, and that is verified

Both kinds are a DOM read, and the shipped detector already handles the real
production URL — checked against `extract/guidde.py` rather than assumed:

    host    : embed.app.guidde.com     GUIDDE_HOSTS registrable-suffix match -> True
    id      : <22-char playbookId>     PLAYBOOK_PATH, `?mode=videoOnly` terminates cleanly

`find_embeds` takes the Guidde case, `detect_media_videos` the plain `<video>`,
and `enrich_with_videos` routes both. **The walker is navigation; the capture
already exists.** That is a materially smaller build than this document assumed.

One correction to the fixture: production carries the embed on `src` (eager),
while `tests/fixtures/lms_page.html` models `data-src` (lazy). Both are in
`URL_ATTRIBUTES` so both work, but the corpus currently exercises only the rarer
shape.

### P9-4 — navigation, and three ways it will bite

Confirmed: the URL never changes. Clicking from journey into a track leaves the
address bar on `/journey/<id>?contextId=…` and only the tab title moves. Four
anchors at every level, all app chrome — tracks, groups and modules are buttons
and `article`-role rows. **Nothing is in the DOM until it is opened**: the
collapsed journey page is 451 characters of body text and contains no track
names at all.

Affordances: `← Back to Journey`, `Back to Track`, `Show Track Contents`, and
`Cancel` / `Next in Journey` at the foot of a track.

Three hazards, each measured:

1. **`document.title` goes stale.** After `← Back to Journey` it still read the
   last module's name. Anything keyed on it mislabels the journey as a module.
   Read the heading from the DOM.
2. **Element references go stale across expansions.** A reference captured
   before an expansion toggled a different accordion open and shut in one
   action — no visible change, no error. Re-read the tree after every state
   change.
3. **Expansion state survives a return to the journey**, which is the one thing
   working in the walker's favour: expand once, revisit cheaply.

### P9-5 — scale, and what it forces

The largest track alone carries **nine** numbered groups, three
modules in the first. Eight tracks at that shape puts the journey in the low
hundreds of modules. Resume-from-partial and an enumerate-only `--dry-run` stop
being conveniences.

### P9-6 — completion state is not machine-readable — **SUPERSEDED by P12-3**

> **This entry is wrong.** Completion *is* machine-readable: a `Completed`
> paragraph on section accordions and `data-testid="completed-icon"` on track
> rows. The measurement below was taken inside a module page, where the tick
> icons do not live — an absence observed in the wrong place and written up as
> a property of the application. The two-pass contract it supports still
> stands, on P9-4's surviving leg (nothing is in the DOM until it is opened),
> not on this one. Kept rather than deleted so the reasoning that depended on
> it stays legible.

The URL never changes (P9-4) and there is no sitemap. Nor is there any way to
read completion off the page: **250 unlabelled SVGs**, no `data-testid`, and no
`aria-label` on the tick icons. The UI shows a human which modules are done;
the DOM says it in no form a walker can assert on.

That closes the last route to reading coverage off the application. Together
with P9-4's *nothing is in the DOM until it is opened*, it means a single-pass
walk can never know what it missed — "captured 40 modules" is a number with no
denominator, and this project has already established that a count whose job is
to be summed must be complete (docs/09 P7-8).

Coverage therefore has to be **constructed rather than read**, which is what the
two-pass contract in the specification below exists for — and the reason
`--dry-run` is the contract and not a preview.

### P9-7 — the rendered text is clamped while the DOM text is whole — **the missing check is CORRECTED by P13-1**

Measured on a module page: one `chakra-collapse` block rendering **115px of
161px** of its content, and **five** `fr-view` (Froala body content) blocks
under `-webkit-line-clamp: 3`.

`innerText` returns the full string in every one of those cases. So the
**bundle is complete** — `content.md`, the chunks, and every text assertion a
capture makes about itself all pass. **But WebShot renders a PDF, and the PDF
shows what was laid out**: the clamped version, with the rest of each block
simply absent.

This is *green is not correct* in a new place. Every check the capture makes
today reads the DOM; the deliverable is the render; **nothing compares the
two**. So a module can pass its whole suite and ship a visibly truncated page.
The check that would catch it does not exist and is nameable: extract the text
back out of the published PDF and assert it against `content.md`. Until
something does that, the clamp handling below is the only thing standing
between this LMS and a quietly incomplete deliverable.

Two things are required before the page is printed, and neither substitutes for
the other:

1. every `Read more` / `Read More` control clicked, and
2. the **residual** clamps — whatever survives the clicking — neutralised in the
   print CSS.

Both belong in the preparation stage, beside the print CSS that already lives
there (`src/webshot/capture/prepare.py`); neither exists today. Note what this
does *not* change: P9-3 still holds, and the walker still writes no video code.
This is page preparation, which is a different seam.

### P9-8 — module metadata at no other level, and a label that disagrees

Per module, and available nowhere above it:

    title           DOM heading (not document.title — P9-4)
    author          e.g. a team name
    type badge      "Article" on every module measured (P9-2)
    duration label  authored
    group prose     the numbered group's description (P9-1)

The duration label is **known to disagree with the artifact it describes**:
**"6 Mins" against a video measured at 251.6 s** — the duration P9-2 records
for article A's video. Record both and trust neither. The label is
what the LMS claims; the measured duration is what the file is; a corpus that
silently picks one is asserting something nobody checked.

Following P9-2, the type badge is recorded and never branched on. "Article on
every module measured" is exactly the sample size that makes a branch on it a
latent bug.

### P9-9 — one outbound target is a service request, not a KB article

The documentation-portal links keep their `Login.aspx?ReturnUrl=…` form and
are never followed, as recorded above. One observed target resolves to a
**service-request form** rather than a KB article.

Recorded because `tests/fixtures/` models only the KB-article shape — the second
fixture skew this pass found, after P9-3's eager-versus-lazy embed. Nothing in
the capture path branches on which kind it is and nothing should, but a test
asserting *the outbound target is a KB article* would be asserting a property of
the fixture rather than of the LMS.

### Progress safety, restated with evidence

`Your Progress` held at **100%** across every expansion, one track open, one
module open, and the return trip. Reading is safe on a completed journey.

**`Next in Journey` remains untested and must not be assumed inert.** No
Assessment module appeared anywhere in this journey — every module encountered is
typed `Article` — so the never-open-an-Assessment rule was not exercised and
stays in force.

**Completion is now a precondition, not a preference.** Run against a completed
journey the walker is a read-only crawler and the whole progress-safety design
reduces to a recorded before/after assertion. It should not be built to run
against a partly complete one.

## Stage 1 as built — what the enumeration proves, and what it does not

`webshot journey URL --dry-run` walks a journey and writes `outline.json`
(`schemas/journey-outline.schema.json`). It opens no module and downloads
nothing. Without `--dry-run` the same enumeration becomes the contract the
capture pass (L1b, `journey/capture.py`) consumes. Until that pass existed the
subcommand refused to run without the flag, because a subcommand that quietly
did half of what its name promises is the silent degradation CONTRIBUTING
rule 5 forbids.

### The stand-in, and which test discriminates each hazard

Nothing here can be checked against the live LMS from the build side, so the
whole thing rests on `tests/fixtures/journey_app/` — a real single-page
application on loopback, modelling the markup P11 measured rather than markup
invented for the purpose. docs/09 P7-11 is the precedent for what a stand-in
that cannot fail is worth, so every hazard has a **naive implementation written
against it**, and the test asserts that implementation gets the wrong answer:

| Hazard | Naive implementation | What it does instead |
|---|---|---|
| The completion modal (P11-1) | goes straight to work | click returns cleanly, `aria-expanded` never moves, body still has text — a journey with no tracks, reported as success |
| `offsetParent` on a fixed dialog (docs/09 P10-3) | `offsetParent === null` | reports the blocking dialog as absent |
| Track readiness (P11-2) | reads straight after the click | title has moved, **zero** accordions |
| Collapsed content survives (P11-4) | asks whether the rows are present | says expanded for a group it never opened |
| Emotion hashes and React ids (P11-3) | keys on `css-*` or the `id` | both change on the next render |
| A held reference rebinds (P9-4) | keeps the accordion handles | a *different* accordion opens; still attached, no error |
| Five levels (P9-1) | journey → section → track → module | **0 modules**: they are one level deeper |
| Nothing in the DOM until opened (P9-4) | reads the tree from the loaded page | no track name in the body; 4 anchors, all chrome |
| Stale `document.title` (P9-4) | takes the journey's name from it | gets the last **track's** name |
| An expansion that did not take | records the group as empty | the walk raises instead (rule 5) |
| A track that never fills in | waits a fixed interval | recorded as a complete, empty track |
| A count that stopped agreeing | a single observation | the second look, taken after every sibling is open, disagrees |
| A walk that moved progress | reports it as a footnote | the walk stops; it is a training record |
| Two track rows with one label | records the first twice | refused: track rows are reached by `aria-label` |
| Two *sections* sharing a track title | section two records section one's content under section two's ids | each click is scoped to its own section's panel (docs/09 P10-5) |
| A status word welded to a title (P12-4) | records `BasicsCompleted` | the title is the accordion's first `<p>` |
| An assessment indistinguishable from an article (P12-2) | publishes bare titles, leaving capture nothing to refuse | `module_type` on every module node |

**Two things here discriminate nothing, and are counted as tests of the fixture
rather than of the walker.** Expansion surviving the return to the journey is
the property working in the walker's favour (P9-4), so no naive implementation
fails on it. And a *collapse* cannot move the count assertion at all, because
P11-4 keeps the rows mounted — that limit has its own test, asserting the
finding list stays **empty**, so the boundary is recorded rather than assumed.

### What the stand-in still cannot reproduce

This list matters more than the one above, because it is where the next defect
is. It is shorter than it was: P11 replaced the invented DOM with the measured
one.

- **A genuine race.** The stand-in's one asynchrony is P11-2's track fill, which
  the walk polls for deterministically. It can never make the
  enumerate-twice-and-diff check fire. `diff_outlines` is unit-tested and the
  walk is wired to it, but **no test has ever seen the walk detect a real SPA
  race** — the case the check exists for.
- ~~**Module-row markup.**~~ **Measured (P12-1)** — and it was wrong: there is
  no `role="article"` on this LMS. The stand-in now models the real row, three
  `<p>`s and all, including an assessment.
- **Authentication.** The stand-in needs none. `--auth-profile` and
  `--storage-state` reach the capture path's proven session code unchanged and
  are exercised nowhere on this route.
- **Scale.** Nine modules against P9-5's low hundreds — the walk is still never
  driven at length. **Resume is built**: keyed on the synthetic path and on a
  digest of the outline it was built against, because a path alone resolves
  against any tree of the same shape and would fill one corpus from two
  structures with every count agreeing.
- **Locked content.** Completion is a precondition, so the stand-in has none.
- ~~**Opening an Assessment.**~~ **Closed by L1b-b.** The capture pass refuses
  every module the outline types `assessment`, records it with the reason, and
  never opens it — proven by a naive capture that treats every row alike and
  does open it.
- ~~**A single-group track.**~~ **Measured (P12-7)** — one exists and renders a
  normal group header, so the flat case does not arise.
- **The clamped text of P9-7.** A capture-stage concern; stage 1 prints nothing.
  P11-4 sharpens why it matters: the DOM is complete while the render is not.

## P11 — the live session's answers, and two nobody asked for (2026-08-23)

Five questions from the stage-1 build, measured on the live journey, plus two
findings the build had no way to ask about. **Two of the seven changed the
implementation and one of the unasked pair would have stopped every real run.**

### P11-1 — a completion modal blocks the page on every visit — **NARROWED by P12-5**

> **"Every visit" is wrong.** Two fresh loads of the same completed journey
> raised no modal at all. It appears sometimes and must still be cleared when
> present, but its *absence* is not evidence of anything — the walk had been
> warning that a journey reading 100% might not be complete.

Navigating to a completed journey raises a dialog — **"You Did It! You
successfully completed your Journey!"** — offering `Return to Dashboard` and
`View Journey Details`. It intercepts every click.

The symptom is the dangerous part: `.click()` returns cleanly, `aria-expanded`
never changes, and `document.body.innerText` still has content. Nothing looks
wrong. It surfaced only in a screenshot, after several minutes lost to it.

**Completion is the walker's precondition, so this is not an edge case — it is
the first thing every run meets.** Dismiss with **`View Journey Details`**,
which stays on the journey. `Return to Dashboard` navigates away and must never
be pressed.

### P11-2 — a track view is not ready when the click returns

After clicking a track, `document.title` had already changed while the body was
**141 characters** with **zero** accordion buttons. It filled in on the next
read.

So a fixed interval is the wrong instrument: too short records an empty track
as a complete one, too long is paid on every track of a low-hundreds journey
(P9-5). Poll for the group accordions and fail loudly on timeout.

### P11-3 — the real markup, and what is safe to key on

**Sections and group rows are the same Chakra accordion component:**

    <button type="button" id="accordion-button-:rq:" aria-expanded="false"
            aria-controls="accordion-panel-:rq:"
            class="chakra-accordion__button css-c0kz90" data-index="0">

**Track rows are plain buttons carrying the title in `aria-label`:**

    <button type="button" aria-label="<track title>"
            class="css-4ei0n9">        parent: div.chakra-stack.css-165casq

| Safe to key on | Not safe |
|---|---|
| `button.chakra-accordion__button` | every `css-*` class — an Emotion hash that changes with any style edit |
| `aria-expanded`, `data-index`, `aria-controls` | the `id` values — React `useId` output, not stable across renders |
| `aria-label` on track rows | |

`data-index` is the component's own ordering, so it is what the synthetic path
ids are built from — a DOM position no longer has to be inferred. That also
removed a guard rather than adding one: sections and groups are reached by
index and cannot collide, so only track rows, which are reached by label, need
the duplicate-sibling refusal.

Module-row markup was not captured and remains taken from P9-4's `article`
role.

### P11-4 — the description survives collapse, so content witnesses nothing

Measured on group 2 of a four-group track, using its
description as the marker:

| | `innerText` | `innerHTML` | `aria-expanded` |
|---|---|---|---|
| while expanded | yes | yes | true |
| after collapsing | **yes** | **yes** | false |

Body length unchanged at 1,207 characters. Chakra collapses by animating height
with overflow hidden; it does **not** unmount.

So "the rows are present" cannot witness that an expansion took — a group that
was never opened answers yes. **`aria-expanded` is the witness**, it is
authoritative, and it is the same attribute at both levels. The stage-1 build
had used content presence and was wrong to.

There is a second consequence worth taking: because collapsed content stays in
the DOM, **text extraction does not need everything expanded — but the PDF
still shows it collapsed.** That is P9-7 from the other direction, and it is
why the render and the DOM have to be checked separately rather than one
standing in for the other.

### P11-5 — siblings do not collapse

    before  [true,  false, false, false]
    after   [true,  true,  false, false]     after clicking group 2

`allowMultiple` at the group level, and the same at the section level — all
three sections were open simultaneously at one point. So the per-node count
assertion will not fire spuriously on a real journey.

Note what this leaves the count assertion able to see. A collapse cannot move
it (P11-4 keeps the rows), and no real journey collapses a sibling anyway. What
it catches is the structure *moving* — a row appearing or disappearing between
the walk's first read and its second — and `aria-expanded` covers the closing
case at expansion time. Both halves are needed and neither substitutes.

### P11-6 — the progress string is stable, and one question stays open

Exactly one occurrence, exactly `Your Progress 100%`. Comparing it whole is
safe on this journey. One caveat: the P11-1 modal also says "You successfully
completed your Journey!", so a scan of the whole body for completion language
would match the modal first — the walk reads the specific element instead.

**Q4 is unanswerable here and was not guessed at.** Every track opened has
several groups — one has 4, the largest has 9 — so **no single-group track exists in this
journey** and whether one renders a group header or flattens is unmeasured. The
walk keeps its refusal: a track that lists no groups is more likely a walker
bug than a real shape, and a loud refusal is how the flat case gets found
rather than silently miscounted.

`Next in Journey` is still unclicked, deliberately, on both sides.

## P12 — driven against the live journey, and four earlier claims fall (2026-08-24)

The first time this design was checked against the application rather than
against notes about it. The same completed journey, driven read-only through the signed-in browser.

**Progress held at `Your Progress 100%` throughout** — two sections expanded,
two tracks opened, one Article opened and returned from. Read before and after,
as the design requires.

Four things confirm exactly. **The collapsed journey body is 451 characters**
(P9-4, to the character), **four anchors** all app chrome, **nine numbered
groups** in the largest track (P9-5), and a `chakra-collapse`
rendering **115px of 161px** (P9-7, to the pixel). `document.title` went stale
on every return, twice.

Four things do not.

### P12-1 — there is no `role="article"`, and the module row is unrecognisable by affordance

The one shape never measured, inferred in P9-4 and carried ever since. Querying
`[role="article"]` inside a real group panel returns **zero**. A module row is:

    <div class="css-5r1nj7">          ← Emotion hash, unstable
      <p>Module title</p>             ← title
      <p>5 mins</p>                   ← duration, authored
      <p>article</p>                  ← type, lowercase in the DOM
    </div>

No `role`, no `data-testid`, no inner `<button>`, `cursor: auto`. It is opened
by a React `onClick` on the div itself, so **nothing about it is findable by
affordance** and the only stable handle is its shape: a child of the panel's
stack carrying three `<p>`s.

Had the walk run against this before the review, every group would have
returned empty and every self-check would have passed on `0 == 0`. The
ultrareview's third finding — an empty group must stop the walk — turned that
from a silently empty corpus into one accurate error, and this is the case it
was written for.

### P12-2 — assessments are present, and the type badge does distinguish them

P9-2 concluded the badge "carries no signal" because both sampled modules read
`Article`, and P9-8 repeated it. The very first group opened here holds two
assessments among six modules:

    <an action-items checklist>   | Less than a minute | assessment
    <a quiz>                      | Less than a minute | assessment

So **P9's "no Assessment module appeared anywhere in this journey" is wrong**,
and the rule that was "absolute but untested" is now absolute and *live on every
run*. The type is the row's third `<p>`, lowercase.

The consequence for stage 1 is concrete: the enumeration published bare titles,
which gave the capture stage nothing to refuse with. `module_type` and
`duration` are now on every module node and in the published schema.

### P12-3 — completion state is machine-readable, in two ways

P9-6 recorded 250 unlabelled SVGs, no `data-testid`, and no `aria-label` on the
tick icons, and concluded completion "cannot be read off the page". Measured:

| Where | What |
|---|---|
| Section accordion | a second `<p>` reading **`Completed`**, in plain text |
| Track row tick | **`data-testid="completed-icon"`** on the `<svg>` |
| Dashboard | `data-testid="user-menu"` |

Both are exactly the hooks P9-6 said were absent. **The two-pass contract
survives, but not on that argument** — it stands on the leg that is still true
and was re-confirmed here: nothing below a node is in the DOM until it is
opened, so the *tree* still cannot be read without walking it. Completion state
was never what the enumeration needed; the claim was simply wrong.

### P12-4 — the accordion's title is its first `<p>`, not its text

    button.chakra-accordion__button
      svg.chakra-icon        the status tick
      p                      "Basics"         ← the title
      p                      "Completed"      ← the status
      svg.chakra-icon        chevron, aria-hidden

`textContent` on that button is **`"BasicsCompleted"`**, which is what the
walk was reading. Every synthetic id, output path and chunk would have carried
the status word welded to the title.

### P12-5 — the completion modal did not appear at all

P11-1 recorded the "You Did It!" dialog on *every* visit to a completed journey,
and the walk was built to clear it first. On two fresh loads of the same journey
there was **no `[role="dialog"]`, no `[aria-modal]`, and no "You Did It" text
anywhere in the document.**

That does not refute the measurement — it was seen — but it bounds it: the
dialog is not a property of every visit. The walk still clears it when present
and no longer treats its absence as a signal. It had warned "this may not be a
completed journey", which on a journey reading 100% is both noise and wrong.

### P12-6 — `#progress` never existed, and the clamped text has no expand control

Two corrections in the same place.

`DomContract.progress` was `#progress`, a fixture-ism. **There is no element
with that id**, so the walk would have failed on its first real journey at the
line that reads its own safety evidence. Worse, the label and its value are
*separate* elements whose arrangement differs between the journey and track
views, so no single structural selector finds it in both. It is matched on the
label's text now.

And P9-7's clamping is real but not where the design put it. It is on the
**track view**, not a module page — the module opened here had no
`chakra-collapse`, no `-webkit-line-clamp` and no clamped block at all, while
both track views had them. **No `Read more`, `Show more` or `See more` control
exists on either.** docs/13 requires that "every `Read more` / `Read More`
control must be clicked, and residual clamps neutralised in the print CSS": the
first half has no referent on the pages measured, and the print-CSS half is not
a fallback for residuals but the *only* mechanism. That is a capture-stage
concern and is written down here rather than built.

### P12-7 — a single-group track exists, and renders a group header

P11-6 could not answer Q4 because every track sampled had several groups.
One track has exactly **one** group, `1. Basics`
— and it renders a normal accordion header, already expanded. So the flat case
does not arise, and the walk's refusal of a track listing *no* groups is
untouched by it.

## The journey walker — the specification

Originally derived entirely from P9. It has since been reconciled against the
live measurements — P11, P12 and P13 — because four of P9's claims did not
survive contact with the real application. Where this section disagrees with the
design earlier in this document, the entry that decided it is named inline.

**A correction is not finished until the spec that quotes it is corrected too.**
P12 overturned four P9 claims on 2026-08-23, and each was faithfully recorded —
but this section went on quoting the originals for two days, including a rule
that told a builder not to branch on the one field that identifies an
Assessment. The P-entry is the measurement; this section is what someone builds
from. Amending only the first leaves the second confidently wrong.

This is the binding specification for L1, and `src/webshot/journey/` is built
to it.

### What the walker is, and is not

**It is navigation.** Detection and capture of both video kinds already ship and
were verified against the real production URLs (P9-3). The walker's whole job is
to reach every module and hand the page to `enrich_with_videos`
(`src/webshot/bundle/videos.py`) and the existing bundle path.

**Do not write video code in the walker.** A walker branch that inspects a video
is evidence the seam was crossed, not a feature.

This document used to add that the walker still owed the page one thing, the
clamp handling of P9-7. **It no longer does**: that shipped in
`src/webshot/capture/prepare.py` as `release_clamped_text` plus the
`data-webshot-clamped` print rules, and every capture gets it for free. So the
walker's job is navigation and nothing else.

### Guaranteeing nothing is missed

There are no URLs and no sitemap, so completeness cannot be read off the page.
It has to be *constructed*.

**Not for the reason this document gave.** It argued from P9-6, that completion
state is not machine-readable; **P12-3 measured that as simply wrong** — the
section accordion carries a plain-text `Completed`, and the track tick carries
`data-testid="completed-icon"`. The two-pass contract survives on the leg that
is still true and was re-confirmed live: nothing below a node is in the DOM
until it is opened, so the *tree* cannot be read without walking it. Completion
state was never what the enumeration needed.

**Two passes, and the first is the contract.**

1. **Enumerate** (`--dry-run`): walk the tree opening only expanders, never a
   module. Emit every node with a stable synthetic id derived from its path,
   plus its title:

       journey/<id>
       journey/<id>/section-01
       journey/<id>/section-01/track-02
       journey/<id>/section-01/track-02/group-03
       journey/<id>/section-01/track-02/group-03/module-01

   The id is a **path, not a server id**, and that is a deliberate reversal of
   *Navigating by id rather than by clicking* above. That section proposed
   keying the walk on ids from GraphQL and a `/track/` route. Nothing measured
   here refutes that the route exists — but the walk cannot obtain the ids to
   use it: a module has no address at all (`contextId` does not name one), and
   Continu's GraphQL runs at boot and effectively never again, so a walk in
   progress observes no ids to key on. Position in the enumerated tree is the
   identity actually available, and it has the property the ids were wanted
   for: it survives a restart and it can be diffed.

2. **Capture**: visit exactly the module set the enumeration produced.
   - a module in the manifest and not captured → **error**, naming it
   - a module encountered and not in the manifest → **error**, the structure
     moved under us
   - counts asserted at **every level**: the journey lists N sections, each
     section lists M tracks, each track lists G groups, each group lists K
     modules — assert visited == listed at each node, **not just in total**

The per-level assertion is the part that is cheap to drop and expensive to lose.
A total-only count is satisfied by a group that came back silently empty, which
is exactly what P9-4's stale-reference hazard produces.

**Run the enumeration twice and diff it** before capturing. Identical output is
the evidence that rendering is deterministic; a difference means the walk is
racing the SPA, and **no capture from that walk can be trusted**.

Resume state keys on the synthetic path, so a resumed run can still prove
coverage rather than hoping — which is what P9-5's low hundreds of modules
demand.

### The hazards, as implementation rules

Each of these produces a capture that passes its own checks. None of them
raises. Stated here as rules because the measurements are above.

| Rule | Because |
|---|---|
| ~~Click every `Read more`, then neutralise residual clamps~~ — **built, and the walker owes nothing here** | P9-7 named the defect. **P12-6 removed the first half** (no `Read more`, `Show more` or `See more` control exists on either track view) and **P13 corrected the check** (`pdf-text.txt` already extracts the render). Shipped in `release_clamped_text` + `BASE_PRINT_CSS`, with `clamped-text` and `clamped-noise` in the corpus |
| Re-read the tree after every state change; never carry an element reference across a click | P9-4 — a stale reference toggles a *different* accordion, silently, and the group reads as empty |
| Take every title from the DOM heading, never `document.title` | P9-4 — it is stale after `← Back to Journey`, and the id, the index, the output path and every chunk inherit the error |
| **Open only a module type measured safe; refuse everything else** — `article` captures, `assessment` refuses, anything unrecognised or absent refuses with a warning naming what was read | **P12-2 reversed P9-2**: the badge *does* distinguish `assessment` from `article`, and the first group opened live held two of them. But a denylist of one string **fails open** — this table used to say "refuse an assessment", and the faithful implementation captured every value that was not exactly that. The type is read positionally from the row's last `<p>` on markup with no stable handle (P12-1), so an appended badge, an empty cell or a rename to `quiz` all read as safe-to-open — and enumerate-twice-and-diff cannot see any of them, because a structural change is identical on both passes. The safe vocabulary measured on this LMS is one value, `article`, which P9-8 confirms covers video modules too. Refusing the unknown is recoverable and loud; opening it is neither |
| Record both the authored duration label and the measured duration | P9-8 — "6 Mins" against 251.6 s |

### Navigation contract

    journey → track     click the track row <button>
    track → module      click the module row — a <div> with three <p>s (P12-1)
    module → track      "Back to Track"
    any → journey       "← Back to Journey"
    inside a module     "Show Track Contents" reveals the track outline

The module row is the detail that breaks a walker written from the level above:
a selector that correctly finds the clickable track row finds nothing one level
down. **P12-1 corrects what that row actually is.** P9-4 inferred `role="article"`
and this document carried it; querying `[role="article"]` in a real group panel
returns **zero**. The row is a `<div>` with an Emotion hash class, holding three
`<p>`s — title, duration, type — with no `role`, no `data-testid`, no inner
`<button>` and `cursor: auto`. It opens on a React `onClick` on the div itself,
so **nothing about it is findable by affordance**; the only stable handle is its
shape, a child of the panel's stack carrying three `<p>`s.

Expansion state **survives** a return to the journey, so expand once and revisit
cheaply — which is what makes a separate enumeration pass affordable rather than
a doubling of the work.

`Cancel` and `Next in Journey` sit at the foot of the track view.
**`Next in Journey` is untested and must not be used for traversal.**

### Per-module capture set

Text, images, links, and both video kinds, plus the metadata of P9-8 that exists
at no other level — including the group prose, which is one level up and appears
nowhere else (P9-1).

Outbound documentation-portal links keep their `Login.aspx?ReturnUrl=…`
form exactly and are never followed (P9-9).

### Output shape, for the agent that reads it later

One journey is **one corpus**, not N unrelated captures. The point is that a
future agent can answer "what does this training say about X" without being
handed the tree by a human.

This supersedes the four-level sketch in *How the output has to be organized*
above; the correction is P9-1's, and the path gains the group:

```
<journey-slug>/
  index.md · index.json        the map, and what is missing from it
  01-basics/                             (section)
    01-getting-around/                   (track)
      01-introduction/                   (group — prose found nowhere else)
        01-the-support-portal/           (module → PDF + bundle)
        ...
  media/                       one copy of every distinct asset, and who used it
```

**`media/`, not `videos/`, and the rename answers a real ambiguity.** The sketch
said `videos/` while the rationale below says "the same screenshot recurs across
modules", and those are not the same population. Measured across the bundles:
`visual_assets[]` and `video_assets[]` both carry a `file` and a `sha256`, while
`embedded_media[]` carries `media_type`, `source_url` and `tracks` — **no file
and no hash**, because it is a reference to something on a remote host rather
than a file. By default a video *is* such a reference and only becomes a file
under `--video-assets`, while page images and Guidde step stills are always on
disk.

So the scope is **files that carry a `sha256`**, and a store holding page images
is not a `videos/` directory.

- **A corpus index** at the root: the full five-level outline, every module's
  synthetic id, title, type, duration (both, per P9-8), video kind, and the file
  that holds it. This is the entry point; **it must be readable alone and still
  be useful.** **Built** — `journey/index.py`, from what capture recorded rather
  than by re-walking. It also carries **every refusal with its reason**, which
  the bullet below turns out to require and this one did not say: an index
  listing only what was captured describes a journey with no assessments in it.
- **Every chunk carries its full path** — journey, section, track, group,
  module — so a retrieved fragment knows where it sits without a second lookup.
  **Built**, as an optional `meta.journey` added by a post-pass over each
  module's bundle. Not in the shared chunk writer: that serves every capture,
  and a page captured on its own belongs to no journey. `meta.sha256` digests
  the chunk's *text*, so adding to `meta` leaves it correct — folding the path
  into the text would be defensible for retrieval and would require recomputing
  every digest.
- **Cross-references resolve.** A module that references another module by title
  carries that module's synthetic id. **Built**, with the ambiguity stated
  rather than resolved away: titles are ambiguous by construction — two groups
  can hold modules with the same title, which is why `click_module_js` is
  panel-scoped — so an unambiguous title resolves to its `node_id` and an
  ambiguous one keeps **every** candidate and is marked `ambiguous`. A title
  equal to the referring module's own is never resolved either: `content.md`
  opens with that heading, so the occurrence cannot be told apart from it.
  Silently picking one candidate would be a wrong answer in the shape of a
  right one, and nothing downstream could tell.
- **Absence is stated, never implied.** The established rule holds: an
  untranscribed video says so, in the README, the manifest and `content.md`. A
  **missing module is an error**, not a silent gap.
- **Media dedupe by content hash across the corpus** — the same screenshot
  recurs across modules; one copy, many references, every referencing module
  recorded. **Built** — `journey/media.py`, consuming the `sha256` each record
  already carries rather than recomputing it.

  **The module bundles are not rewritten, and that resolves a second conflict
  in this document.** *How the output has to be organized* above says each
  module keeps its own bundle "so one module can be handed to an agent alone",
  and that cannot be true of the same bytes as "one copy" across the corpus.
  The requirement's own rationale is provenance — *every referencing module
  recorded* — so `media/` holds one copy per distinct hash **beside** the
  bundles rather than out of them, and a module stays hand-off-able. A
  capability with a stated reason is not worth breaking to satisfy a storage
  reading of an ambiguous phrase.

### Safety, unchanged

Completion is a **precondition**, per *Progress safety, restated with evidence*
above: against a 100% journey the walker is read-only and progress safety
reduces to a recorded before/after assertion, measured stable at 100% across
every expansion, track open, module open and return.

**Never open, start, or submit an Assessment.** P12-2 found two in the first
group opened, so the rule is live on every run, and the capture pass enforces it
as an allowlist rather than a denylist.

`--dry-run` is enumerate-only: walk, emit the outline, open nothing. Under the
two-pass contract it is also the thing the capture is checked against, so it is
not an optional preview.

The five binding rules stay where they are — *The walker's safety rules* above —
and are unchanged but for the locked-content scope, which the completion
precondition replaces.

### Scale

The largest track alone has nine numbered groups (P9-5). Eight
tracks at that shape is low hundreds of modules, so resume-from-partial and
enumerate-only are requirements, not conveniences.

## P13 — the clamp check mostly exists, and P9-7 pointed away from it (2026-08-24)

Read out of the checkout, not recalled. P9-7 and P12-6 between them leave one
piece of work; this is what that work actually is, because P9-7 mis-stated it.

### P13-1 — `pdf-text.txt` already extracts the render — **CORRECTS P9-7**

P9-7 named the missing check as "extract the text back out of the published PDF
and assert it against `content.md`", and called it a check that "does not
exist". Most of it does. Taken at face value that sentence sends someone to
write a second PDF text extractor beside the one the golden harness has used
since the corpus existed.

What is already there:

- `tools/golden/harness.py:417` — `_pdf_page_text()` opens the produced PDF with
  `pypdf.PdfReader` and calls `page.extract_text()` per page.
- `tools/golden/harness.py:470` — that output is normalized and recorded as the
  `pdf-text.txt` artifact of every case.
- `tools/golden/corpus.py:20` — `pdf-text.txt` is a member of
  `WEB_OCR_ARTIFACTS`, which is the default `sentinel_artifacts`. A case's
  sentinels are therefore *already* asserted against the text of the render.
- `tools/golden/harness.py:1016` — `sentinel_failures()` is that assertion.

So the relation P9-7 asks for is a **declaration, not a subsystem**: a case whose
sentinel strings sit inside the clamped region, with `sentinel_artifacts` naming
both `bundle/content.md` and `pdf-text.txt`, fails exactly when the DOM keeps a
paragraph that the PDF drops. No new harness code is required.

### P13-2 — what is genuinely missing is a fixture that clamps

`line-clamp` appears **nowhere** in `tests/`, `tools/` or `src/`.
`tests/fixtures/lms_page.html` is 79 lines and contains no `max-height`. No case
in the corpus exercises the path at all.

This is the sharper form of P9-7's warning, and it cuts the other way from how
that entry reads. A golden proves **stability, not correctness**. Had a clamped
page ever been recorded, the truncation would have been captured as the expected
output and the diff would have stayed clean over it indefinitely. The corpus
cannot catch a defect that no fixture reproduces — so the fixture is the
load-bearing part of this work, not the assertion.

### P13-3 — the fix is a print-CSS rule that does not exist

`BASE_PRINT_CSS` in `src/webshot/capture/prepare.py` sets `overflow: visible` in
two places, both scoped to `[data-webshot-*]` elements it marked itself. Nothing
in that file touches `-webkit-line-clamp` or a collapsed `max-height`.

Per P12-6 the print CSS is the *only* mechanism here — no `Read more`,
`Show more` or `See more` control exists on either track view — so this rule is
the whole of the fix rather than a fallback for residuals.

One shape to avoid: a blanket `* { max-height: none !important }`. It would
un-collapse blocks the page deliberately keeps collapsed, and it would move the
recorded output of every existing case, turning a one-case change into a corpus
re-record. The file's established idiom is a marked pass — `data-webshot-hidden`,
`data-webshot-root`, `data-webshot-ancestor`, `data-webshot-ocr-text` — so the
clamp release belongs behind a marker set by measuring `getComputedStyle` on the
page, with the CSS targeting that marker and nothing else.

### P13-4 — sequencing, and why the check comes first

The clamp fix and the fixture that proves it are one unit of work and belong in
one pull request, ahead of the capture stage. The capture stage produces PDFs by
the hundred (P9-5 scale); a truncation defect discovered after that runs is
discovered across a whole corpus at once. The check is cheap now and is the
safety net the capture stage will be landing on.

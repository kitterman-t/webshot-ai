# ADR-0007 — pypdf (not pikepdf) for associated-file embedding

**Status:** accepted (rationale corrected 2026-08-19 after red-team review; consequences extended in Phase 1) · **Date:** 2026-08-19

## Context
The AI-PDF design embeds manifest/content/chunks inside the PDF as associated
files, which requires the `AFRelationship` key and the document-level `/AF`
array. An earlier draft of this ADR justified rejecting pikepdf by citing its
open issue #463 — that issue is in fact resolved (pikepdf's attachment API now
supports the relationship property), and pikepdf enters the dependency tree
transitively via OCRmyPDF regardless, so both of the draft's factual claims
were wrong.

## Decision
Use pypdf, for the reasons that were true all along:
1. **It is already proven in this codebase** — v2's
   `protected_viewer.py` embeds associated files with pypdf today, including
   writing the catalog `/AF` array.
2. It is already a core dependency (validation), so embedding adds no new
   *direct* dependency; pikepdf remains transitive-only.
3. Spike-verified (docs/09 S2): `EmbeddedFile` +
   `associated_file_relationship`/`subtype`/`description` round-trip, tags
   survive the single-document rewrite, values must be wrapped as
   `NameObject`/`TextStringObject`.

## Consequences
One direct PDF library. The pypdf version floor must be ≥ 6 (the
`EmbeddedFile` API; enforced in Phase 0.1). If pikepdf's strengths (QPDF
repair, linearization) are ever needed, the embed call sits behind one
function.

**Amended after Phase 1 (docs/09 P1-3).** The custom glue is larger than the
"~5 lines" S2 estimated, because pypdf's attachments are not PDF/A-3 valid as
written. All three of these are on WebShot:

1. the document-level catalog `/AF` array (S2's finding);
2. **`/UF` on the file specification** — ISO 19005-3 clause 6.8 requires `/F`
   *and* `/UF`; `add_attachment` writes only `/F`. Set via
   `EmbeddedFile.alternative_name`;
3. **an unescaped MIME `/Subtype`** — hand it `NameObject("/application/json")`
   and let pypdf encode the slash. v2 pre-escaped it as
   `"/application#2Fjson"`, which pypdf escaped again, and veraPDF then read
   back a subtype that is not a MIME type.

None of the three is detectable by reading the file back with pypdf, which
decodes its own output. They were found by validating against veraPDF, which is
the argument for ADR-0010's gate existing at all.

**One more sharp edge, in the merge rather than the embed.** `PdfWriter()` +
`append(a)` + `append(b)` silently drops the catalog-level keys of both
documents, including the PDF/A identification XMP and `/OutputIntents`. The
composition therefore clones its writer from the converted file
(`PdfWriter(clone_from=…)`) and appends only the appendix.

"""A `chunks.jsonl` reader splits where the writer split: at `\\n`, nowhere else.

Every chunk writer spells a record `json.dumps(record, ensure_ascii=False)`.
That escapes the ASCII control characters, but it writes U+0085, U+2028 and
U+2029 raw, and `str.splitlines()` breaks a line at all three. A reader that
split with it cut a record in two and handed `json.loads` half of it.

The fixture is the `chunks.jsonl` a real capture wrote for a page of three
`<pre>` blocks, each holding one of the three characters: three records, six
`splitlines()` lines (docs/09 P10-28).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "chunks_line_separators.jsonl"
SEPARATORS = ("\u0085", "\u2028", "\u2029")


def _fixture_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _records(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.split("\n") if line.strip()]


def _texts() -> list[str]:
    return [record["text"] for record in _records(_fixture_text())]


def _bundle(tmp_path: Path, chunks: str | None = None) -> Path:
    bundle = tmp_path / "capture.ai"
    bundle.mkdir(parents=True)
    if chunks is None:
        shutil.copyfile(FIXTURE, bundle / "chunks.jsonl")
    else:
        (bundle / "chunks.jsonl").write_text(chunks, encoding="utf-8", newline="\n")
    return bundle


def _run(bundle: Path) -> Any:
    from tools.golden.corpus import CORPUS
    from tools.golden.harness import RunArtifacts

    return RunArtifacts(
        case=CORPUS[0],
        workdir=bundle.parent,
        pdf=bundle.parent / "capture.pdf",
        bundle=bundle,
        report=bundle.parent / "report.json",
        exit_code=0,
        stderr="",
    )


def test_the_fixture_is_the_writers_own_spelling(tmp_path: Path) -> None:
    """Every test below is vacuous unless the fixture still holds the defect.

    An editor or a tool that escaped the three characters would leave a file
    `splitlines()` reads correctly, and each reader test would pass against
    the old code too. Rewriting the parsed records through `write_chunks`
    also ties the fixture to what the writer produces today.
    """
    from webshot.extract.exports import write_chunks

    data = FIXTURE.read_bytes()
    text = data.decode("utf-8")
    for character in SEPARATORS:
        assert text.count(character) == 1, f"U+{ord(character):04X} is not raw"
    records = _records(text)
    assert len(records) == 3
    assert len(text.splitlines()) == 6, "the fixture no longer splits wrongly"

    write_chunks(records, tmp_path)
    assert (tmp_path / "chunks.jsonl").read_bytes() == data


def _journey() -> tuple[Any, Any]:
    from webshot.journey.model import JourneyNode, JourneyOutline

    module = JourneyNode("m1", "module", 1, "Separators")
    group = JourneyNode("g1", "group", 1, "Group", (module,))
    track = JourneyNode("t1", "track", 1, "Track", (group,))
    section = JourneyNode("s1", "section", 1, "Section", (track,))
    outline = JourneyOutline(
        journey_id="j1",
        source="https://lms.invalid/journey",
        title="Journey",
        progress_before="",
        progress_after="",
        root=JourneyNode("j1", "journey", 1, "Journey", (section,)),
    )
    return outline, module


def test_stamping_a_journey_path_keeps_every_record(tmp_path: Path) -> None:
    from webshot.journey.capture import stamp_chunk_paths

    outline, module = _journey()
    bundle = _bundle(tmp_path)

    assert stamp_chunk_paths(bundle, outline, module) == 3

    stamped = _records((bundle / "chunks.jsonl").read_text(encoding="utf-8"))
    assert [record["text"] for record in stamped] == _texts()
    for record in stamped:
        assert record["meta"]["journey"]["node_id"] == "m1"
        digest = hashlib.sha256(record["text"].encode("utf-8")).hexdigest()
        assert record["meta"]["sha256"] == digest, record["id"]


@pytest.mark.parametrize(
    "bad",
    ["{oops", "null", "[1, 2]", '{"id": "ch_000004", "meta": null}'],
    ids=["not-json", "not-an-object", "an-array", "meta-not-an-object"],
)
def test_a_line_that_is_not_a_chunk_record_leaves_the_chunk_file_whole(
    tmp_path: Path, bad: str
) -> None:
    """The file was opened for writing first, so the failure emptied it.

    Measured against the old reader on the fixture: `JSONDecodeError`, and
    0 of the file's 786 bytes left. The journey stops there, and the module
    is not in the resume file, so a rerun meets the same record again. A line
    that parses but cannot be stamped failed the same way one step later, so
    stamping happens before the write as well.
    """
    from webshot.journey.capture import stamp_chunk_paths

    outline, module = _journey()
    bundle = _bundle(tmp_path, _fixture_text() + bad + "\n")
    before = (bundle / "chunks.jsonl").read_bytes()

    with pytest.raises(ValueError):
        stamp_chunk_paths(bundle, outline, module)
    assert (bundle / "chunks.jsonl").read_bytes() == before


def test_the_harness_asserts_every_chunk_digest(tmp_path: Path) -> None:
    from tools.golden.harness import chunk_digest_failures

    assert chunk_digest_failures(_run(_bundle(tmp_path / "clean"))) == []

    # The control: a wrong digest on the U+2028 record is named, so the
    # check read that record rather than skipping it.
    wrong = _records(_fixture_text())
    wrong[1]["meta"]["sha256"] = "0" * 64
    planted = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in wrong)
    failures = chunk_digest_failures(_run(_bundle(tmp_path / "planted", planted)))
    assert len(failures) == 1, failures
    assert failures[0].startswith("chunk ch_000002: sha256 " + "0" * 64)


def test_the_harness_resolves_every_chunk_pointer(tmp_path: Path) -> None:
    from tools.golden.harness import chunk_pointer_failures

    bundle = _bundle(tmp_path)
    (bundle / "content.json").write_text(json.dumps({"texts": [{}] * 4}))
    assert chunk_pointer_failures(_run(bundle)) == []

    (bundle / "content.json").write_text(json.dumps({"texts": [{}] * 3}))
    assert chunk_pointer_failures(_run(bundle)) == [
        "chunk ch_000003: doc_items '#/texts/3' does not resolve in content.json"
    ]


def test_the_golden_snapshot_masks_every_digest_and_keeps_the_text() -> None:
    """A split record was kept as it stood: digest unmasked, U+2028 now `\\n`."""
    from tools.golden.harness import MASKED_CHUNK_DIGEST, _normalize_chunks

    text = _fixture_text()
    normalized = _normalize_chunks(text)

    records = _records(normalized)
    assert [record["text"] for record in records] == _texts()
    assert [r["meta"]["sha256"] for r in records] == [MASKED_CHUNK_DIGEST] * 3
    for character in SEPARATORS:
        assert normalized.count(character) == 1
    assert normalized.endswith("}\n") and not normalized.endswith("\n\n")


def test_a_changed_golden_line_is_printed_whole() -> None:
    """The report named a changed file and showed no line of it."""
    from tools.golden.harness import compare

    [problem] = compare(
        {"bundle/content.txt": "one\u2028two\n"},
        {"bundle/content.txt": "one\u2029two\n"},
    )
    assert "\n-one\u2028two\n" in problem
    assert "\n+one\u2029two" in problem


def test_parity_reads_and_mutates_whole_records() -> None:
    from tools.parity.mutations import (
        MUTATIONS,
        _break_chunk_provenance,
        _drop_first_chunk,
        _v3_scrub_refs,
    )
    from tools.parity.views import _chunk_lines

    files = {"bundle/chunks.jsonl": _fixture_text()}
    lines = _fixture_text().split("\n")
    texts = _texts()

    assert [chunk["text"] for chunk in _chunk_lines(files)] == texts
    assert [c["text"] for c in _chunk_lines(_drop_first_chunk(files))] == texts[1:]

    scrubbed = _chunk_lines(_v3_scrub_refs(files, {"#/texts/2"}))
    assert [chunk["id"] for chunk in scrubbed] == ["ch_000001", "ch_000003"]

    broken = _break_chunk_provenance(files)["bundle/chunks.jsonl"].split("\n")
    first = json.loads(broken[0])
    assert first["text"] == texts[0] and first["meta"]["doc_items"] == []
    assert broken[1:] == lines[1:], "only the first record may change"

    # One record holding a separator is one chunk, and dropping the first of
    # one chunk is not the defect this mutation plants.
    [drop_first] = [m for m in MUTATIONS if m.name == "drop-first-chunk"]
    assert not drop_first.applies({"bundle/chunks.jsonl": lines[0] + "\n"})
    assert drop_first.applies(files)


@pytest.mark.parametrize("character", SEPARATORS, ids=lambda c: f"U+{ord(c):04X}")
def test_every_separator_in_the_fixture_is_inside_a_record(character: str) -> None:
    """Each character sits in a chunk's text, which is where a page puts it."""
    assert sum(character in text for text in _texts()) == 1

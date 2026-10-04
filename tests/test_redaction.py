"""Password redaction (docs/04-spec.md §6.2), proven on the recorded corpus.

Two assertions, and both are load-bearing (docs/10-traceability-matrix.md §6.2,
red-team finding C6): the password value is ABSENT from every published
artifact, and the redacted form-field record is PRESENT.  Absence alone would
pass vacuously if form extraction silently vanished — which is exactly the kind
of regression Phase 2's extraction swap could introduce.

These tests read the recorded goldens, so they need no browser and they fail
the moment a re-record captures a leak.  The one deliberate exemption is the
`source/` copy: it is the user's own local input file preserved byte-for-byte
(docs/02-architecture.md §Bundle table — "never for URLs"), so the fixture
password it already contained is not a capture leak.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, cast

from webshot.capture.snapshot import _redact_password_values

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_BUNDLE = REPO_ROOT / "tests" / "golden" / "form-fields" / "bundle"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "form_page.html"

#: The value typed into the fixture's password field, defined once here and in
#: the fixture itself.  It exists to be leaked; the tests prove it never is.
FIXTURE_PASSWORD = "XyzzY-9-FixturePass!"
#: The *second* fixture's second password, which contains the first as a
#: prefix.  The suffix is what leaked: redaction replaced the shorter value
#: first, so this one no longer matched itself and `[REDACTED PASSWORD]-rotated`
#: was published (docs/09 P8-8).
FIXTURE_ROTATED_PASSWORD = "XyzzY-9-FixturePass!-rotated"
REDACTION = "[REDACTED PASSWORD]"
PASSWORD_FIELD = "account_password"

#: The overlap lives on its own page, not as two more fields on `form_page.html`:
#: that fixture has a frozen v2 parity baseline which cannot be re-recorded,
#: and changing the page would leave the parity harness comparing a v3 capture
#: against a v2 capture of a different document.
OVERLAP_BUNDLE = (
    REPO_ROOT / "tests" / "golden" / "form-overlapping-passwords" / "bundle"
)
OVERLAP_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "form_overlapping_passwords.html"


def published_artifacts() -> list[Path]:
    return [
        path
        for path in sorted(GOLDEN_BUNDLE.rglob("*"))
        if path.is_file() and "source" not in path.relative_to(GOLDEN_BUNDLE).parts
    ]


def test_the_fixture_actually_contains_the_password() -> None:
    """The absence assertions below are meaningless if the input lost the value."""
    assert FIXTURE_PASSWORD in FIXTURE.read_text("utf-8")


def test_password_value_is_absent_from_every_published_artifact() -> None:
    assert published_artifacts(), "form-fields golden not recorded"
    leaks = [
        path.relative_to(GOLDEN_BUNDLE).as_posix()
        for path in published_artifacts()
        if FIXTURE_PASSWORD in path.read_text("utf-8", errors="ignore")
    ]
    assert not leaks, f"password value leaked into: {leaks}"


def _form_records(content: dict[str, Any]) -> list[dict[str, Any]]:
    """Form-field records in either bundle format.

    v2 (schema_version 1.0) carries them as `form-value` blocks in
    content.json; format 3 carries them in assets.json (docs/02 fidelity
    mapping).  The test accepts either so the swap's re-record cannot silently
    drop the record — the record itself must exist somewhere.
    """
    if "blocks" in content:
        return [b for b in content["blocks"] if b.get("type") == "form-value"]
    assets_path = GOLDEN_BUNDLE / "assets.json"
    if assets_path.is_file():
        assets = json.loads(assets_path.read_text("utf-8"))
        return list(assets.get("form_fields", []))
    return []


def test_redacted_password_record_is_present() -> None:
    """§6.2 asserts presence, not just absence (the vacuous-pass trap)."""
    content = json.loads((GOLDEN_BUNDLE / "content.json").read_text("utf-8"))
    records = _form_records(content)
    assert records, "no form-field records at all — form extraction vanished"
    password_records = [r for r in records if r.get("name") == PASSWORD_FIELD]
    assert password_records, f"no record for the {PASSWORD_FIELD!r} field"
    assert all(r.get("value") == REDACTION for r in password_records), (
        "the password field's record must carry the redaction marker, "
        f"got: {[r.get('value') for r in password_records]}"
    )


def test_accessibility_snapshot_reports_the_field_redacted() -> None:
    """The aria snapshot reads the live DOM, so it needs its own redaction
    (this leaked in v2.2 until the form-fields fixture caught it — Phase 2)."""
    snapshot = (GOLDEN_BUNDLE / "accessibility.yaml").read_text("utf-8")
    assert "Account password" in snapshot, "password textbox missing from snapshot"
    assert REDACTION in snapshot


def test_the_overlap_fixture_holds_two_overlapping_passwords() -> None:
    """The overlap is the point: one is a prefix of the other."""
    body = OVERLAP_FIXTURE.read_text("utf-8")
    assert FIXTURE_PASSWORD in body
    assert FIXTURE_ROTATED_PASSWORD in body
    assert FIXTURE_ROTATED_PASSWORD.startswith(FIXTURE_PASSWORD)


def _overlap_artifacts() -> list[Path]:
    return [
        path
        for path in sorted(OVERLAP_BUNDLE.rglob("*"))
        if path.is_file() and "source" not in path.relative_to(OVERLAP_BUNDLE).parts
    ]


def test_neither_password_nor_a_fragment_of_one_reaches_an_artifact() -> None:
    """Not just the whole value — the tail a prefix-match would leave behind.

    `[REDACTED PASSWORD]-rotated` published the distinguishing half of a
    password while looking, to a reader and to an absence assertion checking
    only whole values, exactly like a successful redaction.
    """
    assert _overlap_artifacts(), "form-overlapping-passwords golden not recorded"
    orphan = REDACTION + FIXTURE_ROTATED_PASSWORD[len(FIXTURE_PASSWORD) :]
    leaks = [
        path.relative_to(OVERLAP_BUNDLE).as_posix()
        for path in _overlap_artifacts()
        for needle in (FIXTURE_PASSWORD, FIXTURE_ROTATED_PASSWORD, orphan)
        if needle in path.read_text("utf-8", errors="ignore")
    ]
    assert not leaks, f"password material published in: {sorted(set(leaks))}"


def test_the_overlap_capture_still_recorded_its_ordinary_form_state() -> None:
    """Absence is only a measurement if the capture worked (red-team C6).

    A bundle that recorded nothing would pass every assertion above.
    """
    snapshot = (OVERLAP_BUNDLE / "accessibility.yaml").read_text("utf-8")
    # The account field's *live* value — the aria snapshot reads the live DOM,
    # which is the surface P8-72 brought `content.html` into line with.
    assert "live@example.com" in snapshot
    assert snapshot.count(REDACTION) == 2


def test_overlapping_values_are_redacted_longest_first() -> None:
    """The unit-level proof, with no browser: order decides correctness."""

    class _StubFrame:
        async def evaluate(self, _script: str) -> list[str]:
            # Document order, shortest first — the order that broke it.
            return [FIXTURE_PASSWORD, FIXTURE_ROTATED_PASSWORD]

    class _StubPage:
        def __init__(self) -> None:
            self.frames = [_StubFrame()]

    text = f"- textbox: {FIXTURE_PASSWORD}\n- textbox: {FIXTURE_ROTATED_PASSWORD}\n"
    scrubbed = asyncio.run(_redact_password_values(cast(Any, _StubPage()), text))
    assert FIXTURE_PASSWORD not in scrubbed
    assert FIXTURE_ROTATED_PASSWORD not in scrubbed
    assert "-rotated" not in scrubbed
    assert scrubbed.count(REDACTION) == 2


# --------------------------------------------------------------------------- #
# Live control state (docs/09 P8-72)
# --------------------------------------------------------------------------- #


def test_the_snapshot_records_what_the_controls_actually_held() -> None:
    """`innerHTML` serializes content attributes, not IDL properties.

    So the inert clone the snapshot is built from received the page-authored
    defaults, while the in-browser block extraction read the live DOM — and
    `content.html` and the `form-value` records described the same page
    differently. Reading `.value` off the *clone* to "fix" it read the same
    default straight back, which is why the rewrite looked correct and was a
    no-op (docs/09 P8-72).
    """
    html = (OVERLAP_BUNDLE / "content.html").read_text("utf-8")
    assert 'value="live@example.com"' in html
    assert "avery@example.com" not in html, "the default outlived the live value"
    assert "live recovery note" in html
    assert "default recovery note" not in html
    # Checkedness and select choice were never synchronized at all.
    assert re.search(r'<input[^>]*id="notify"[^>]*checked', html)


def test_the_two_form_surfaces_agree() -> None:
    """The point of the fix: one page, one answer."""
    records = {
        record["name"]: record["value"]
        for record in json.loads((OVERLAP_BUNDLE / "assets.json").read_text("utf-8"))[
            "form_fields"
        ]
    }
    html = (OVERLAP_BUNDLE / "content.html").read_text("utf-8")
    assert records["account"] == "live@example.com"
    assert f'value="{records["account"]}"' in html
    assert records["recovery_note"] == "live recovery note"
    assert records["recovery_note"] in html
    assert records["notify"] == "true"
    # The select's live choice reaches the form-value record. It cannot reach
    # content.html because nh3 strips `<option>` from the snapshot regardless
    # — pre-existing, unrelated to this fix, recorded as P8-75.
    assert records["display_mode"] == "Dense"


def test_the_live_values_are_not_merely_the_defaults() -> None:
    """Otherwise the assertions above would pass on the unfixed code."""
    fixture = OVERLAP_FIXTURE.read_text("utf-8")
    assert 'value="avery@example.com"' in fixture
    assert ">default recovery note<" in fixture
    assert 'id="notify" name="notify">' in fixture, "must be unchecked by default"


def test_a_removed_control_does_not_shift_the_others_values() -> None:
    """Live controls are matched to cloned ones by an explicit mark, not by
    position, and the fixture's hidden inputs are what make that necessary.

    The clone filter removes hidden and excluded elements, so one dropped
    input shifts every later index and controls receive each other's values.
    Measured with a positional implementation against this fixture: `notify`
    lost its `checked` state, reading the hidden CSRF input's instead — a
    worse fidelity bug than P8-72 itself.
    """
    fixture = OVERLAP_FIXTURE.read_text("utf-8")
    assert 'type="hidden"' in fixture, "the shifting control must still be here"
    assert "display:none" in fixture

    html = (OVERLAP_BUNDLE / "content.html").read_text("utf-8")
    # Both are dropped from the snapshot, which is what does the shifting.
    assert "csrf_token" not in html
    assert "offscreen-default" not in html
    # And every surviving control still holds its own value.
    assert re.search(r'<input[^>]*id="notify"[^>]*checked', html)
    assert 'id="confirm-account"' in html and 'value="live@example.com"' in html
    assert "hidden-default" not in html

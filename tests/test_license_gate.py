"""The license gate's classification rules.

The gate itself runs in CI against the installed tree; these tests pin the
judgment calls it makes, especially the ones where a substring match would give
the wrong answer (LGPL is permitted, AGPL is not, and both contain "GPL").
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.licenses.check import acceptable, alternatives, is_denied

PERMITTED = [
    "MIT",
    # SPDX disjunctions: the licensor offers terms and the recipient elects.
    # `tld`, which trafilatura pulls in, is the real case (docs/09 P3-5).
    "MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later",
    "Apache-2.0 OR BSD-3-Clause",
    "GPL-2.0-only OR MIT",
    "MIT License",
    "BSD-3-Clause",
    "Apache Software License",
    "Apache-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)",
    "GNU Lesser General Public License v3 (LGPLv3)",
    "GNU Library or Lesser General Public License (LGPL)",
    "LGPL-3.0-only",
    "PSF-2.0",
    "ISC",
    "The Unlicense (Unlicense)",
]

DENIED = [
    "GNU General Public License v2 (GPLv2)",
    # Every alternative refused means there is no acceptable choice...
    "GPL-3.0-only OR AGPL-3.0-only",
    # ...and `AND` is not a choice at all, so one refused part refuses it.
    "MIT AND GPL-3.0-only",
    "GNU General Public License v3 or later (GPLv3+)",
    "GPL-3.0-only",
    "GNU Affero General Public License v3",
    "AGPL-3.0",
    "Server Side Public License",
    "SSPL-1.0",
]


@pytest.mark.parametrize("license_text", PERMITTED)
def test_permissive_and_weak_copyleft_licenses_pass(license_text: str) -> None:
    assert not is_denied(license_text)


@pytest.mark.parametrize("license_text", DENIED)
def test_strong_copyleft_and_sspl_are_denied(license_text: str) -> None:
    assert is_denied(license_text)


# --------------------------------------------------------------------------- #
# The disjunction rule, and the thing it must not do
# --------------------------------------------------------------------------- #


def test_a_disjunction_reports_every_acceptable_choice() -> None:
    assert acceptable("MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later") == [
        "MPL-1.1",
        "LGPL-2.1-or-later",
    ]


def test_a_license_name_containing_a_lowercase_or_is_not_split() -> None:
    """SPDX's operator is uppercase; a license *name* may contain the word."""
    name = "GNU Library or Lesser General Public License (LGPL)"
    assert alternatives(name) == [name]


def test_a_full_license_body_is_never_split_into_alternatives() -> None:
    """The dangerous case: nearly every license body says "EXPRESS OR IMPLIED".

    Splitting on that would hand `is_denied` a fragment naming no license,
    which would read as permitted — so a package shipping the GPL's *text* as
    its metadata would pass. Long or multi-line values stay one blob.
    """
    body = (
        "GNU GENERAL PUBLIC LICENSE Version 3\n\nTHIS PROGRAM IS DISTRIBUTED IN "
        "THE HOPE THAT IT WILL BE USEFUL, BUT WITHOUT ANY WARRANTY; without even "
        "the implied warranty of MERCHANTABILITY, EXPRESS OR IMPLIED, and so on "
        "at the considerable length these documents are always written at."
    )
    assert alternatives(body) == [body]
    assert is_denied(body)


def test_a_package_declaring_nothing_is_unknown_rather_than_denied() -> None:
    """A warning, never a denial: absence of a license is absence of evidence.

    `is_denied` means "no acceptable alternative was offered", and an empty
    field must not be able to say that — the gate would fail CI with a message
    asserting a GPL dependency it knows nothing about.
    """
    for nothing in ("", "   ", "UNKNOWN"):
        assert alternatives(nothing) != []
        assert not is_denied(nothing)


def test_a_permissive_license_body_still_passes() -> None:
    body = (
        "MIT License\n\nPermission is hereby granted, free of charge, to any "
        "person obtaining a copy of this software... THE SOFTWARE IS PROVIDED "
        '"AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.'
    )
    assert not is_denied(body)


def test_a_mandatory_gpl_term_is_not_escaped_by_an_inner_disjunction() -> None:
    """`AND` binds every operand, and it binds tighter than `OR`.

    `GPL-3.0-only AND (MIT OR Apache-2.0)` offers a choice *inside* a term the
    recipient must take anyway.  Splitting the string at every ` OR ` produced
    the fragment `Apache-2.0)`, which names nothing denied, so the gate passed
    a package whose GPL obligation is not optional — spec §6 invariant 9 read
    backwards (docs/09 P8-7).
    """
    for expression in (
        "GPL-3.0-only AND (MIT OR Apache-2.0)",
        "(MIT OR Apache-2.0) AND GPL-3.0-only",
        "MIT AND (BSD-3-Clause OR AGPL-3.0-only AND SSPL-1.0)",
    ):
        assert is_denied(expression), expression
        assert acceptable(expression) == []


def test_a_conjunction_of_permitted_terms_is_still_permitted() -> None:
    """The fix must not deny what the policy allows.

    An `AND` of acceptable licenses is one acceptable alternative, reported
    whole: the recipient takes every side of it and every side is permitted.
    """
    assert not is_denied("MIT AND Apache-2.0")
    assert acceptable("MIT AND Apache-2.0") == ["MIT AND Apache-2.0"]
    grouped = "(MIT OR Apache-2.0) AND (BSD-3-Clause OR ISC)"
    assert not is_denied(grouped)


def test_a_denied_conjunction_beside_a_permitted_choice_is_permitted() -> None:
    """`OR` still elects: one clean alternative is enough (docs/09 P3-5)."""
    expression = "Apache-2.0 OR (GPL-2.0-only AND Classpath-exception-2.0)"
    assert not is_denied(expression)
    assert acceptable(expression) == ["Apache-2.0"]

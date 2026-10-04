#!/usr/bin/env python3
"""Fail if the installed dependency tree contains a copyleft license we cannot use.

WebShot's license policy (docs/adr/0006-license-policy.md, docs/04-spec.md §6.9):
permissive and weak-copyleft licenses only — MIT, BSD, Apache-2.0, MPL-2.0,
PSF, ISC, LGPL (dynamic use).  GPL, AGPL, and SSPL projects may be *invoked* as
external binaries the user installs (Ghostscript, veraPDF), but must never be
linked into the installed Python environment.

    uv run python tools/licenses/check.py            # gate the current env
    uv run python tools/licenses/check.py --summary  # + markdown table

The table is what docs/03-components.md's license summary is meant to be
regenerated from, rather than maintained by hand.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any

# "Lesser"/"Library" GPL is permitted, so it is removed before the denied tokens
# are searched for; "agpl" and "lgpl" both contain "gpl", which is why the
# permitted spellings have to go first.
PERMITTED_SPELLINGS = (
    "lesser general public license",
    "library general public license",
    "lgpl",
)
DENIED_TOKENS = (
    "general public license",
    "gpl",
    "affero",
    "sspl",
    "server side public license",
)
UNKNOWN = {"UNKNOWN", "", None}

#: Some packages put their whole license *text* in the metadata field rather
#: than an identifier (chonkie ships the MIT body).  Splitting a license body
#: on " OR " would be actively dangerous — "EXPRESS OR IMPLIED" appears in the
#: warranty disclaimer of nearly every license, and one of the resulting
#: fragments would name no license at all, so a GPL body could split into
#: something that looks permitted.  Only a short, single-line value is read as
#: an SPDX expression; anything longer stays one opaque blob, matched whole.
MAX_EXPRESSION_LENGTH = 200


def _alternative_is_denied(license_text: str) -> bool:
    text = license_text.lower()
    for permitted in PERMITTED_SPELLINGS:
        text = text.replace(permitted, "")
    return any(token in text for token in DENIED_TOKENS)


def _split_top_level(expression: str, operator: str) -> list[str] | None:
    """Split on `operator` at parenthesis depth zero, or `None` if it is absent.

    Depth matters: `GPL-3.0-only AND (MIT OR Apache-2.0)` splits on `AND` into
    two operands, and on `OR` into nothing at all — the `OR` inside the group
    belongs to the group, not to this level.

    Case matters too. SPDX operators are uppercase by specification, and
    matching only that form keeps a license *name* that contains the word
    ("GNU Library or Lesser General Public License") from being split apart.
    """
    parts: list[str] = []
    depth = 0
    start = 0
    index = 0
    token = f" {operator} "
    while index < len(expression):
        character = expression[index]
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        elif (
            depth == 0
            and character == " "
            and expression[index : index + len(token)] == token
        ):
            parts.append(expression[start:index])
            index += len(token)
            start = index
            continue
        index += 1
    if not parts:
        return None
    parts.append(expression[start:])
    return [part.strip() for part in parts if part.strip()]


def _unwrap(expression: str) -> str:
    """Drop one layer of parentheses that wraps the whole expression."""
    text = expression.strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        for position, character in enumerate(text):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0 and position < len(text) - 1:
                    return text  # the parens close before the end: not a wrapper
        text = text[1:-1].strip()
    return text


def alternatives(license_text: str) -> list[str]:
    """The licenses a package is offered under, as choices.

    An SPDX `OR` is the licensor offering terms; the recipient elects one.
    An `AND` is not a choice: every operand binds, so a conjunction is
    acceptable only if all of its operands are, and it contributes exactly one
    alternative to the level above it.

    **Precedence is why this is a parser and not a `split`.** `OR` binds looser
    than `AND` in SPDX, and parentheses override both. Splitting the string at
    every `OR` turned `GPL-3.0-only AND (MIT OR Apache-2.0)` into the fragments
    `GPL-3.0-only AND (MIT` and `Apache-2.0)` — the second of which names
    nothing denied, so the gate approved a package whose GPL term is mandatory
    (docs/09 P8-7). The rule the gate is enforcing is spec §6 invariant 9, and
    it is not a rule that can be enforced by a split.

    Never returns an empty list.  A package that declares nothing is unknown,
    which is a warning; "no acceptable alternative" is what `is_denied` means,
    and an empty field must not be able to say that.
    """
    if "\n" in license_text or len(license_text) > MAX_EXPRESSION_LENGTH:
        return [license_text]
    parts = [part for part in _alternatives(license_text) if part.strip()]
    return parts or [license_text]


def _alternatives(expression: str) -> list[str]:
    """One expression's acceptable-if-any operands, innermost first."""
    text = _unwrap(expression)
    disjuncts = _split_top_level(text, "OR")
    if disjuncts is not None:
        return [part for operand in disjuncts for part in _alternatives(operand)]
    conjuncts = _split_top_level(text, "AND")
    if conjuncts is not None:
        # One alternative, taken or refused whole. Refused if any operand is:
        # an `AND` obliges the recipient to every side of it, so a conjunction
        # containing a denied term offers no acceptable choice at all.
        if any(_conjunct_is_denied(operand) for operand in conjuncts):
            return []
        return [text]
    return [text]


def _conjunct_is_denied(operand: str) -> bool:
    """Whether one operand of an `AND` rules the whole conjunction out."""
    return not _alternatives(operand) or all(
        _alternative_is_denied(part) for part in _alternatives(operand)
    )


def acceptable(license_text: str) -> list[str]:
    """Which of the offered alternatives ADR-0006 lets WebShot take.

    All of them, not one: the ADR names the license families it accepts and
    does not rank them, so the gate reports the choice that exists rather than
    inventing a preference and reporting it as a decision.
    """
    return [
        part for part in alternatives(license_text) if not _alternative_is_denied(part)
    ]


def is_denied(license_text: str) -> bool:
    """True only when *every* offered alternative is one ADR-0006 refuses.

    docs/09 P3-5: `trafilatura` brings `tld`, offered as "MPL-1.1 OR
    GPL-2.0-only OR LGPL-2.1-or-later".  Reading that string as a whole made
    the gate refuse a package the policy actually permits, because WebShot may
    simply take the LGPL terms ADR-0006 names.  A dual-licensed package is
    denied when no choice is acceptable, not when one is.
    """
    return not acceptable(license_text)


def installed_packages() -> list[dict[str, Any]]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "piplicenses",
            "--format=json",
            "--with-urls",
            "--with-license-file",
            "--no-license-path",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(
            "pip-licenses failed; install the dev extra first.\n" + completed.stderr
        )
    packages: list[dict[str, Any]] = json.loads(completed.stdout)
    return sorted(packages, key=lambda package: package["Name"].lower())


def markdown_table(packages: list[dict[str, Any]]) -> str:
    lines = ["| Package | Version | License |", "|---|---|---|"]
    lines += [
        f"| {package['Name']} | {package['Version']} | {package['License']} |"
        for package in packages
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary", action="store_true", help="print the markdown license table"
    )
    parser.add_argument(
        "--strict-unknown",
        action="store_true",
        help="treat an unknown license as a failure instead of a warning",
    )
    arguments = parser.parse_args(argv)

    packages = installed_packages()
    denied: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    # A package offered under a choice of terms is compliant because of the
    # choice WebShot makes, so the choice is reported rather than left implied.
    elections: list[tuple[dict[str, Any], list[str]]] = []
    for package in packages:
        offered = alternatives(package["License"])
        choices = acceptable(package["License"])
        if not choices:
            denied.append(package)
        elif len(offered) > 1:
            elections.append((package, choices))
        if package["License"] in UNKNOWN:
            unknown.append(package)

    if arguments.summary:
        print(markdown_table(packages))
        print()

    for package in unknown:
        print(
            f"WARNING: {package['Name']} {package['Version']} declares no license; "
            "check it by hand before shipping.",
            file=sys.stderr,
        )
    for package, choices in elections:
        print(
            f"NOTE: {package['Name']} {package['Version']} is offered as "
            f"{package['License']}; WebShot may take {' or '.join(choices)} "
            "(docs/adr/0006)."
        )
    for package in denied:
        print(
            f"DENIED: {package['Name']} {package['Version']} is "
            f"{package['License']} — GPL/AGPL/SSPL code must stay an external "
            "binary, never a linked dependency (docs/adr/0006).",
            file=sys.stderr,
        )

    if denied or (unknown and arguments.strict_unknown):
        return 1
    print(f"license gate: {len(packages)} packages checked, none denied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

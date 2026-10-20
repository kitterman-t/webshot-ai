"""Every requirement in pyproject.toml names its package as PyPI normalizes it
(docs/09 P84-5).

Written `Markdown>=3.6,<4`, the requirement was one Dependabot's uv updater
could not rewrite: each day it set out to update markdown from 3.10.3 to 3.11,
found no change to make and recorded `dependency_file_content_not_changed`, so
no pull request was opened and markdown stayed behind for weeks. uv itself
reads the name either way, which is why nothing else noticed.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _requirements() -> dict[str, list[str]]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    tables = {"project.dependencies": project["dependencies"]}
    for group, specs in project.get("optional-dependencies", {}).items():
        tables[f"project.optional-dependencies.{group}"] = specs
    for group, specs in data.get("dependency-groups", {}).items():
        # An `{include-group = ...}` entry names a group, not a package.
        tables[f"dependency-groups.{group}"] = [s for s in specs if isinstance(s, str)]
    return tables


def _misnamed(specs: list[str]) -> list[tuple[str, str]]:
    names = [Requirement(spec).name for spec in specs]
    return [
        (name, canonicalize_name(name))
        for name in names
        if name != canonicalize_name(name)
    ]


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("Markdown>=3.6,<4", [("Markdown", "markdown")]),
        ("Pillow>=12.3,<13", [("Pillow", "pillow")]),
        ("ruamel.yaml>=0.18", [("ruamel.yaml", "ruamel-yaml")]),
        ("docling-slim[format-html]>=2.138.0", []),
        ("markdown>=3.6,<4", []),
    ],
)
def test_a_name_counts_as_written_only_in_normalized_form(
    spec: str, expected: list[tuple[str, str]]
) -> None:
    assert _misnamed([spec]) == expected


def test_every_requirement_is_written_as_pypi_normalizes_it() -> None:
    tables = _requirements()
    # A table that could not be found must not read as a clean one.
    assert tables["project.dependencies"], "pyproject.toml lists no dependencies"
    misnamed = {
        table: found for table, specs in tables.items() if (found := _misnamed(specs))
    }
    assert misnamed == {}, f"write each name in its normalized form: {misnamed}"

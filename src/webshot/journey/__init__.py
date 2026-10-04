"""Walking a Continu journey, whose contents have no addresses of their own.

Two passes. `enumerate_journey` opens expanders, reads the tree and opens no
module; `capture.capture_journey` then captures exactly the module set the
enumeration listed. docs/13 makes the enumeration the contract the capture is
checked against, so a contract has to be right before anything leans on it.
"""

from __future__ import annotations

from .model import (
    LEVELS,
    JourneyNode,
    JourneyOutline,
    count_failures,
    diff_outlines,
    synthetic_id,
)
from .walk import (
    DomContract,
    EnumerationFailure,
    enumerate_journey,
    open_module,
    return_to_track,
)

__all__ = [
    "LEVELS",
    "DomContract",
    "EnumerationFailure",
    "JourneyNode",
    "JourneyOutline",
    "count_failures",
    "diff_outlines",
    "enumerate_journey",
    "open_module",
    "return_to_track",
    "synthetic_id",
]

"""EXTRACT stage: the docling bridge and everything downstream of it."""

#: The distributions that make up the extraction stack — the single list both
#: `webshot doctor`'s Extraction check and the manifest's `tool_versions`
#: describe, so the two can never drift apart.
EXTRACTION_DISTRIBUTIONS = ("docling-slim", "docling-core", "chonkie", "nh3")

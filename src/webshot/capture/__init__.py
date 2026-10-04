"""CAPTURE stage: drive the browser and harvest what the page renders."""

#: The upstream behind `--auto-selector` and manifest metadata enrichment
#: (docs/05 task 3.4), reported by `webshot doctor` and recorded in every
#: bundle's `tool_versions`.
DISCOVERY_DISTRIBUTIONS = ("trafilatura",)

"""ACQUIRE stage: decide what a source is and make it renderable."""

#: The upstream that reads every non-Markdown local format (docs/05 task 3.1).
#: Named here for the same reason the extraction list is named in its own
#: package: `webshot doctor` and the manifest's `tool_versions` both describe
#: it, and a bundle has to say which version shaped its content.
ADAPTER_DISTRIBUTIONS = ("markitdown",)

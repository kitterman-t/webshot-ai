# Architecture Decision Records

One decision per file: the context it was made in, the decision itself, and the
consequences accepted with it. A record is not revised when a decision turns out
to need refinement — an addendum is added, so the reasoning at the time stays
readable.

| ADR | Decision |
|---|---|
| [0001](0001-oss-first-policy.md) | OSS-first: custom code needs a documented gap |
| [0002](0002-keep-playwright-capture-core.md) | Keep the Playwright capture core |
| [0003](0003-adopt-ocrmypdf.md) | Adopt OCRmyPDF for the searchable text layer |
| [0004](0004-adopt-docling-slim.md) | Adopt docling-slim for semantic extraction |
| [0005](0005-adopt-markitdown-adapters.md) | Adopt MarkItDown for local format adapters |
| [0006](0006-license-policy.md) | Permissive licenses only in the dependency tree |
| [0007](0007-pdf-embedding-pypdf.md) | Embed associated files with pypdf |
| [0008](0008-ocr-engine-abstraction.md) | Abstract the OCR engine; HierarchicalChunker + chonkie by default |
| [0009](0009-mcp-server.md) | Ship an MCP server as the agent interface |
| [0010](0010-validation-gates.md) | PDF/A on the protected path only; veraPDF as the gate |

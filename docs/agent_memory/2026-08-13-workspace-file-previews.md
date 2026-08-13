# Workspace file previews

- `WorkspaceFilePreview` must not place text, JSON, Markdown or code inside a generic iframe: browser-native viewers can render an unstyled white surface and do not provide a coherent local-workspace reading experience.
- The frontend now fetches the already-authorized same-origin workspace file endpoint for text previews, limits rendered content to 768 KB, formats valid JSON, renders Markdown through the existing sanitized `ReactMarkdown` pipeline, and displays source/text in a dark monospaced surface. It never executes file content.
- Images retain a contained native image preview and PDFs retain an iframe viewer. Unsupported document formats explicitly offer the existing local `Abrir` and download actions instead of claiming unsupported in-browser rendering.
- A unit-level `window.scrollTo` no-op belongs in the jsdom setup because Motion invokes the browser API while measuring transitions; this keeps test output meaningful without changing browser behavior.

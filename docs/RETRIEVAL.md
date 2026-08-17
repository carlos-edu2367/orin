# Local retrieval (semantic code search)

Orin indexes the project folder bound to a project and exposes two tools to the
agent: `search_code`, which finds code by meaning, and `project_map`, which
lists the files the project depends on most.

The regex tool `search_files` is unchanged. The two are complementary: use
`search_files` when you know the exact identifier, `search_code` when you do
not.

## Where the index lives

One SQLite file per project, at `<data>/retrieval/<workspace_id>.db`, separate
from `orin.db`. It is derived data — deleting the file is safe, and the next
search rebuilds it.

## Embeddings

| Value of `ORIN_RETRIEVAL_EMBEDDER` | Behaviour |
|---|---|
| `ollama` (default) | Calls a local Ollama instance. Nothing leaves the machine. |
| `remote` | Calls an OpenAI-shaped endpoint. **Indexed file content leaves the machine.** Requires `ORIN_RETRIEVAL_API_KEY`. |
| anything else, or a failure | Lexical mode: BM25 only. Results say so. |

For the default, install Ollama and pull the model:

```bash
ollama pull nomic-embed-text
```

Other variables: `ORIN_RETRIEVAL_MODEL`, `ORIN_RETRIEVAL_BASE_URL`,
`ORIN_RETRIEVAL_API_KEY`.

## What is never indexed

`.env*`, `*.pem`, `*.key`, `id_rsa*`, `*.pfx` and similar are excluded before
any content is read, along with `.git`, `node_modules`, `.venv`, `dist`,
`build`, `__pycache__`, lock files, non-UTF-8 files, and anything matched by the
project's root `.gitignore`.

## How the index stays current

Three triggers, all incremental: binding a folder starts a full scan; every
`write_file`, `edit_file` or `run_command` call queues its changed paths on a
background thread; and a `search_code` call refreshes the index first if it has
not been scanned in the last 60 seconds, to catch edits made outside Orin.

Indexing never blocks a tool call: a mutating tool queues its changed paths on
the background worker rather than waiting for them to be embedded.

## Limits

- 2 MB per file, 50,000 chunks per project.
- The `.gitignore` support is a subset: comments, negation, directory-only
  patterns and root anchoring. Nested `.gitignore` files and `**` are not
  interpreted.
- Chunking is heuristic — regular expressions for common definition syntax,
  falling back to an overlapping sliding window — not a real parse.

## Planned

- **tree-sitter** as a second `Chunker` implementation, giving exact
  syntax-boundary chunks in any language instead of the current heuristic
  patterns.
- **An embedding model bundled in the release** (e.g. bge-small via
  `onnxruntime`), as a fourth `EmbeddingPort` implementation, so semantic
  search works offline on the first run without installing Ollama.

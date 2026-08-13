# Visual-reading review fixes

- For a turn model that supports images but explicitly cannot call tools, `pre_read_attachments` must preserve the neutral image blocks returned by `view_file` in the current user message. Appending only the explanatory text leaves that model without the attachment.
- `VisionReader` creates a dedicated provider transport for each visual transcription. It must close that transport in `finally` and normalize transport failures to `VisionUnavailable`, so a failed enrichment does not leak a client or fail the enclosing turn unexpectedly.
- The downgrade for migration `0027_agent_skills` must remove every index on `skills` before dropping that table; dropping the table after the first index made the next `DROP INDEX` fail.
- The persistence schema contract test must list the durable project, workspace-root, tool-ledger, attachment, and vision-selection tables already declared in `schema.py`.

Verification: focused attachment/vision tests and `tests/unit/persistence/test_postgres_adapter.py tests/unit/persistence/test_postgres_schema.py` passed on 2026-08-13.

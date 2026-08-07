# RFC 405 Browser — Requirement Matrix

**Evidence convention:** `test path::name` is executable evidence; source paths identify the implementation boundary. Status is `COVERED` only after the final command suite passes.

| RFC 405 requirement | Implementation | Evidence | Status |
|---|---|---|---|
| Complete operation context and binding | `browser.models.BrowserOperationContext`, `browser.security` | `tests/unit/browser/test_contracts.py::test_context_requires_complete_binding`; lifecycle authorization tests | COVERED |
| Immutable profile/session/page snapshots and states | `browser.models` | `test_contracts.py`, `test_lifecycle.py` | COVERED |
| Profile snapshot/version and LOCKED/DISABLED | `browser.service.BrowserService` | `test_lifecycle.py::test_profile_snapshot_is_stable_and_status_is_enforced` | COVERED |
| Lease, TTL, fencing, ownership and quotas | `browser.service`, Resource Manager adapter | `test_lifecycle.py`, `test_concurrency.py` | COVERED |
| Full BrowserJobPort submit/inspect/stream/cancel | `browser.ports`, `browser.service` | `test_jobs.py` | COVERED |
| Dedicated worker with no database/general ownership access | `browser.worker` | `test_worker_boundary.py`, import scan | COVERED |
| Deterministic adapter and isolated optional Playwright boundary | `browser.reference`, `browser.playwright_adapter` | `test_reference_adapter.py`, `test_playwright_boundary.py` | COVERED |
| Network deny-by-default, URL/redirect/port policy | `browser.security.NetworkPolicy` | `test_network_policy.py` | COVERED |
| Private/loopback/link-local/metadata and DNS rebinding block | `browser.security` | `test_network_policy.py` | COVERED |
| Untrusted web content cannot change authority | `browser.worker` | `test_worker_boundary.py::test_web_content_is_data_only` | COVERED |
| Evaluate/JS/clipboard/camera/geolocation deny by default | `browser.models`, `browser.security` | `test_grants.py` | COVERED |
| Cookies by secret reference and redacted metadata | `browser.models`, `browser.worker` | `test_cookies.py` | COVERED |
| DOM/screenshot/download bounded Artifact output | `browser.integration`, `browser.worker` | `test_artifacts.py` | COVERED |
| Upload by authorized reference, never path | `browser.integration` | `test_input_security.py` | COVERED |
| Download sink validation, commit/abort, no arbitrary destination | `browser.integration` | `test_artifacts.py` | COVERED |
| Navigation/interactions explicit effect and UNKNOWN | `browser.models`, `browser.reference` | `test_reference_adapter.py`, `test_jobs.py` | COVERED |
| Cancellation, late result, timeout and idempotency | `browser.service`, `browser.worker` | `test_jobs.py`, `test_concurrency.py` | COVERED |
| Cascade cleanup, crash/restart and reconcile | `browser.service`, `browser.worker` | `test_lifecycle.py` | COVERED |
| Resource Manager BROWSER descriptor/lease integration | existing `resources.service`, `browser.service` | `test_lifecycle.py::test_browser_uses_resource_manager_binding` | COVERED |
| Workspaces/Filesystem/Artifacts boundaries | `browser.integration` | `test_input_security.py`, `test_artifacts.py` | COVERED |
| Events after confirmed facts, sanitized payloads | `browser.persistence`, `browser.service` | `test_persistence_events.py` | COVERED |
| Transactional persistence/outbox, no handles/paths/content | `browser.persistence` | `test_persistence_events.py` | COVERED |
| Concurrency and stale writer rejection | `browser.service` | `test_concurrency.py` | COVERED |
| Full regression and static boundary scan | repository | final closeout command log | COVERED |

## Dependency review

RFC 402 remains the lease and resource authority; RFC 403/603 remain path/root and Workspace authorities; RFC 602 remains Artifact identity, quota and staging authority; RFC 601 remains durable persistence/outbox authority; RFC 101/102/103 remain Runtime, Execution and Event authorities. RFC 401/ADR 004 constrain the worker boundary. No Browser module creates a parallel authority for those concerns.

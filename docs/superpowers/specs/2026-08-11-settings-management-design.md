# Settings and management design

## Intent

Keep chat as the primary surface while making advanced system and project management predictable, discoverable, and backed by real APIs.

## Information architecture

`/settings` is the only global management container. Its internal navigation contains General, Providers, OmniRoute, Memory, Skills, Agents, Workspace, and Advanced. Existing Providers and Skills views are rendered through this container rather than reimplemented. Direct legacy routes redirect to their corresponding Settings destination.

Project-specific concerns remain outside global Settings under `/projects/:projectId/*`. A chat belonging to a project exposes a quiet contextual menu linking only to that project, its memory, and its workspace. Global Settings never mixes records belonging to distinct projects.

## Memory

The API exposes paginated user, project, and agent memory queries. Every query is authorized and scoped before searching; filters are passed to storage rather than loading an unbounded list into the browser. Details, edit, and delete operate on the same resource identity and optimistic-version protocol. The UI shows scope tabs and an optional project/agent selector only when the corresponding scope is selected.

## OmniRoute lifecycle

An `OmniRouteProcessManager` detects the configured endpoint, distinguishes AgentOS-owned and external processes, and reports stopped, starting, ready, failed, or external. AgentOS stores `auto_start` in persistent runtime settings. At application startup, an enabled setting schedules a bounded background attempt: detect first, start only when absent, poll the documented health/model endpoint, and record a user-safe failure. Startup never waits indefinitely or fails because OmniRoute is unavailable. Stop/restart are exposed only for a process owned by AgentOS.

## Validation

Unit tests cover routes, scoped memory behavior, persistence, startup ownership, duplicate prevention, failures, and UI navigation. Browser checks cover Settings entry from chat and the primary management routes. The full local stack remains running after verification.

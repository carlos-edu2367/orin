# Agent Skills design

## Scope

Deliver procedural Agent Skills for the existing conversational runtime: registry, versioned SKILL.md packages, hybrid retrieval, agent-facing discovery/load/resource tools, persisted audit records, APIs and a compact Skills library UI. Existing RFC 902 workflow Skills remain a separate future workflow abstraction.

## Architecture

`agentos.skills` owns immutable versions and selection. `TurnSession` builds a compact catalogue from the newest user message, extends its existing toolset with `search_skills`, `list_skills`, `use_skill` and `read_skill_resource`, and emits normal activity events. Loading is a tool result, so all current provider transports retain it in the next model call; the session cache prevents repeats. PostgreSQL stores skill identity/version, associations, and execution snapshots; built-ins remain source-controlled SKILL.md packages seeded idempotently.

## Key decisions

- Agent Skills are Markdown procedures, never authority or executable code.
- Directory/SKILL.md is the interoperable portable package; AgentOS frontmatter extends it only where necessary.
- Discovery is pin + attachment signal + lexical scoring now, with an optional semantic scorer interface and lexical fail-safe.
- Scopes resolve `agent > workspace > user > system`; published versions are immutable and execution loads capture content digest and snapshot.
- Tool prerequisites are checked before use; skill dependency cycles and depth are rejected.
- The UI stays sparse: list/search/detail/create/edit and agent pinning, with instruction sections disclosed only on detail.

## Verification

Unit tests cover parsing, registry resolution, availability, dependency cycles, retrieval ranking, lazy load/cache/security, Postgres schema/service/API, prompt injection, and frontend library interactions. An integration test drives a turn whose provider asks for `use_skill`, asserts the initial prompt has only metadata, and asserts the next provider request receives the requested instructions.

# Plugin hooks and commands design

## Intent

A plugin that declares only `hooks/` and `commands/` is detected and completely ignored today. `inspect_plugin_package` calls `Path.exists()` on both directories and appends a warning string (`inspector.py:66-69`); nothing anywhere else in the codebase reads their contents — not a filename, not a schema, not a count. Because `contribution_count` sums only skills, MCP servers, and agents (`models.py:87-88`), such a package has `contribution_count == 0`, fails `is_installable`, and `PluginService.inspect` rejects it with *"this package contributes nothing Orin can use"* (`service.py:118-119`).

This is Category A, the second of two gaps mapped in live testing of the Plugin Library (see [2026-08-16-plugin-library-design.md](2026-08-16-plugin-library-design.md)); the first — repositories with no manifest at all — was closed in [2026-08-16-plugin-library-raw-mcp-install-design.md](2026-08-16-plugin-library-raw-mcp-install-design.md).

The reference case is https://github.com/eugeniughelbur/obsidian-second-brain: a valid `.claude-plugin/plugin.json`, 46 command files, 3 hooks, and no `skills/*/SKILL.md`. Reading that repository directly surfaced a third, previously unmapped gap: its manifest declares `mcpServers` **inline** and points at its commands with a `"commands": "./commands/"` field, and `parse_plugin_manifest` discards both (`manifest.py:30-45`) while the inspector reads MCP config only from a separate `.mcp.json` (`inspector.py:48`). That repository therefore loses a real MCP server contribution to a manifest-parsing gap, independently of hooks and commands being unsupported.

## Scope

In scope: honoring `mcpServers`, `commands`, and `hooks` declared in `plugin.json`; parsing `commands/*.md` into first-class contributions and expanding them from the chat composer; parsing `hooks/hooks.json` and executing `SessionStart`, `PostToolUse`, and `PostCompact` hooks inside a narrow, purpose-built executor under a consent step separate from installation; counting commands and hooks toward `contribution_count` so a hooks/commands-only plugin becomes installable.

Out of scope: `PreToolUse` and any other blocking hook semantics — v1 hooks cannot veto an action, and the executor makes that structural rather than conventional (see *Hook executor*); `async: true` hook execution; positional `$1`/`$2` command arguments; a general-purpose sandbox (container, seccomp, user namespaces) — the isolation here is path- and argv-confinement, not kernel isolation; hook-authored modification of tool arguments or results; commands or hooks contributed by anything other than an installed plugin.

### Why the existing lifecycle machinery is not the substrate

Two candidate substrates were evaluated and rejected on evidence:

- **`AgenticRuntime._life` lifecycle states** (`running`, `waiting_tool`, `tool_started`, `tool_finished`, `context_compacted`, …) are one-way telemetry. `_life` forwards to `store.lifecycle(...)` and discards the return value (`runtime.py:729-731`); the states exist to feed the SSE timeline. There is no return path for a handler to influence anything.
- **The Event System** ([2026-08-06-event-system-design.md](2026-08-06-event-system-design.md)) is explicitly specified to "publish only confirmed facts" with at-least-once delivery. A hook that runs *before* a fact, or that could change it, is the opposite contract. The bus is a fit for notification-only hooks and a misfit for anything else.

The real choke points are narrower and are used directly: `AgentToolset.invoke` is reached from exactly three call sites inside `_run_toolset` (`runtime.py:681`, `:690`, `:698`); `PostgresChatStore.create` (`chat.py:246`) is where a user message becomes a turn; `TurnSession` prompt assembly (`session.py:781`) is the nearest equivalent to session start.

### Relationship to the existing isolation posture

`LocalTerminalAdapter.execute` is this codebase's existing statement about running third-party processes (`terminal/local.py:100-108`): `shlex.split` with no shell, an executable allow-list, and outright rejection of `|  ;  &&  ||  >  <  \`  $(`  newline`. Its comment records that production supplies "a narrower read-only list."

Claude Code hooks are arbitrary shell command strings. The reference repository's `hooks.json` declares `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/load_vault_context.py"` on `SessionStart`, `"${CLAUDE_PLUGIN_ROOT}/hooks/validate-ai-first.sh"` on `PostToolUse` with `matcher: "Write|Edit|MultiEdit|NotebookEdit|create_file"` and `timeout: 10`, and another `.sh` on `PostCompact` with `async: true`.

A faithful Claude Code hook engine would therefore contradict the posture this codebase already committed to. The resolution adopted here is neither "run arbitrary shell" nor "run nothing": a dedicated executor that keeps the terminal adapter's no-shell rule and adds package confinement, so the real hooks above run while the class of commands that motivates a sandbox does not. This is a deliberate reduction in fidelity, recorded as such.

## Delivery order

Three independently mergeable increments, in order. Increment 0 delivers value on its own even if 1 and 2 never ship.

0. **Manifest gap** — `mcpServers` inline, `commands` and `hooks` path fields.
1. **Commands** — parsing, expansion, API, composer picker.
2. **Hooks** — parsing, consent, executor, dispatch.

## Backend

### Increment 0 — manifest fields

`parse_plugin_manifest` (`manifest.py`) reads three fields it currently discards. `PluginManifest` gains `mcp_servers: tuple[McpServerContribution, ...]`, `commands_path: str`, and `hooks_path: str`.

- `mcpServers`: parsed by the existing `parse_mcp_config`, which already accepts a mapping of slug → server config and applies the transport/HTTPS/arg rules. The inspector unions the manifest-declared servers with those from a separate `.mcp.json`; on a slug collision `.mcp.json` wins, being the more specific declaration. The combined result stays capped at 16 servers, the cap `parse_mcp_config` already applies.
- `commands` and `hooks`: optional relative paths, defaulting to `./commands/` and `./hooks/`. Each is resolved against the package root and must remain inside it. A path that escapes via `..`, an absolute path, or a symlink target outside the package is **rejected**, not silently corrected: `PluginServiceError` with `code="plugin_manifest_path_rejected"`, using the `code=` mechanism added for Category B (`service.py:25-27`) and already read by the gateway's `plugin_service_error` handler.

`${CLAUDE_PLUGIN_ROOT}` appearing inside an `mcpServers` command or args is left as a literal at inspection time; it is resolved when the server is launched, which is outside this design.

### Contribution model

`PluginInspection` (`models.py`) gains two collections symmetric with the three that exist:

```python
@dataclass(frozen=True, slots=True)
class CommandContribution:
    command_id: str        # "{plugin_id}:{slug}"
    slug: str              # invocation name without the plugin prefix
    description: str
    argument_hint: str     # "" when the command declares none
    relative_path: str

@dataclass(frozen=True, slots=True)
class HookContribution:
    hook_id: str           # "{plugin_id}:{event}:{index}", index into that event's
                           # flattened hook list in declaration order; stable for a
                           # given package, and re-derived whenever a version changes
    event: str             # SessionStart | PostToolUse | PostCompact
    matcher: str           # regex over tool name; "" matches all
    command: str           # raw declared string, ${CLAUDE_PLUGIN_ROOT} unresolved
    timeout_seconds: int
```

`contribution_count` becomes the sum of all five collections. This is what makes a hooks/commands-only plugin installable and is the point of the whole design. `is_installable` and `requires_approval` keep their current definitions on top of the new count; the generic *"contributes nothing Orin can use"* error survives for the genuinely empty package.

### Persistence

No change to `plugin_contributions`. Its `kind` column is `String(32)` and its uniqueness constraint is `(plugin_id, user_id, kind, reference)` (`schema.py:792-797`), so two new `kind` values — `command` and `hook` — need no migration. `reference` carries `command_id` / `hook_id`.

The `enabled` boolean already on that table is the hook consent record: a `hook` row with `enabled=false` is exactly "declared, approved at install, not authorized to execute."

A command's markdown body is **not** copied into `plugin_contributions`. The row carries identity only; the body is read from the plugin's `install_path` at expansion time, so the package stays the single source of truth.

One migration, `0037_plugin_commands_and_hooks`, adds two conversation-scoped tables (see *Command expansion* and *SessionStart semantics*).

### Increment 1 — command parsing

`src/agentos/plugins/commands.py` exposes `parse_commands(root: Path, *, plugin_id: str) -> tuple[tuple[CommandContribution, ...], tuple[str, ...]]`, returning contributions and warnings.

- One `CommandContribution` per `*.md` directly under the commands directory, sorted, capped at 200 — the cap the inspector already applies to skills (`inspector.py:27`).
- `slug` comes from the filename stem through `plugin_id_from_name`; `command_id` is `f"{plugin_id}:{slug}"`, matching how plugin skill identities are built (`inspector.py:43`).
- YAML frontmatter supplies `description` and `argument-hint`. **Unknown frontmatter keys are ignored, never rejected.** The reference repository's `obsidian-daily.md` declares `category`, `trigger-mode`, `triggers_en`, `triggers_es`, `triggers_pt`, and `triggers_zh`; authors invent keys freely and a plugin must not fail because of one.
- A file with no frontmatter is still a valid command with an empty description. A file that cannot be read or decoded becomes a warning and is skipped, mirroring the existing broken-skill and broken-agent handling.

### Command resolution and collisions

`src/agentos/plugins/command_library.py` exposes `CommandLibrary`, injected into `PluginActivator` the way `skill_library`, `mcp_service`, and `agent_templates` already are.

```python
class CommandLibrary:
    def install_plugin_commands(self, *, user_id, plugin_id, commands) -> None: ...
    def remove_plugin_commands(self, *, user_id, plugin_id) -> None: ...
    def list(self, user_id: str) -> tuple[dict, ...]: ...
    def resolve(self, user_id: str, token: str) -> ResolvedCommand | None: ...
```

`resolve` accepts either the bare `slug` or the qualified `plugin-id:slug`. When two or more *active* plugins declare the same slug, the bare form resolves to nothing and only the qualified form works for either. Ambiguity never silently picks a winner. `list` marks the ambiguous ones so the picker can show the qualified form.

`ResolvedCommand` carries `command_id`, `plugin_id`, and the absolute path to the markdown file; the body is read on demand.

### Command expansion

Expansion happens in `PostgresChatStore.create` (`chat.py:246`), before the message rows are written, so a command message is a normal turn by the time anything downstream sees it.

The trigger rule is deliberately conservative. Expansion is attempted only when the message's **first whitespace-delimited token** starts with `/`, and the token's remainder after that leading `/` — with no further slashes in it — resolves through `CommandLibrary.resolve` to an active command. Any other message beginning with `/` — a path, a regex, a date — passes through byte-for-byte. A normal message is never hijacked.

Argument substitution: the message text after that first token, stripped, replaces `$ARGUMENTS` wherever it appears in the body. If the body declares no `$ARGUMENTS` and arguments were supplied, the body is followed by a blank line and `Argumentos: <arguments>`; many commands in the reference corpus declare no placeholder at all. If no arguments were supplied, `$ARGUMENTS` is replaced with the empty string and nothing is appended.

`conversation_messages.content` keeps **what the person typed** (`/obsidian-daily amanhã`), so the transcript shows the command rather than a 200-line prompt, and so the `String(16000)` content limit is not consumed by a body that can exceed it.

The expansion is persisted in `conversation_message_commands` (migration `0037`), mirroring `0032_message_attachments`: `message_id`, `plugin_id`, `command_id`, `arguments`, `expanded_body`. `history_for_turn` substitutes `expanded_body` for that message's content — the same place `_attachment_marker` already augments content (`chat.py:448-452`).

Persisting the expanded body rather than re-expanding on read is deliberate: in later turns the model sees exactly the text it saw originally, even if the plugin is later disabled, upgraded, or removed.

### API

`GET /v1/plugins/commands`, alongside the existing plugin routes (`gateway.py:945-1020`), rate-limit action `plugins.commands`. Returns the caller's active commands: `command_id`, `slug`, `qualified` (bool — whether the bare slug is ambiguous), `plugin_id`, `plugin_display_name`, `description`, `argument_hint`.

### Increment 2 — hook manifest parsing

`src/agentos/plugins/hooks_manifest.py` exposes `parse_hooks(root: Path, *, plugin_id: str)`, reading `hooks/hooks.json` (or the manifest-declared hooks path) in the Claude Code shape: `{"hooks": {<Event>: [{"matcher": …, "hooks": [{"type": "command", "command": …, "timeout": …, "async": …}]}]}}`.

- Recognized events: `SessionStart`, `PostToolUse`, `PostCompact`. Any other event name produces an explicit warning — *"declared, not supported in this version"* — rather than vanishing.
- `type` other than `"command"` is warned and skipped.
- `timeout` defaults to 10 seconds and is clamped to 1–30.
- `async: true` is ignored in v1 and warned, so the author's intent is visible rather than silently reinterpreted.
- `matcher` is compiled as a regex at parse time; a matcher that will not compile is warned and the hook is skipped.
- Cap of 32 hooks per plugin.

### Hook consent

Approval of the plugin lists each hook **with its exact command string** — the user approves what will run, not a count. Activation inserts `hook` contribution rows with `enabled=false`.

A separate action flips them on: `PUT /v1/plugins/{plugin_id}/hooks-enabled`, rate-limit action `plugins.hooks_enabled`, body `{"enabled": bool}`. Installing a plugin is not authorizing it to execute code, and the two decisions stay separately revocable.

`PluginActivator.deactivate` removes command and hook rows symmetrically with the MCP and skill rollback it already performs.

### Hook executor

`src/agentos/plugins/hook_executor.py` is the only module in this design that touches a process. Its rules exist to make both veto and package escape structurally impossible:

1. `${CLAUDE_PLUGIN_ROOT}` is the only interpolation performed, and it resolves to the plugin's own `install_path`.
2. The resulting string is split with `shlex.split(posix=True)`. Any of `|  ;  &&  ||  >  <  \`  $(  \n  \r` in the command **rejects the hook** — it never causes a shell. This reuses the terminal adapter's deny list verbatim (`terminal/local.py:107`).
3. `argv[0]` must resolve, after `Path.resolve()`, to a file inside `install_path`; **or** it must be a bare interpreter from a minimal allow-list (`python`, `python3`, `node`, `sh`, `bash`) whose **first argument** resolves to a file inside `install_path`. This single rule admits `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/load_vault_context.py"` and `"${CLAUDE_PLUGIN_ROOT}/hooks/validate-ai-first.sh"` while refusing `python3 -c "…"`, `curl …`, and any path outside the package.
4. `subprocess.Popen(argv, shell=False, start_new_session=True, cwd=install_path)`.
5. The environment is **constructed from scratch** — a minimal allow-list (`PATH`, `HOME`/`USERPROFILE`, `SYSTEMROOT`, `TMPDIR`/`TEMP`, `LANG`) plus `CLAUDE_PLUGIN_ROOT`. The Orin process environment is never inherited wholesale and no secret is ever passed.
6. The event payload is written to stdin as JSON, which is the Claude Code contract.
7. On timeout the process tree is killed. stdout and stderr are captured and truncated at 12,000 characters, the limit the runtime already applies to tool output (`runtime.py:696`).
8. **The exit code is recorded and never obeyed.** The executor's return type carries no field capable of denying anything, so "no veto" is a property of the code rather than a convention a future edit could quietly break.

A `.sh` hook with no execute bit — which is every `.sh` on Windows — fails cleanly and per hook, recorded, without disturbing the turn.

### Hook dispatch

`src/agentos/plugins/hook_engine.py` resolves which hooks match an event and hands each to the executor.

| Event | Integration point | Destination of stdout |
|---|---|---|
| `SessionStart` | prompt assembly, `session.py:781` | injected as a labelled context block, explicitly subordinate to the system prompt |
| `PostToolUse` | result loop of `_run_toolset`, `runtime.py:710`, only when `matcher` matches `tool_name` | activity event on the timeline |
| `PostCompact` | after `context_compacted`, `runtime.py:398` | activity event on the timeline |

Only hooks belonging to plugins that are `ACTIVE` **and** whose `hook` contribution rows are `enabled=true` are dispatched.

### SessionStart semantics

In Orin a "session" is the conversation, not the turn, and successive turns may run in different worker processes. `SessionStart` hooks fire on the **first turn of a conversation**, and their output is persisted in `conversation_hook_context` (`conversation_id`, `plugin_id`, `hook_id`, `body`, `created_at`) — the second table in migration `0037`. Later turns of the same conversation inject the stored block without relaunching the process.

The alternative — firing on every turn — would give fresher context but places a `Popen` per hook on the hot path of every turn. Predictability and cost were chosen over freshness; the trade-off is recorded here so a future revision can revisit it with evidence.

### Failure isolation

A hook that fails, times out, is rejected by the executor's rules, or cannot be launched **never fails the turn**. It produces a visible activity event and execution continues.

No automatic disabling after repeated failure in v1: a failure counter that survives worker restarts is complexity nothing has yet asked for, and a visible failure is already actionable.

## Frontend

### Data layer

`pluginsApi.ts` gains `listPluginCommands()` for `GET /v1/plugins/commands` and `setPluginHooksEnabled(pluginId, enabled)` for the consent route, following the shapes already in that module.

### Composer picker

`Composer.tsx` is a plain textarea today (`Composer.tsx:109`) with no `/` or `@` affordance, so the picker is new.

It opens when `/` is typed **at the start of an otherwise empty composer** — not mid-text, or a file path would summon it. Subsequent typing filters by slug and description prefix. Arrow keys move, Enter selects, Escape dismisses and leaves the typed text alone. Each row shows the slug (qualified when ambiguous), the plugin's display name, and the description; the `argument_hint` becomes the placeholder once a command is selected.

The picker owns `ArrowUp`/`ArrowDown`/`Enter`/`Escape` only while open, so the existing Enter-to-send behaviour (`Composer.tsx:76-82`) is untouched when it is closed.

### Transcript

A user message with a `conversation_message_commands` record renders as a command chip — slug plus arguments — rather than as raw text, so a 200-line expansion never floods the transcript.

### Approval card

`PluginApprovalCard.tsx` currently lists skills, MCP servers, and agents, and shows *"Este plugin não oferece contribuições compatíveis com o Orin"* when all three are empty (`PluginApprovalCard.tsx:21-23`). It gains two sections:

- **Commands**: slug and description per command, collapsed behind a count past a handful, since 46 is a realistic number.
- **Hooks**: event, matcher, and the **exact command string**, always expanded and never truncated — this is the text the user is authorizing to execute, and it is the one thing on the card that must not be summarized.

The card states plainly that approving installs the hooks without authorizing them to run, and points at the separate consent control.

### Plugin detail

The plugin's row in the Biblioteca gains a "permitir execução de hooks" toggle, present only when the plugin contributes hooks, wired to `setPluginHooksEnabled`, and showing the current consent state.

## Error handling

| Situation | Behaviour |
|---|---|
| `commands`/`hooks` path escapes the package | `PluginServiceError(code="plugin_manifest_path_rejected")`; inspection fails |
| Unreadable or undecodable command file | Warning, file skipped, rest of the plugin installs |
| Malformed `hooks.json` | Warning, hooks contribute nothing, commands and other contributions still install |
| Unsupported hook event, `type`, or `async: true` | Warning naming the specific declaration; that hook is skipped |
| `/token` matching no active command | Message passes through unchanged as plain text |
| Ambiguous bare slug | Bare form resolves to nothing; qualified form works; picker shows the qualified form |
| Hook rejected by executor rules | Activity event naming the rule; turn continues |
| Hook non-zero exit, timeout, or crash | Activity event; **turn continues; nothing is blocked** |
| Package with no contributions of any of the five kinds | Existing generic *"contributes nothing Orin can use"* |

## Testing

Backend, red before green:

- **Manifest**: inline `mcpServers` parsed; union with `.mcp.json` with `.mcp.json` winning a slug collision; `commands`/`hooks` path fields honored; escaping paths rejected with the specific code. Fixture mirrors the reference repository's real shape.
- **Commands**: unknown frontmatter keys tolerated; missing frontmatter; the 200 cap; `$ARGUMENTS` present, absent-with-arguments, and absent-without-arguments; ambiguous slug across two active plugins.
- **Expansion**: `create` writes the typed text to `content` and the body to `conversation_message_commands`; `history_for_turn` substitutes the body; **a message starting with `/` that is not a command survives byte-for-byte**; expansion survives the plugin being disabled afterwards.
- **Hook parsing**: the reference `hooks.json` yields exactly three hooks; unknown event warns; uncompilable matcher skipped; timeout clamped; `async` warned.
- **Executor**: shell metacharacters rejected; `argv[0]` outside `install_path` rejected; `python3 -c` rejected; `python3 <script inside package>` accepted; timeout kills the tree; environment contains no inherited Orin variable; **a non-zero exit blocks nothing**.
- **Dispatch**: hooks with `enabled=false` never run; `PostToolUse` matcher filters by tool name; `SessionStart` runs once per conversation and later turns reuse the stored block.
- **Activation**: commands and hooks appear as contributions; `deactivate` removes both; `contribution_count` includes them; a hooks-only package is installable.

Frontend:

- Picker opens on leading `/` only, filters, selects, dismisses on Escape, and leaves Enter-to-send intact when closed.
- Command chip renders in the transcript.
- Approval card lists commands and hooks, shows the exact hook command string, and no longer shows the "no compatible contributions" message for a hooks/commands-only plugin.
- Hooks consent toggle appears only for plugins contributing hooks and reflects state.

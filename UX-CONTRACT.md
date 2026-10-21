# UX contract

## Chat and Code mode lifecycle

1. A conversation is either **Orin Chat** or **Orin Code** for its entire lifetime.
   From a Chat conversation, the agent may propose opening a new Code session via
   `ProposeCodeSession` (`CodeSessionProposalCard`), letting the user review or
   edit the workspace folder, provider/model, permission mode, and prompt.
2. In `plan` mode (or when `EnterPlanMode` / `ExitPlanMode` is used), Orin writes
   a plan under `.orin/plans/` and presents a `PlanApprovalCard` before mutating
   project files or running commands.
3. Automated tests and `VerifyProject` checks are expected for implementation
   work. When browser verification is needed, the agent uses the **Playwright MCP**
   server (`@playwright/mcp`, managed in *Configurações → Navegador*).
4. A genuine decision or a permission prompt produces an explicit interactive
   card (`UserQuestionCard`, `ToolPermissionCard`, `McpApprovalCard`,
   `PluginApprovalCard`).
5. Completion is shown alongside the turn's work duration (`Trabalhou por...`)
   and token/USD cost estimates in the overview panel; caveats remain visible
   instead of being silently treated as success.

## Permissions and autonomy

- Six canonical permission modes are supported: `default`, `acceptEdits`,
  `plan`, `auto` (local classifier with circuit breaker), `dontAsk`, and
  `bypassPermissions` (legacy aliases `ask_permissions`, `accept_edits`,
  `plan_mode`, and `full_autonomy` map to these).
- `Shift+Tab` in the composer cycles through `default → acceptEdits → plan`
  (plus `auto` when configured, and `bypassPermissions` when allowed by settings).
- Multi-layer rules (`deny → ask → allow`) across `managed`, `cli`, `local`,
  `project`, and `user` settings govern tool execution, backed by a `tree-sitter`
  AST parser for `Bash` and `PowerShell` commands and **Workspace Trust** for
  repository-provided hooks and MCP servers.

## Notifications and monitoring

- Multiple sessions (Chat and Code) and background subagents/commands can run
  concurrently, with live status indicators in the sidebar (`running`,
  `waiting_user`, `failed`).
- Desktop/web notifications (`PushNotification` and the `Notification` hook) are
  opt-in and announce completion, idle attention, or a required permission/decision.
- Notification delivery failure must never change task state.

## Activity in chat

- A continuous sequence of ordinary tools in the same turn and agent is one
  expandable activity line, drawn as quiet text with a chevron rather than a
  box. While it runs, the title is the latest human-readable action beside a
  live clock; once settled, it is one sentence summarising the batch ("Explorou
  3 arquivos, executou 4 comandos"). Expanding it lists every individual call
  as a verb and target, with failures marked, so no call or failure is lost.
- Parallel subagents fold into separate per-agent `SubagentTaskContainer` blocks
  keyed by `agentId`, never shattering when events interleave.
- Approval requests, plan reviews, user questions, and code-session proposals
  remain independent interactive cards so the person can act on them directly.

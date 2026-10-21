# Orin design context

## Product character

Orin is a local-first agent workspace. It should feel composed, precise and
calm while work is in progress: a person is supervising an intelligent system,
not operating a terminal emulator.

The visual language is a dark, space-like workspace with restrained violet
light. Information density is high, but hierarchy is created with spacing,
subtle surfaces and typography rather than dense boxes or decorative effects.

## Tokens and visual rules

- Use the existing `--orin-*` and surface tokens in `frontend/src/styles/theme.css`.
- Backgrounds stay near `--orin-ink` / `--orin-surface`; raised content uses
  the existing translucent surface tokens (`--surface-raised`, `--bg-subtle`).
- Violet is an affordance and focus color, not a fill for large regions.
- Success, warning and danger use the existing semantic tokens. Never convey a
  state through color alone.
- Controls are compact, labelled and keyboard reachable. Icon-only controls
  require an accessible name and a tooltip/title.
- Interactive decision cards (`UserQuestionCard`, `CodeSessionProposalCard`,
  `McpApprovalCard`, `PluginApprovalCard`, `ToolPermissionCard`) use a single
  elevated surface without nested double borders (`cards-inside-cards`).

## Chat and Code modes

Each conversation is created in either **Orin Chat** or **Orin Code** (the mode
is an immutable property of the conversation). In Orin Chat, the agent can
propose handing off engineering work into a dedicated Code session through the
`CodeSessionProposalCard` (`ProposeCodeSession`).

In Orin Code, progress is surfaced through the `TodoBar`, inline diffs,
`PlanApprovalCard`, and collapsible tool batches. It must state whether work is
running, awaiting a person, blocked, or completed; it must not imply validation
that did not happen.

## Responsive and accessibility expectations

- Preserve readable order from desktop through narrow layouts.
- Respect `prefers-reduced-motion`; animation may support orientation but is
  never the only state signal.
- Use native buttons, form labels, `aria-live` for asynchronous status, and
  visible focus states supplied by the shared styles.
- Settings expose one persistent preference per concept and provide a clear
  saved/error state beside the action that caused it.

# Orin design context

## Product character

Orin is a local-first agent workspace. It should feel composed, precise and
calm while work is in progress: a person is supervising an intelligent system,
not operating a terminal emulator.

The visual language is a dark, space-like workspace with restrained violet
light. Information density is high, but hierarchy is created with spacing,
subtle surfaces and typography rather than dense boxes or decorative effects.

## Tokens and visual rules

- Use the existing `--orin-*` tokens in `frontend/src/styles/theme.css`.
- Backgrounds stay near `--orin-ink` / `--orin-surface`; raised content uses
  the existing translucent surface tokens.
- Violet is an affordance and focus color, not a fill for large regions.
- Success, warning and danger use the existing semantic tokens. Never convey a
  state through color alone.
- Controls are compact, labelled and keyboard reachable. Icon-only controls
  require an accessible name and a tooltip/title.

## Code mode

Code mode is a visible workflow, not an opaque setting. Its central card is
the canonical representation: `Planejar → Implementar → Testar → Corrigir →
Entregar`. It must state whether work is running, awaiting a person, blocked,
or completed; it must not imply validation that did not happen.

The composer toggle is a quiet explicit override. Automatic detection remains
server-side, so the absence of an active toggle never means code work is
disabled.

## Responsive and accessibility expectations

- Preserve readable order from desktop through narrow layouts.
- Respect `prefers-reduced-motion`; animation may support orientation but is
  never the only state signal.
- Use native buttons, form labels, `aria-live` for asynchronous status, and
  visible focus states supplied by the shared styles.
- Settings expose one persistent preference per concept and provide a clear
  saved/error state beside the action that caused it.

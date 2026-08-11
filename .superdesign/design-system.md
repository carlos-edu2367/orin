# AgentOS UI design system

## Product and tone

AgentOS is a local-first environment for orchestrating agents. Interfaces are calm, technical and legible: they should make capability, provenance and status understandable without turning operational work into a dashboard.

## Visual tokens

- Canvas: `#07090b` (`--ink`), with raised surfaces around `#0b0d10`.
- Text: warm white `#f4f0e9`; muted content uses `rgba(244,240,233,.56)` and faint content `rgba(244,240,233,.32)`.
- Accent: restrained lime `#c8ff6a` (`--signal`), used only for primary actions, active state and success indicators.
- Danger: `#ff9d91`.
- Geometry: 16px for surfaces, 10px for compact controls, hairline low-opacity borders.
- Typography: system sans for interface and `--mono` for identifiers, semantic versions, counts and metadata.
- Motion: quiet opacity/translate transitions using `cubic-bezier(.22,.61,.36,1)`; respect reduced motion.

## Page and component principles

- Keep the desktop experience centred with a narrow readable content column; collapse controls for mobile below 860px.
- Use an understated brand bar with the AgentOS mark, page title and the existing command palette.
- Prefer disclosure panels and compact metadata rows over dense cards or large illustrations.
- Operational state must be textually explicit and never colour-only.
- Do not introduce gradients, decorative fonts, bright multi-colour accents, glassmorphism or marketplace-style visual noise.

## Skills library flow

- `/skills`: heading, short explanation, search field, quiet source filters, and a compact installed-skill list.
- `/skills/:skillId`: return affordance, summary, version/source/tags, progressive disclosure sections for instructions, dependencies, usage and versions.
- Creation and editing happen in a focused form with basic fields first and advanced fields behind a disclosure.
- Agent configuration exposes `Auto discover` and a compact pinned-skills picker; selected skills are metadata chips, not instruction bodies.

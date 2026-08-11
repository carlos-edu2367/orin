# Theme

## Token summary

- Background: `--ink #07090b`, `--surface #0b0d10`; raised layers use low-opacity warm white.
- Text: `--text #f4f0e9`, `--muted rgba(244,240,233,.56)`, `--faint rgba(244,240,233,.32)`.
- Signal: `--signal #c8ff6a`; danger `#ff9d91`; technical text uses `--mono`.
- Radius: 16px default and 10px compact; motion ease `cubic-bezier(.22,.61,.36,1)`.
- Desktop-to-mobile breakpoint: 860px; reduced motion respects `prefers-reduced-motion`.

## Source

Theme and component CSS: `frontend/src/styles/agentos.css`; Tailwind layers and global base: `frontend/src/styles/index.css`.

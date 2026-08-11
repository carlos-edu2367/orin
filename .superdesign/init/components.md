# Shared UI components

## `frontend/src/features/conversations/Composer.tsx` — Composer

Reusable conversation input. Props: `value`, `onChange`, `onSubmit`, `onStop`, `running`, `error`.

```tsx
export function Composer({ value, onChange, onSubmit, onStop, running = false, disabled = false, placeholder = 'Descreva o que você precisa…', settings, hint, error, autoFocus, notice, focusSignal = 0, canSend = true }: ComposerProps) {
  // Auto-resizing textarea, Enter-to-send, and an animated Send/Stop control.
  return <form className={`composer${focused ? ' is-focused' : ''}${running ? ' is-running' : ''}`} onSubmit={submit}>…</form>
}
```

Full implementation source: `frontend/src/features/conversations/Composer.tsx`.

## `frontend/src/components/CommandPalette.tsx` — CommandPalette

Reusable Ctrl/Cmd+K navigation dialog. Props: optional commands and recent conversations.

```tsx
export function CommandPalette({ commands = [], conversations = [] }: CommandPaletteProps) {
  // Filters commands, handles keyboard navigation, and renders its dialog in a portal.
  return <>…</>
}
```

Full implementation source: `frontend/src/components/CommandPalette.tsx`.

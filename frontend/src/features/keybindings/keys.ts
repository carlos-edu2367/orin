/**
 * Key strings in Claude Code's `keybindings.json` syntax (W7): modifiers joined with `+` (`ctrl+shift+k`), chords
 * separated by a space (`ctrl+k ctrl+s`). `alt`, `opt`, `option` and `meta` are the Alt/Option key; `cmd`, `command`,
 * `super` and `win` are the Command/Windows key. Names are case-insensitive; the canonical form orders the modifiers
 * ctrl, alt, shift, cmd.
 */

const MODIFIER_ALIASES: Record<string, 'ctrl' | 'alt' | 'shift' | 'cmd'> = {
  ctrl: 'ctrl', control: 'ctrl',
  alt: 'alt', opt: 'alt', option: 'alt', meta: 'alt',
  shift: 'shift',
  cmd: 'cmd', command: 'cmd', super: 'cmd', win: 'cmd',
}
const MODIFIER_ORDER = ['ctrl', 'alt', 'shift', 'cmd'] as const

const KEY_ALIASES: Record<string, string> = {
  esc: 'escape', escape: 'escape', return: 'enter', enter: 'enter', tab: 'tab', space: 'space', ' ': 'space',
  up: 'up', arrowup: 'up', down: 'down', arrowdown: 'down', left: 'left', arrowleft: 'left', right: 'right', arrowright: 'right',
  pageup: 'pageup', pagedown: 'pagedown', home: 'home', end: 'end', backspace: 'backspace', delete: 'delete', del: 'delete',
  insert: 'insert', plus: '+', minus: '-',
}
const NAMED_KEYS = new Set([
  'escape', 'enter', 'tab', 'space', 'up', 'down', 'left', 'right', 'pageup', 'pagedown', 'home', 'end', 'backspace', 'delete',
  'insert', ...Array.from({ length: 12 }, (_, index) => `f${index + 1}`),
])

export const CHORD_TIMEOUT_MS = 3000

export type KeyParse = { ok: true; keys: string[] } | { ok: false; error: string }

/** One keystroke (`ctrl+shift+k`) in canonical form, or null when it is not a valid keystroke. */
export function normalizeStroke(raw: string): string | null {
  const text = raw.trim().toLowerCase()
  if (!text) return null
  // A trailing "+" is the plus key itself ("ctrl++").
  const parts = text.endsWith('++') ? [...text.slice(0, -2).split('+'), '+'] : text.split('+')
  if (parts.some((part) => part === '')) return null
  const key = parts[parts.length - 1]
  const modifiers = new Set<string>()
  for (const part of parts.slice(0, -1)) {
    const modifier = MODIFIER_ALIASES[part]
    if (!modifier || modifiers.has(modifier)) return null
    modifiers.add(modifier)
  }
  const name = KEY_ALIASES[key] ?? key
  if (!NAMED_KEYS.has(name) && [...name].length !== 1) return null
  return [...MODIFIER_ORDER.filter((modifier) => modifiers.has(modifier)), name].join('+')
}

/** A whole binding: one keystroke or a chord of up to three. */
export function parseKeys(raw: string): KeyParse {
  const strokes = raw.trim().split(/\s+/).filter(Boolean)
  if (strokes.length === 0) return { ok: false, error: 'empty key' }
  if (strokes.length > 3) return { ok: false, error: 'a chord has at most three keystrokes' }
  const keys: string[] = []
  for (const stroke of strokes) {
    const normalized = normalizeStroke(stroke)
    if (!normalized) return { ok: false, error: `'${stroke}' is not a keystroke` }
    keys.push(normalized)
  }
  return { ok: true, keys }
}

/** The canonical keystroke of a keyboard event, or null for a lone modifier press. */
export function strokeFromEvent(event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'altKey' | 'shiftKey' | 'metaKey'> & { code?: string }): string | null {
  // Option+letter on a Mac types a symbol (Option+P is "π"): with Alt held, the physical key names the letter.
  const physical = event.altKey ? /^Key([A-Z])$/.exec(event.code ?? '')?.[1] ?? /^Digit([0-9])$/.exec(event.code ?? '')?.[1] : undefined
  const raw = physical ? physical.toLowerCase() : event.key
  if (!raw || ['Control', 'Shift', 'Alt', 'Meta', 'AltGraph', 'CapsLock', 'Dead', 'Unidentified'].includes(raw)) return null
  let name = KEY_ALIASES[raw.toLowerCase()] ?? raw.toLowerCase()
  // Shift turns "/" into "?" and "1" into "!": the shifted character is the key, so "shift" is implied, not added,
  // for printable characters (Claude Code's own syntax binds "?" directly).
  const printable = [...name].length === 1 && name !== ' '
  if (name === ' ') name = 'space'
  const modifiers: string[] = []
  if (event.ctrlKey) modifiers.push('ctrl')
  if (event.altKey) modifiers.push('alt')
  if (event.shiftKey && (!printable || /[a-z]/.test(name))) modifiers.push('shift')
  if (event.metaKey) modifiers.push('cmd')
  if (printable && /[a-z]/.test(name)) name = name.toLowerCase()
  return [...modifiers, name].join('+')
}

export function displayKeys(keys: readonly string[], platform: 'mac' | 'other' = 'other'): string {
  const label = (stroke: string) => stroke.split('+').map((part) => {
    if (part === 'ctrl') return platform === 'mac' ? '⌃' : 'Ctrl'
    if (part === 'alt') return platform === 'mac' ? '⌥' : 'Alt'
    if (part === 'shift') return platform === 'mac' ? '⇧' : 'Shift'
    if (part === 'cmd') return platform === 'mac' ? '⌘' : 'Win'
    if (part === 'escape') return 'Esc'
    if (part === 'enter') return 'Enter'
    if (part === 'space') return 'Espaço'
    if (part.length === 1) return part.toUpperCase()
    return part.charAt(0).toUpperCase() + part.slice(1)
  }).join(platform === 'mac' ? '' : '+')
  return keys.map(label).join(' ')
}

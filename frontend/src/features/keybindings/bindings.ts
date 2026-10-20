/**
 * Reading `keybindings.json` and resolving what each key does (W7).
 *
 * The file is a list of `{ context, bindings: { key: action | null } }` blocks, or an object with that list under
 * `bindings` (and optional `$schema`/`$docs`). `null` unbinds a default. Later sources win: Orin's defaults, then
 * Claude Code's file (`~/.claude/keybindings.json`), then Orin's (`~/.orin/keybindings.json`).
 */
import { ACTIONS, BROWSER_RESERVED_KEYS, CLAUDE_CONTEXTS, type Environment, type KeyContext, RESERVED_KEYS, UNSUPPORTED_ACTIONS } from './catalog'
import { parseKeys } from './keys'

export type UserBinding = { context: KeyContext; keys: string[]; action: string | null }
export type Validation = { bindings: UserBinding[]; errors: string[]; warnings: string[] }

const MAX_BINDINGS = 500

export function validateKeybindings(text: string | null, environment: Environment): Validation {
  const result: Validation = { bindings: [], errors: [], warnings: [] }
  if (text === null || !text.trim()) return result
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch (error) {
    result.errors.push(`não é JSON válido: ${error instanceof Error ? error.message : String(error)}`.slice(0, 300))
    return result
  }
  const blocks = Array.isArray(data) ? data : (typeof data === 'object' && data !== null ? (data as Record<string, unknown>).bindings : undefined)
  if (!Array.isArray(blocks)) {
    result.errors.push("o arquivo precisa ser uma lista de blocos, ou um objeto com a lista em 'bindings'")
    return result
  }
  const seen = new Map<string, string>()
  blocks.forEach((block, index) => {
    const entry = typeof block === 'object' && block !== null ? (block as Record<string, unknown>) : null
    const where = `bloco ${index + 1}`
    if (!entry) { result.errors.push(`${where}: não é um objeto`); return }
    const context = entry.context
    if (typeof context !== 'string' || !(CLAUDE_CONTEXTS as readonly string[]).includes(context)) {
      result.errors.push(`${where}: contexto desconhecido '${String(context).slice(0, 60)}'`)
      return
    }
    const map = entry.bindings
    if (typeof map !== 'object' || map === null || Array.isArray(map)) {
      result.errors.push(`${where} (${context}): 'bindings' precisa ser um objeto de tecla → ação`)
      return
    }
    for (const [rawKey, action] of Object.entries(map as Record<string, unknown>)) {
      if (result.bindings.length >= MAX_BINDINGS) { result.errors.push(`mais de ${MAX_BINDINGS} atalhos; o resto foi ignorado`); return }
      const parsed = parseKeys(rawKey)
      if (!parsed.ok) { result.errors.push(`${context}: ${parsed.error}`); continue }
      const label = parsed.keys.join(' ')
      if (action !== null && typeof action !== 'string') { result.errors.push(`${context} ${label}: a ação precisa ser um texto ou null`); continue }
      if (parsed.keys.some((key) => RESERVED_KEYS.has(key))) { result.errors.push(`${context} ${label}: tecla reservada (copiar, colar e edição de texto)`); continue }
      if (typeof action === 'string' && !ACTIONS[action]) {
        if (UNSUPPORTED_ACTIONS.has(action)) result.warnings.push(`${context} ${label}: '${action}' não existe na interface do Orin; o atalho é ignorado`)
        else result.errors.push(`${context} ${label}: ação desconhecida '${action.slice(0, 60)}'`)
        continue
      }
      if (typeof action === 'string' && ACTIONS[action].context !== context && context !== 'Global') {
        result.warnings.push(`${context} ${label}: '${action}' pertence ao contexto ${ACTIONS[action].context}`)
      }
      if (environment === 'browser' && parsed.keys.some((key) => BROWSER_RESERVED_KEYS.has(key))) {
        result.warnings.push(`${context} ${label}: o navegador não deixa a página usar essa tecla; funciona só no app de desktop`)
      }
      const id = `${context}\u0000${label}`
      if (seen.has(id)) result.warnings.push(`${context} ${label}: atalho repetido; vale o último (${String(action)})`)
      seen.set(id, String(action))
      result.bindings.push({ context: context as KeyContext, keys: parsed.keys, action: action as string | null })
    }
  })
  return result
}

/** context → key sequence (canonical, space-joined) → action. */
export type Keymap = Map<KeyContext, Map<string, string>>

export function defaultKeymap(environment: Environment): Keymap {
  const map: Keymap = new Map()
  for (const [action, spec] of Object.entries(ACTIONS)) {
    const keys = environment === 'electron' ? spec.electron : spec.browser
    for (const key of keys) {
      const parsed = parseKeys(key)
      if (!parsed.ok) continue
      if (!map.has(spec.context)) map.set(spec.context, new Map())
      map.get(spec.context)!.set(parsed.keys.join(' '), action)
    }
  }
  return map
}

/** Defaults, then each file's bindings in order (later wins); `null` removes a key. */
export function resolveKeymap(environment: Environment, files: Array<UserBinding[]>): Keymap {
  const map = defaultKeymap(environment)
  for (const bindings of files) {
    for (const binding of bindings) {
      if (!map.has(binding.context)) map.set(binding.context, new Map())
      const sequence = binding.keys.join(' ')
      if (binding.action === null) map.get(binding.context)!.delete(sequence)
      else map.get(binding.context)!.set(sequence, binding.action)
    }
  }
  return map
}

/** The keys bound to an action in the resolved keymap (for hints and the help screen). */
export function keysFor(map: Keymap, action: string): string[][] {
  const context = ACTIONS[action]?.context
  const found: string[][] = []
  for (const [ctx, bindings] of map) {
    if (context && ctx !== context && ctx !== 'Global') continue
    for (const [sequence, bound] of bindings) if (bound === action) found.push(sequence.split(' '))
  }
  return found
}

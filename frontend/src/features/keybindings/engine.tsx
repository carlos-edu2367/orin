/**
 * The keybinding engine (W7): which action a key press means in the contexts on screen, chords included, and a hook
 * for surfaces to handle their actions. Text fields keep typing: a handler decides what it takes.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { ApiClient } from '../../api/client'
import { getKeybindingsFiles } from '../../api/settings'
import { defaultKeymap, type Keymap, resolveKeymap, validateKeybindings, type Validation } from './bindings'
import type { Environment, KeyContext } from './catalog'
import { CHORD_TIMEOUT_MS, strokeFromEvent } from './keys'

export const PENDING = '__chord_pending__'

export type KeyMatch = string | typeof PENDING | null

/** A stateful matcher over a keymap: feed it key events, it answers an action, PENDING (inside a chord) or null. */
export function createMatcher(keymap: Keymap, clock: () => number = () => Date.now()) {
  let pending: { sequence: string; at: number } | null = null
  function lookup(sequence: string, contexts: readonly KeyContext[]): KeyMatch {
    let prefix = false
    for (const context of contexts) {
      const bindings = keymap.get(context)
      if (!bindings) continue
      const action = bindings.get(sequence)
      if (action) return action
      for (const key of bindings.keys()) if (key.startsWith(`${sequence} `)) prefix = true
    }
    return prefix ? PENDING : null
  }
  return function match(event: Parameters<typeof strokeFromEvent>[0], contexts: readonly KeyContext[]): KeyMatch {
    const stroke = strokeFromEvent(event)
    if (!stroke) return null
    const now = clock()
    if (pending && now - pending.at <= CHORD_TIMEOUT_MS) {
      const continued = lookup(`${pending.sequence} ${stroke}`, contexts)
      if (continued === PENDING) { pending = { sequence: `${pending.sequence} ${stroke}`, at: now }; return PENDING }
      pending = null
      if (continued) return continued
    }
    pending = null
    const single = lookup(stroke, contexts)
    if (single === PENDING) pending = { sequence: stroke, at: now }
    return single
  }
}

export function detectEnvironment(): Environment {
  return typeof window !== 'undefined' && 'orinDesktop' in window ? 'electron' : 'browser'
}

type Handler = (event: KeyboardEvent) => boolean | void
type Registration = { id: number; context: KeyContext; handlers: Record<string, Handler> }

type KeybindingsValue = {
  environment: Environment
  keymap: Keymap
  report: { orin: Validation; claude: Validation }
  match: (event: KeyboardEvent, contexts: readonly KeyContext[]) => KeyMatch
  register: (registration: Omit<Registration, 'id'>) => () => void
  reload: () => void
}

const empty: Validation = { bindings: [], errors: [], warnings: [] }

/** Registrations plus the window listener that sends each key to the newest surface handling its action. */
function createDispatcher(initial: ReturnType<typeof createMatcher>) {
  let matcher = initial
  let registrations: Registration[] = []
  let nextId = 1
  function onKeyDown(event: KeyboardEvent) {
    if (event.defaultPrevented || event.isComposing) return
    // The newest surface first (a dialog over the chat), Global last.
    const stack = [...registrations].reverse()
    const contexts = [...new Set([...stack.map((item) => item.context).filter((context) => context !== 'Global'), 'Global' as const])]
    const action = matcher(event, contexts)
    if (action === PENDING) { event.preventDefault(); return }
    if (!action) return
    for (const item of stack) {
      const handler = item.handlers[action]
      if (handler && handler(event) !== false) { event.preventDefault(); return }
    }
  }
  return {
    setMatcher(next: ReturnType<typeof createMatcher>) { matcher = next },
    register(registration: Omit<Registration, 'id'>) {
      const entry = { ...registration, id: nextId++ }
      registrations = [...registrations, entry]
      return () => { registrations = registrations.filter((item) => item.id !== entry.id) }
    },
    attach() {
      window.addEventListener('keydown', onKeyDown)
      return () => window.removeEventListener('keydown', onKeyDown)
    },
  }
}

const KeybindingsContext = createContext<KeybindingsValue | null>(null)

export function KeybindingsProvider({ client, environment = detectEnvironment(), children }: { client?: ApiClient; environment?: Environment; children: ReactNode }) {
  const [report, setReport] = useState<{ orin: Validation; claude: Validation }>({ orin: empty, claude: empty })
  const [generation, setGeneration] = useState(0)
  useEffect(() => {
    if (!client) return
    let alive = true
    // The files live on the host: a shared server answers 404 and the defaults stay (never an error for the person).
    getKeybindingsFiles(client).then((files) => {
      if (!alive) return
      setReport({ orin: validateKeybindings(files.orin.text, environment), claude: validateKeybindings(files.claude.text, environment) })
    }).catch(() => undefined)
    return () => { alive = false }
  }, [client, environment, generation])
  const keymap = useMemo(
    () => resolveKeymap(environment, [report.claude.bindings, report.orin.bindings]),
    [environment, report],
  )
  const matcher = useMemo(() => createMatcher(keymap), [keymap])
  const [dispatcher] = useState(() => createDispatcher(matcher))
  useEffect(() => { dispatcher.setMatcher(matcher) }, [dispatcher, matcher])
  useEffect(() => dispatcher.attach(), [dispatcher])
  const register = useCallback((registration: Omit<Registration, 'id'>) => dispatcher.register(registration), [dispatcher])
  const value = useMemo<KeybindingsValue>(() => ({
    environment, keymap, report, register, match: matcher, reload: () => setGeneration((value) => value + 1),
  }), [environment, keymap, report, register, matcher])
  return <KeybindingsContext.Provider value={value}>{children}</KeybindingsContext.Provider>
}

// Without a provider (a surface rendered on its own, as in a test), the browser defaults still work: one shared
// dispatcher attaches its listener the first time something registers.
const fallback: KeybindingsValue = (() => {
  const keymap = defaultKeymap('browser')
  const match = createMatcher(keymap)
  const dispatcher = createDispatcher(match)
  let attached = false
  return {
    environment: 'browser', keymap, report: { orin: empty, claude: empty }, match, reload: () => undefined,
    register: (registration) => {
      if (!attached && typeof window !== 'undefined') { dispatcher.attach(); attached = true }
      return dispatcher.register(registration)
    },
  }
})()

export function useKeybindings(): KeybindingsValue {
  return useContext(KeybindingsContext) ?? fallback
}

/** Handle some actions of one context while `enabled`; a handler returning false lets the key through. */
export function useKeybindingHandlers(context: KeyContext, handlers: Record<string, Handler>, enabled = true) {
  const { register } = useKeybindings()
  const latest = useRef(handlers)
  useEffect(() => { latest.current = handlers })
  const actions = Object.keys(handlers).sort().join('|')
  useEffect(() => {
    if (!enabled) return
    const proxy: Record<string, Handler> = {}
    for (const action of actions.split('|').filter(Boolean)) proxy[action] = (event) => latest.current[action]?.(event)
    return register({ context, handlers: proxy })
  }, [context, actions, enabled, register])
}

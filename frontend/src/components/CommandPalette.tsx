import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'

export type PaletteCommand = {
  id: string
  label: string
  hint?: string
  group: string
  run: () => void
}

type CommandPaletteProps = {
  commands?: PaletteCommand[]
  conversations?: Array<{ conversation_id: string; title: string; state: string }>
}

/**
 * Navigation, as a keystroke instead of a permanent sidebar.
 *
 * Everything that would otherwise need a nav item lives here — conversations,
 * overview, providers, memories — which is what keeps the chat itself free of
 * administrative chrome.
 */
export function CommandPalette({ commands = [], conversations = [] }: CommandPaletteProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const reduced = useReducedMotion()

  const close = useCallback(() => { setOpen(false); setQuery(''); setActive(0) }, [])

  const items = useMemo<PaletteCommand[]>(() => [
    { id: 'new', label: 'Nova conversa', hint: 'Começar do zero', group: 'Ir para', run: () => navigate('/') },
    { id: 'providers', label: 'Providers e modelos', hint: 'Chaves e catálogo', group: 'Ir para', run: () => navigate('/providers') },
    { id: 'settings', label: 'Settings', hint: 'Configurações e gerenciamento', group: 'Ir para', run: () => navigate('/settings') },
    { id: 'memory', label: 'Memory', hint: 'Memórias globais', group: 'Ir para', run: () => navigate('/settings/memory') },
    { id: 'skills', label: 'Biblioteca de skills', hint: 'Procedimentos disponíveis', group: 'Ir para', run: () => navigate('/skills') },
    ...commands,
    ...conversations.map((item) => ({
      id: `chat:${item.conversation_id}`,
      label: item.title,
      hint: item.state,
      group: 'Conversas',
      run: () => navigate(`/chats/${encodeURIComponent(item.conversation_id)}`),
    })),
  ], [commands, conversations, navigate])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return items
    return items.filter((item) => `${item.label} ${item.hint ?? ''} ${item.group}`.toLowerCase().includes(needle))
  }, [items, query])

  // Keep the resting palette compact, but never make older conversations
  // unreachable: as soon as the person searches, every loaded conversation is
  // considered. The API currently returns the complete local history.
  const visible = useMemo(() => query.trim() ? filtered : filtered.slice(0, 32), [filtered, query])

  // Filtering can shrink the list under the highlighted row, so the effective
  // index is clamped while rendering rather than corrected by a follow-up state
  // update that would render the out-of-range value once first.
  const activeIndex = Math.min(active, Math.max(visible.length - 1, 0))

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setOpen((value) => !value)
        return
      }
      if (!open) return
      if (event.key === 'Escape') { event.preventDefault(); close(); return }
      if (event.key === 'ArrowDown') { event.preventDefault(); setActive(Math.min(activeIndex + 1, visible.length - 1)) }
      if (event.key === 'ArrowUp') { event.preventDefault(); setActive(Math.max(activeIndex - 1, 0)) }
      if (event.key === 'Enter') {
        event.preventDefault()
        const chosen = visible[activeIndex]
        if (chosen) { chosen.run(); close() }
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [open, visible, activeIndex, close])

  useEffect(() => { if (open) inputRef.current?.focus() }, [open])

  const grouped = useMemo(() => {
    const map = new Map<string, PaletteCommand[]>()
    for (const item of visible) {
      const list = map.get(item.group) ?? []
      list.push(item)
      map.set(item.group, list)
    }
    return [...map.entries()]
  }, [visible])

  return (
    <>
      <button type="button" className="palette-trigger" onClick={() => setOpen(true)} aria-label="Abrir navegação (Ctrl+K)">
        <span aria-hidden="true">⌘</span>K
      </button>
      {createPortal(
        <AnimatePresence>
          {open && (
            <motion.div
              className="palette-backdrop"
              role="dialog"
              aria-modal="true"
              aria-label="Navegação"
              initial={reduced ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.14 }}
              onClick={(event) => { if (event.target === event.currentTarget) close() }}
            >
              <motion.div
                className="palette"
                initial={reduced ? false : { opacity: 0, y: -12, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={reduced ? { opacity: 0 } : { opacity: 0, y: -8, scale: 0.98 }}
                transition={{ duration: 0.18, ease: [0.22, 0.61, 0.36, 1] }}
              >
                <input
                  ref={inputRef}
                  className="palette__input"
                  type="text"
                  value={query}
                  placeholder="Buscar conversas e destinos…"
                  aria-label="Buscar"
                  onChange={(event) => { setQuery(event.target.value); setActive(0) }}
                />
                <div className="palette__results">
                  {grouped.map(([group, entries]) => (
                    <section key={group}>
                      <p className="palette__group">{group}</p>
                      <ul>
                        {entries.map((item) => {
                          const index = visible.indexOf(item)
                          return (
                            <li key={item.id}>
                              <button
                                type="button"
                                className={index === activeIndex ? 'palette__item is-active' : 'palette__item'}
                                onMouseEnter={() => setActive(index)}
                                onClick={() => { item.run(); close() }}
                              >
                                <span>{item.label}</span>
                                {item.hint && <small>{item.hint}</small>}
                              </button>
                            </li>
                          )
                        })}
                      </ul>
                    </section>
                  ))}
                  {filtered.length === 0 && <p className="palette__empty">Nada encontrado para “{query}”.</p>}
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  )
}

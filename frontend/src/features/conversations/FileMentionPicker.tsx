import { useEffect, useMemo, useState } from 'react'

export type ActiveFileMention = {
  query: string
  startIndex: number
  endIndex: number
}

/** An agent the person can ask for with `@name` (Biblioteca › Agentes). */
export type MentionAgent = { name: string; description: string }

export type FileMentionPickerProps = {
  files: string[]
  /** Agents offered before the files; selecting one inserts `@name` as well. */
  agents?: MentionAgent[]
  query: string
  loading?: boolean
  onSelect: (token: string) => void
  onDismiss: () => void
}

type MentionItem = { kind: 'agent'; token: string; description: string } | { kind: 'file'; token: string }

/** The agents whose name matches the typed `@query` (an empty query lists them all). */
export function matchingAgents(agents: readonly MentionAgent[], query: string): MentionAgent[] {
  const needle = query.trim().toLowerCase()
  if (needle.includes('/') || needle.includes('\\') || needle.includes('.')) return []
  return agents
    .filter((agent) => !needle || agent.name.includes(needle))
    .sort((a, b) => Number(!a.name.startsWith(needle)) - Number(!b.name.startsWith(needle)) || a.name.localeCompare(b.name))
}

const MENTION_PREFIX_RE = /(?:^|[\s([{"'`])@([A-Za-z0-9_./\\-]*)$/
const MENTION_TAIL_RE = /^[A-Za-z0-9_./\\-]*/

/**
 * Locate an active `@path` token at the caret position (or at the end of the text).
 * Ignores email-like strings (`user@example.com`).
 */
export function detectFileMention(text: string, caretPosition?: number): ActiveFileMention | null {
  const pos = typeof caretPosition === 'number' && caretPosition >= 0
    ? Math.min(caretPosition, text.length)
    : text.length
  const before = text.slice(0, pos)
  const match = MENTION_PREFIX_RE.exec(before)
  if (!match) return null
  const query = match[1] ?? ''
  const startIndex = pos - query.length - 1
  const after = text.slice(pos)
  const tailMatch = MENTION_TAIL_RE.exec(after)
  const endIndex = pos + (tailMatch?.[0]?.length ?? 0)
  return { query, startIndex, endIndex }
}

/**
 * Replace the active `@query` token with `@<filePath> `.
 */
export function applyFileMention(text: string, mention: ActiveFileMention, filePath: string): string {
  const prefix = text.slice(0, mention.startIndex)
  const suffix = text.slice(mention.endIndex)
  const separator = suffix.startsWith(' ') ? '' : ' '
  return `${prefix}@${filePath}${separator}${suffix}`
}

/**
 * Popover menu that opens when the user types `@` anywhere in the Composer.
 * Supports keyboard navigation via ArrowUp, ArrowDown, Enter, Tab, and Escape.
 */
export function FileMentionPicker({
  files,
  agents = [],
  query,
  loading = false,
  onSelect,
  onDismiss,
}: FileMentionPickerProps) {
  const matches = useMemo<MentionItem[]>(() => {
    const needle = query.trim().replace(/\\/g, '/').replace(/^\/+/, '').toLowerCase()
    const filtered = needle
      ? files.filter((item) => item.toLowerCase().includes(needle))
      : files
    const agentItems: MentionItem[] = matchingAgents(agents, query).slice(0, 8)
      .map((agent) => ({ kind: 'agent', token: agent.name, description: agent.description }))
    const fileItems: MentionItem[] = filtered.map((token) => ({ kind: 'file', token }))
    return [...agentItems, ...fileItems].slice(0, 20)
  }, [files, agents, query])

  const [highlighted, setHighlighted] = useState(0)
  const [queryAtLastReset, setQueryAtLastReset] = useState(query)
  if (query !== queryAtLastReset) {
    setQueryAtLastReset(query)
    setHighlighted(0)
  }

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        onDismiss()
        return
      }
      if (matches.length === 0) return
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setHighlighted((index) => (index + 1) % matches.length)
      } else if (event.key === 'ArrowUp') {
        event.preventDefault()
        setHighlighted((index) => (index - 1 + matches.length) % matches.length)
      } else if (event.key === 'Enter' || (event.key === 'Tab' && !event.shiftKey)) {
        event.preventDefault()
        const target = matches[highlighted] ?? matches[0]
        if (target) onSelect(target.token)
      }
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [matches, highlighted, onSelect, onDismiss])

  if (matches.length === 0 && !loading) return null

  return (
    <ul
      className="command-picker file-mention-picker"
      role="listbox"
      aria-label={agents.length > 0 ? 'Agentes e arquivos do workspace' : 'Arquivos do workspace'}
      data-testid="file-mention-picker"
    >
      {loading && matches.length === 0 ? (
        <li className="command-picker__item file-mention-picker__empty" role="option" aria-selected={false}>
          <span className="command-picker__description">Buscando arquivos…</span>
        </li>
      ) : (
        matches.map((item, index) => {
          if (item.kind === 'agent') {
            return (
              <li
                key={`agent:${item.token}`}
                role="option"
                aria-selected={index === highlighted}
                className={`command-picker__item file-mention-picker__item${index === highlighted ? ' is-highlighted' : ''}`}
                data-testid={`mention-agent-${item.token}`}
                onMouseEnter={() => setHighlighted(index)}
                onMouseDown={(event) => {
                  event.preventDefault()
                  onSelect(item.token)
                }}
              >
                <span className="command-picker__name">@{item.token}</span>
                <span className="command-picker__kind">Agente</span>
                <span className="command-picker__description">{item.description}</span>
              </li>
            )
          }
          const filePath = item.token
          const parts = filePath.split('/')
          const basename = parts[parts.length - 1] ?? filePath
          const directory = parts.length > 1 ? parts.slice(0, -1).join('/') : ''
          return (
            <li
              key={filePath}
              role="option"
              aria-selected={index === highlighted}
              className={`command-picker__item file-mention-picker__item${index === highlighted ? ' is-highlighted' : ''}`}
              onMouseEnter={() => setHighlighted(index)}
              onMouseDown={(event) => {
                event.preventDefault()
                onSelect(filePath)
              }}
            >
              <span className="command-picker__name">@{filePath}</span>
              <span className="command-picker__kind">Arquivo</span>
              {directory && <span className="command-picker__hint">{directory}</span>}
              <span className="command-picker__description">{basename}</span>
            </li>
          )
        })
      )}
    </ul>
  )
}

import { useEffect, useMemo, useRef, useState } from 'react'
import type { ConversationActivityEvent } from './activityTypes'
import { useKeybindingHandlers } from '../keybindings/engine'

export type TranscriptMessage = { message_id: string; role: 'user' | 'assistant'; content: string; turn_id?: string | null }
export type TranscriptEntry = {
  id: string
  kind: 'user' | 'assistant' | 'thinking' | 'tool' | 'hook' | 'agent' | 'event'
  label: string
  body: string
  meta?: string
}

const SKIPPED = new Set([
  'assistant.delta', 'assistant.completed', 'context.updated', 'todo.updated', 'turn.started', 'turn.completed', 'tool.progressed',
])
export const TRANSCRIPT_CUT = 600
const STATE_LABEL: Record<string, string> = {
  completed: 'concluída', failed: 'falhou', cancelled: 'cancelada', queued: 'na fila', working: 'em andamento',
  waiting_tool: 'rodando', waiting_agent: 'esperando um agente', waiting_user: 'esperando você', retrying: 'tentando de novo',
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function hookEntry(event: ConversationActivityEvent): TranscriptEntry {
  const payload = event.payload ?? {}
  const name = text(payload.hook_event) || 'hook'
  const source = text(payload.source)
  const meta = [
    event.status ? `status: ${event.status}` : '',
    typeof payload.exit_code === 'number' ? `código de saída ${payload.exit_code}` : '',
    payload.blocked === true ? 'bloqueou' : '',
    typeof payload.duration_ms === 'number' ? `${payload.duration_ms} ms` : '',
  ].filter(Boolean).join(' · ')
  const reason = text(payload.reason)
  // A settings hook's summary only repeats the label and status ("Hook Stop (user): ok"); a plugin hook's is its output.
  const output = /^Hook \S+ \([^)]*\): \w+$/.test(event.summary) ? '' : event.summary
  return {
    id: event.eventId, kind: 'hook', label: `Hook ${name}${source ? ` (${source})` : ''}`,
    body: [output, reason && reason !== output ? `Motivo: ${reason}` : ''].filter(Boolean).join('\n'), meta,
  }
}

function toolEntry(event: ConversationActivityEvent): TranscriptEntry {
  const name = event.toolName ?? event.tool ?? 'ferramenta'
  const lines = [event.summary]
  if (event.command) lines.push(`$ ${event.command}`)
  else if (event.filePath ?? event.path) lines.push(String(event.filePath ?? event.path))
  if (event.outputTail) lines.push(event.outputTail)
  const meta = [STATE_LABEL[event.state] ?? '', typeof event.exitCode === 'number' ? `código de saída ${event.exitCode}` : ''].filter(Boolean).join(' · ')
  return { id: event.eventId, kind: 'tool', label: `Ferramenta ${name}`, body: lines.filter(Boolean).join('\n'), meta }
}

function eventEntry(event: ConversationActivityEvent): TranscriptEntry | null {
  if (event.type === 'plugin.hook_executed') return hookEntry(event)
  if (event.type.startsWith('tool.')) return toolEntry(event)
  if (event.type.startsWith('agent.') || event.type.startsWith('delegation.')) {
    const body = [event.summary, event.content ?? ''].filter(Boolean).join('\n')
    return body ? { id: event.eventId, kind: 'agent', label: event.agentName ? `Agente ${event.agentName}` : 'Agente', body } : null
  }
  return event.summary ? { id: event.eventId, kind: 'event', label: 'Evento', body: event.summary, meta: event.type } : null
}

/**
 * The detailed transcript (W7, `ctrl+o`): the messages with what happened between them — the model's reasoning, every
 * tool call (its last state), hooks with their outcome, subagents — in the order of each turn. Streamed thinking is
 * joined into one block per run; text and token counters of the stream itself are left out.
 */
export function buildTranscript(messages: readonly TranscriptMessage[], events: readonly ConversationActivityEvent[]): TranscriptEntry[] {
  const byTurn = new Map<string, TranscriptEntry[]>()
  const loose: TranscriptEntry[] = []
  const toolSlot = new Map<string, { list: TranscriptEntry[]; index: number }>()
  let thinking: { key: string; entry: TranscriptEntry } | null = null
  for (const event of events) {
    if (SKIPPED.has(event.type)) continue
    const list = event.turnId ? (byTurn.get(event.turnId) ?? byTurn.set(event.turnId, []).get(event.turnId)!) : loose
    if (event.type === 'agent.thought_delta') {
      const piece = event.content ?? event.delta ?? text(event.payload?.delta)
      const key = `${event.turnId ?? ''}:${event.agentId}`
      if (thinking && thinking.key === key && list[list.length - 1] === thinking.entry) {
        thinking.entry.body += piece
      } else {
        const entry: TranscriptEntry = { id: event.eventId, kind: 'thinking', label: event.parentAgentId && event.agentName ? `Raciocínio de ${event.agentName}` : 'Raciocínio', body: piece }
        list.push(entry)
        thinking = { key, entry }
      }
      continue
    }
    const entry = eventEntry(event)
    if (!entry) continue
    const invocation = event.type.startsWith('tool.') ? (event.invocationId ?? event.originEventId) : undefined
    const slot = invocation ? toolSlot.get(invocation) : undefined
    if (slot) { slot.list[slot.index] = { ...entry, id: slot.list[slot.index].id }; continue }
    list.push(entry)
    if (invocation) toolSlot.set(invocation, { list, index: list.length - 1 })
  }
  const out: TranscriptEntry[] = []
  const placed = new Set<string>()
  const place = (turnId: string | null | undefined) => {
    if (!turnId || placed.has(turnId)) return
    placed.add(turnId)
    out.push(...(byTurn.get(turnId) ?? []))
  }
  for (const message of messages) {
    if (message.role === 'assistant') place(message.turn_id)
    if (message.content.trim()) {
      out.push({ id: message.message_id, kind: message.role, label: message.role === 'user' ? 'Você' : 'Orin', body: message.content })
    }
    if (message.role === 'user') place(message.turn_id)
  }
  for (const turnId of byTurn.keys()) place(turnId)
  return [...out, ...loose].filter((entry) => entry.body.trim() || entry.meta)
}

export function TranscriptView({ messages, events, onClose }: {
  messages: readonly TranscriptMessage[]
  events: readonly ConversationActivityEvent[]
  onClose: () => void
}) {
  const [showAll, setShowAll] = useState(false)
  const entries = useMemo(() => buildTranscript(messages, events), [messages, events])
  const bodyRef = useRef<HTMLDivElement>(null)
  useKeybindingHandlers('Transcript', {
    'transcript:exit': () => onClose(),
    'transcript:toggleShowAll': () => setShowAll((value) => !value),
  })
  useEffect(() => { bodyRef.current?.focus() }, [])
  const cut = entries.some((entry) => entry.body.length > TRANSCRIPT_CUT)
  return (
    <div className="command-info__backdrop" role="presentation">
      <section className="command-info transcript-view" role="dialog" aria-modal="true" aria-labelledby="transcript-title">
        <header className="command-info__header">
          <h2 id="transcript-title">Transcrição detalhada</h2>
          <div className="transcript-view__actions">
            {cut && (
              <button type="button" className="ghost-button" aria-pressed={showAll} onClick={() => setShowAll((value) => !value)}>
                {showAll ? 'Encurtar' : 'Mostrar tudo'}
              </button>
            )}
            <button type="button" className="ghost-button" onClick={onClose}>Fechar</button>
          </div>
        </header>
        <div ref={bodyRef} className="command-info__body transcript-view__body" role="region" aria-label="Transcrição" tabIndex={0}>
          {entries.length === 0 && <p className="transcript-view__empty">A conversa ainda não tem nada para mostrar.</p>}
          <ol className="transcript-view__list">
            {entries.map((entry) => (
              <li key={entry.id} className={`transcript-view__entry transcript-view__entry--${entry.kind}`}>
                <p className="transcript-view__label">{entry.label}{entry.meta ? <span className="transcript-view__meta"> · {entry.meta}</span> : null}</p>
                <pre className="transcript-view__text">{showAll || entry.body.length <= TRANSCRIPT_CUT ? entry.body : `${entry.body.slice(0, TRANSCRIPT_CUT)}…`}</pre>
              </li>
            ))}
          </ol>
        </div>
      </section>
    </div>
  )
}

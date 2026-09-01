import { useState } from 'react'
import { createBrowserApiClient } from '../../api/client'
import { deleteManagedMemory } from '../../api/memory'
import type { ConversationActivityEvent } from './activityTypes'

/**
 * "Aprendi: …" — the one place a person finds out the agent kept something.
 *
 * It builds its own API client rather than receiving one: `renderActivityGroup`
 * already carries eight parameters, and undoing a memory needs nothing from the
 * conversation beyond what the event itself carries.
 */
export function MemoryLearnedCard({ event }: { event: ConversationActivityEvent }) {
  const [undone, setUndone] = useState(false)
  const [failed, setFailed] = useState(false)

  const undo = () => {
    if (!event.memoryId) return
    deleteManagedMemory(createBrowserApiClient(), event.memoryId, event.memoryScope ?? 'user', event.memoryProjectId)
      .then(() => { setUndone(true); setFailed(false) })
      .catch(() => setFailed(true))
  }

  return (
    <article className="activity-card memory-learned" data-state={undone ? 'cancelled' : 'completed'} data-kind="lifecycle">
      <span className="activity-card__glyph" aria-hidden="true">◈</span>
      <span className="activity-card__label">{undone ? 'Memória descartada' : event.summary}</span>
      {!undone && event.memoryId && (
        <button type="button" className="memory-learned__undo" onClick={undo}>Desfazer</button>
      )}
      {failed && <span role="alert" className="memory-learned__error">Não foi possível desfazer.</span>}
    </article>
  )
}

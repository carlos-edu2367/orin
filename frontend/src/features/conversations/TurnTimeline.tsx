import { AnimatePresence } from 'motion/react'
import { MarkdownMessage } from './MarkdownMessage'
import { renderActivityGroup } from './ActivityStream'
import type { ApiClient } from '../../api/client'
import { WorkspaceFileCard, type WorkspaceFilePreviewHandler } from './WorkspaceFileCard'
import type { TimelineItem } from './turnTimelineFold'

type TurnTimelineProps = {
  items: TimelineItem[]
  conversationId?: string
  client?: ApiClient
  onPreview?: WorkspaceFilePreviewHandler
}

/**
 * One assistant turn, told in order: narration and the action it triggered,
 * back to back, instead of the finished text followed by a replayed log.
 */
export function TurnTimeline({ items, conversationId, client, onPreview }: TurnTimelineProps) {
  return (
    <div className="turn-timeline">
      <AnimatePresence initial={false}>
        {items.map((item) => (
          <div key={item.id} className={`turn-timeline__${item.kind}`}>
            {item.kind === 'text'
              ? <MarkdownMessage content={item.content} conversationId={conversationId} client={client} onPreview={onPreview} />
              : item.group.kind === 'artifact' && item.group.events[0]?.path && conversationId
                ? <WorkspaceFileCard reference={{ conversationId, path: item.group.events[0].path as string }} client={client} onPreview={onPreview} />
                : renderActivityGroup(item.group)}
          </div>
        ))}
      </AnimatePresence>
    </div>
  )
}

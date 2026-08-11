import { AnimatePresence } from 'motion/react'
import { MarkdownMessage } from './MarkdownMessage'
import { renderActivityGroup } from './ActivityStream'
import type { TimelineItem } from './turnTimelineFold'

type TurnTimelineProps = {
  items: TimelineItem[]
}

/**
 * One assistant turn, told in order: narration and the action it triggered,
 * back to back, instead of the finished text followed by a replayed log.
 */
export function TurnTimeline({ items }: TurnTimelineProps) {
  return (
    <div className="turn-timeline">
      <AnimatePresence initial={false}>
        {items.map((item) => (
          <div key={item.id} className={`turn-timeline__${item.kind}`}>
            {item.kind === 'text' ? <MarkdownMessage content={item.content} /> : renderActivityGroup(item.group)}
          </div>
        ))}
      </AnimatePresence>
    </div>
  )
}

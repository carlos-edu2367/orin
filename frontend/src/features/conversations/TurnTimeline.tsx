import { AnimatePresence } from 'motion/react'
import { useState } from 'react'
import { MarkdownMessage } from './MarkdownMessage'
import { renderActivityGroup } from './ActivityStream'
import type { ApiClient } from '../../api/client'
import { rewindTurn } from '../../api/conversations'
import { WorkspaceFileCard, type WorkspaceFilePreviewHandler } from './WorkspaceFileCard'
import type { TimelineItem } from './turnTimelineFold'
import type { ConversationActivityEvent, ToolPermissionDecision } from './activityTypes'
import type { UserQuestionAnswer } from './UserQuestionCard'

type TurnTimelineProps = {
  items: TimelineItem[]
  conversationId?: string
  client?: ApiClient
  onPreview?: WorkspaceFilePreviewHandler
  openQuestionTurnIds?: ReadonlySet<string>
  onAnswer?: (event: ConversationActivityEvent, answers: UserQuestionAnswer[]) => Promise<void>
  onMcpApprove?: (event: ConversationActivityEvent, secrets: Record<string, string>) => Promise<void>
  onMcpDecline?: (event: ConversationActivityEvent) => Promise<void>
  onPluginApprove?: (event: ConversationActivityEvent) => Promise<void>
  onPluginDecline?: (event: ConversationActivityEvent) => Promise<void>
  onToolPermission?: (event: ConversationActivityEvent, decision: ToolPermissionDecision) => Promise<void>
  onRewindTurn?: (turnId: string) => Promise<void>
}

function turnModifiedTurnId(items: TimelineItem[]): string | null {
  for (const item of items) {
    if (item.kind !== 'activity') continue
    for (const event of item.group.events) {
      const modified =
        event.toolName === 'write_file' ||
        event.toolName === 'edit_file' ||
        event.kind === 'artifact' ||
        Boolean(event.structuredPatch && event.structuredPatch.length > 0)
      if (modified && event.turnId) return event.turnId
    }
  }
  return null
}

/**
 * One assistant turn, told in order: narration and the action it triggered,
 * back to back, instead of the finished text followed by a replayed log.
 */
export function TurnTimeline({
  items,
  conversationId,
  client,
  onPreview,
  openQuestionTurnIds,
  onAnswer,
  onMcpApprove,
  onMcpDecline,
  onPluginApprove,
  onPluginDecline,
  onToolPermission,
  onRewindTurn,
}: TurnTimelineProps) {
  const [rewinding, setRewinding] = useState(false)
  const [rewound, setRewound] = useState(false)
  const modifiedTurnId = turnModifiedTurnId(items)
  const canShowRewind = Boolean(modifiedTurnId && (onRewindTurn || (client && conversationId)))

  async function handleRewind() {
    if (!modifiedTurnId || rewinding || rewound) return
    setRewinding(true)
    try {
      if (onRewindTurn) {
        await onRewindTurn(modifiedTurnId)
      } else if (client && conversationId) {
        await rewindTurn(client, conversationId, modifiedTurnId)
      }
      setRewound(true)
    } finally {
      setRewinding(false)
    }
  }

  return (
    <div className="turn-timeline">
      <AnimatePresence initial={false}>
        {items.map((item) => (
          <div key={item.id} className={`turn-timeline__${item.kind}`}>
            {item.kind === 'text'
              ? <MarkdownMessage content={item.content} conversationId={conversationId} client={client} onPreview={onPreview} />
              : item.group.kind === 'artifact' && item.group.events[0]?.path && conversationId
                ? <WorkspaceFileCard reference={{ conversationId, path: item.group.events[0].path as string }} client={client} onPreview={onPreview} />
                : renderActivityGroup(item.group, openQuestionTurnIds, onAnswer, conversationId, onPreview, onMcpApprove, onMcpDecline, onPluginApprove, onPluginDecline, onToolPermission)}
          </div>
        ))}
      </AnimatePresence>
      {canShowRewind && (
        <div className="turn-timeline__rewind">
          <button
            type="button"
            className="ghost-button turn-timeline__rewind-button"
            data-testid="rewind-turn-button"
            disabled={rewinding || rewound}
            onClick={() => void handleRewind()}
          >
            {rewound ? 'Alterações revertidas' : rewinding ? 'Revertendo…' : 'Reverter alterações'}
          </button>
        </div>
      )}
    </div>
  )
}

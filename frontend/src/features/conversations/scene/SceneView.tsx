import { useMemo, useState, type JSX } from 'react'
import type { ConversationActivityEvent, TodoItem } from '../activityTypes'
import {
  deriveRecentActivities,
  deriveStepperData,
  resolveFinalDeliverable,
  resolvePendingDecision,
  type TeamFinalDeliverable,
  type TeamPendingDecision,
  type TeamRecentActivityEntry,
} from '../teamActivityModel'
import { SceneCanvas } from './SceneCanvas'
import { SceneHud, type SceneAgentControlResult } from './SceneHud'
import type { SceneActionHandlers } from './ScenePendingCards'
import {
  readSceneSoundPreference,
  writeSceneSoundPreference,
} from './sceneAudio'
import { foldScene } from './sceneReducer'
import './scene.css'

export type { SceneActionHandlers } from './ScenePendingCards'
export type { SceneAgentControlResult } from './SceneHud'

export interface SceneViewProps {
  events?: ConversationActivityEvent[]
  todos?: TodoItem[]
  actions?: SceneActionHandlers
  openQuestionTurnIds?: ReadonlySet<string>
  onSendToOrin?: (text: string) => Promise<void> | void
  onStopAgent?: (
    agentId: string,
  ) => Promise<SceneAgentControlResult | void> | SceneAgentControlResult | void
  onMessageAgent?: (
    agentId: string,
    message: string,
  ) => Promise<SceneAgentControlResult | void> | SceneAgentControlResult | void
  pendingDecision?: TeamPendingDecision | null
  finalDeliverable?: TeamFinalDeliverable | null
  recentActivities?: TeamRecentActivityEntry[]
  paused?: boolean
  running?: boolean
  completedStepsCount?: number
  totalStepsCount?: number
  onPauseTeam?: () => void
  onBackToChat?: () => void
  onResolveDecision?: (optionValue: string, decision?: TeamPendingDecision) => void
  onOpenDeliverable?: (deliverable: TeamFinalDeliverable) => void
  onViewAllActivities?: () => void
}

export function SceneView({
  events,
  todos,
  actions,
  openQuestionTurnIds,
  onSendToOrin,
  onStopAgent,
  onMessageAgent,
  pendingDecision,
  finalDeliverable,
  recentActivities,
  paused = false,
  running = false,
  completedStepsCount,
  totalStepsCount,
  onPauseTeam,
  onBackToChat,
  onResolveDecision,
  onOpenDeliverable,
  onViewAllActivities,
}: SceneViewProps): JSX.Element {
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [soundEnabled, setSoundEnabled] = useState<boolean>(() =>
    readSceneSoundPreference(),
  )

  const scene = useMemo(
    () => foldScene(events, { running }),
    [events, running],
  )

  // Drop selection automatically if the selected agent is no longer in the scene state
  const effectiveSelectedAgentId = useMemo(() => {
    if (!selectedAgentId) return null
    return scene.agents.some((ag) => ag.id === selectedAgentId)
      ? selectedAgentId
      : null
  }, [scene.agents, selectedAgentId])

  const stepperData = useMemo(
    () =>
      deriveStepperData({
        todos,
        completedStepsCount,
        totalStepsCount,
        running,
      }),
    [todos, completedStepsCount, totalStepsCount, running],
  )

  const resolvedDecision = useMemo<TeamPendingDecision | null>(
    () => resolvePendingDecision(pendingDecision, events),
    [pendingDecision, events],
  )

  const resolvedDeliverable = useMemo<TeamFinalDeliverable | null>(
    () => resolveFinalDeliverable(finalDeliverable, events),
    [finalDeliverable, events],
  )

  const resolvedRecentActivities = useMemo(
    () => deriveRecentActivities(events, recentActivities),
    [events, recentActivities],
  )

  const handleToggleSound = () => {
    setSoundEnabled((prev) => {
      const next = !prev
      writeSceneSoundPreference(next)
      return next
    })
  }

  return (
    <section
      className="scene-view"
      data-testid="scene-view"
      aria-label="Cena da equipe"
    >
      <div className="scene-view__layout">
        <div className="scene-view__stage">
          <SceneCanvas
            scene={scene}
            selectedAgentId={effectiveSelectedAgentId}
            onSelectAgent={(agentId) => setSelectedAgentId(agentId)}
            soundEnabled={soundEnabled}
          />
        </div>
        <div className="scene-view__hud">
          <SceneHud
            scene={scene}
            events={events}
            actions={actions}
            openQuestionTurnIds={openQuestionTurnIds}
            onSendToOrin={onSendToOrin}
            onStopAgent={onStopAgent}
            onMessageAgent={onMessageAgent}
            selectedAgentId={effectiveSelectedAgentId}
            onSelectAgent={setSelectedAgentId}
            paused={paused}
            soundEnabled={soundEnabled}
            onToggleSound={handleToggleSound}
            stepperData={stepperData}
            pendingDecision={resolvedDecision}
            finalDeliverable={resolvedDeliverable}
            recentActivities={resolvedRecentActivities}
            onPauseTeam={onPauseTeam}
            onBackToChat={onBackToChat}
            onResolveDecision={onResolveDecision}
            onOpenDeliverable={onOpenDeliverable}
            onViewAllActivities={onViewAllActivities}
          />
        </div>
      </div>
    </section>
  )
}

export default SceneView

import type { OrchestrationSettingItem } from '../../api/orchestrationSettings'
import { OrchestrationSettingsForm } from './OrchestrationSettingsForm'

const AGENT_MESSAGE_PATHS: readonly string[] = [
  'orchestration.agentMessages.parentTool',
  'orchestration.agentMessages.continueOnUnread',
]

export function AgentMessagesSettingsBlock({
  items,
  busy = false,
  onSave,
}: {
  items: OrchestrationSettingItem[]
  busy?: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const ordered = AGENT_MESSAGE_PATHS.map((path) => items.find((item) => item.path === path)).filter(
    (item): item is OrchestrationSettingItem => Boolean(item),
  )
  const extras = items.filter((item) => !AGENT_MESSAGE_PATHS.includes(item.path))
  const visibleItems = [...ordered, ...extras]

  if (visibleItems.length === 0) {
    return (
      <p className="code-settings__muted">
        Nenhuma chave de mensagens a subagentes disponível nesta instância.
      </p>
    )
  }

  return (
    <OrchestrationSettingsForm
      items={visibleItems}
      busy={busy}
      onSave={onSave}
      hideGroupHeaders
    />
  )
}

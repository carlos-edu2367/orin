import type { OrchestrationSettingItem } from '../../api/orchestrationSettings'
import { OrchestrationSettingsForm } from './OrchestrationSettingsForm'

const BACKGROUND_PATHS: readonly string[] = [
  'orchestration.background.autoResume',
  'orchestration.stall.minutes',
  'orchestration.stall.autoCancel',
]

export function BackgroundSettingsBlock({
  items,
  busy = false,
  onSave,
}: {
  items: OrchestrationSettingItem[]
  busy?: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const ordered = BACKGROUND_PATHS.map((path) => items.find((item) => item.path === path)).filter(
    (item): item is OrchestrationSettingItem => Boolean(item),
  )
  const extras = items.filter((item) => !BACKGROUND_PATHS.includes(item.path))
  const visibleItems = [...ordered, ...extras]

  if (visibleItems.length === 0) {
    return (
      <p className="code-settings__muted">
        Nenhuma chave de agentes em segundo plano disponível nesta instância.
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

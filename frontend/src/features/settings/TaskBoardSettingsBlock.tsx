import type { OrchestrationSettingItem } from '../../api/orchestrationSettings'
import { OrchestrationSettingsForm } from './OrchestrationSettingsForm'

const TASK_BOARD_PATHS: readonly string[] = ['orchestration.taskBoard.enabled']

export function TaskBoardSettingsBlock({
  items,
  busy = false,
  onSave,
}: {
  items: OrchestrationSettingItem[]
  busy?: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const ordered = TASK_BOARD_PATHS.map((path) => items.find((item) => item.path === path)).filter(
    (item): item is OrchestrationSettingItem => Boolean(item),
  )
  const extras = items.filter((item) => !TASK_BOARD_PATHS.includes(item.path))
  const visibleItems = [...ordered, ...extras]

  if (visibleItems.length === 0) {
    return (
      <p className="code-settings__muted">
        Nenhuma chave de quadro de tarefas compartilhado disponível nesta instância.
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

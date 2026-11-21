import type { OrchestrationSettingItem } from '../../api/orchestrationSettings'
import { OrchestrationSettingsForm } from './OrchestrationSettingsForm'

const WORKTREE_PATHS: readonly string[] = [
  'orchestration.worktree.enabled',
  'orchestration.worktree.linkDirs',
  'orchestration.worktree.mergeCheckCommand',
]

export function WorktreeSettingsBlock({
  items,
  busy = false,
  trusted = true,
  projectRoot = null,
  onSave,
}: {
  items: OrchestrationSettingItem[]
  busy?: boolean
  trusted?: boolean
  projectRoot?: string | null
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const ordered = WORKTREE_PATHS.map((path) => items.find((item) => item.path === path)).filter(
    (item): item is OrchestrationSettingItem => Boolean(item),
  )
  const extras = items.filter((item) => !WORKTREE_PATHS.includes(item.path))
  const visibleItems = [...ordered, ...extras]

  if (visibleItems.length === 0) {
    return (
      <p className="code-settings__muted">
        Nenhuma chave de worktree por subagente disponível nesta instância.
      </p>
    )
  }

  return (
    <div className="orch-settings__block">
      {projectRoot && !trusted && (
        <p
          className="orch-settings__warning"
          role="note"
          data-testid="worktree-untrusted-warning"
        >
          Repositório não confiável: o comando de checagem da integração (<code>mergeCheckCommand</code>) é ignorado até que esta pasta seja marcada como confiável.
        </p>
      )}
      <OrchestrationSettingsForm
        items={visibleItems}
        busy={busy}
        onSave={onSave}
        hideGroupHeaders
      />
    </div>
  )
}

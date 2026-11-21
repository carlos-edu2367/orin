import { useMemo } from 'react'
import type {
  OrchestrationSettingItem,
  OrchestrationSettingLayer,
} from '../../api/orchestrationSettings'
import './orchestrationSettings.css'

const LAYER_LABELS: Record<OrchestrationSettingLayer, string> = {
  default: 'Padrão',
  user: 'Usuário',
  project: 'Projeto',
  local: 'Local',
  cli: 'Linha de comando',
  managed: 'Gerenciado',
}

const CHOICE_LABELS: Record<string, string> = {
  model_default: 'Padrão do modelo',
  inherit: 'Herdar do pai',
  by_type: 'Por tipo de subagente',
  low: 'Baixo (low)',
  medium: 'Médio (medium)',
  high: 'Alto (high)',
  xhigh: 'Muito alto (xhigh)',
  max: 'Máximo (max)',
  ultra: 'Ultra (ultra)',
}

const SUBAGENT_TYPES: ReadonlyArray<{ key: string; label: string; hint: string }> = [
  { key: 'explore', label: 'Explorar (explore)', hint: 'Leitura e busca rápida no repositório' },
  { key: 'plan', label: 'Planejar (plan)', hint: 'Análise arquitetural e desenho de plano' },
  { key: 'verifier', label: 'Verificador (verifier)', hint: 'Revisão independente e checagem de evidências' },
  { key: 'general', label: 'Propósito geral (general)', hint: 'Execução autônoma de subtarefas completas' },
  { key: 'fork', label: 'Ramificação (fork)', hint: 'Subagente que herda o histórico da conversa' },
]

const ORDERED_PATHS = [
  'effortLevel',
  'effort.planMode',
  'effort.subagentPolicy',
  'effort.byType',
  'effort.maxForSubagents',
  'effort.boostKeyword',
  'effort.escalateOnFailure',
] as const

function choiceLabel(choice: string): string {
  return CHOICE_LABELS[choice] ?? choice
}

function controlIdFor(path: string): string {
  return `effort-setting-control-${path.replace(/[^A-Za-z0-9_-]/g, '-')}`
}

function RowHeader({
  item,
  inputId,
  busy,
  onSave,
}: {
  item: OrchestrationSettingItem
  inputId?: string
  busy: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const canReset = item.hasUserOverride && !item.locked
  return (
    <div className="orch-settings__row-head">
      <div className="orch-settings__meta">
        {inputId ? (
          <label className="orch-settings__label" htmlFor={inputId}>
            {item.label}
          </label>
        ) : (
          <span className="orch-settings__label">{item.label}</span>
        )}
        {item.description && <p className="orch-settings__description">{item.description}</p>}
        <span className="orch-settings__path">{item.path}</span>
      </div>
      <div className="orch-settings__badges">
        <span
          className={
            item.layer === 'managed'
              ? 'code-settings__layer code-settings__layer--managed'
              : 'code-settings__layer'
          }
        >
          {LAYER_LABELS[item.layer]}
        </span>
        {canReset && (
          <button
            type="button"
            className="button button--quiet orch-settings__reset"
            disabled={busy}
            onClick={() => void onSave({ [item.path]: null })}
          >
            Restaurar padrão
          </button>
        )}
      </div>
    </div>
  )
}

function ByTypeEditor({
  item,
  isActivePolicy,
  busy,
  onSave,
}: {
  item: OrchestrationSettingItem
  isActivePolicy: boolean
  busy: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const disabled = busy || item.locked
  const rawMap =
    typeof item.value === 'object' && item.value !== null && !Array.isArray(item.value)
      ? (item.value as Record<string, unknown>)
      : {}
  const defaultMap =
    typeof item.defaultValue === 'object' && item.defaultValue !== null && !Array.isArray(item.defaultValue)
      ? (item.defaultValue as Record<string, unknown>)
      : {}
  const choices = item.choices ?? ['inherit', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra']

  const currentMap: Record<string, string> = {}
  for (const { key } of SUBAGENT_TYPES) {
    const val = rawMap[key] ?? defaultMap[key] ?? 'inherit'
    currentMap[key] = typeof val === 'string' ? val : 'inherit'
  }
  for (const [k, v] of Object.entries(rawMap)) {
    if (typeof v === 'string' && !(k in currentMap)) {
      currentMap[k] = v
    }
  }

  function updateType(subagentType: string, nextValue: string) {
    if (disabled) return
    void onSave({
      [item.path]: {
        ...currentMap,
        [subagentType]: nextValue,
      },
    })
  }

  return (
    <div
      className={`orch-settings__row effort-panel__by-type${isActivePolicy ? ' is-highlighted' : ' is-inactive'}${item.locked ? ' is-locked' : ''}`}
      data-testid="effort-by-type-editor"
      data-active={isActivePolicy ? 'true' : 'false'}
    >
      <RowHeader item={item} busy={busy} onSave={onSave} />
      {!isActivePolicy && (
        <p className="effort-panel__by-type-hint">
          Esta tabela fica ativa quando &ldquo;Esforço dos subagentes&rdquo; estiver em{' '}
          <strong>Por tipo de subagente</strong>.
        </p>
      )}
      <div className="effort-panel__by-type-grid">
        {SUBAGENT_TYPES.map((entry) => {
          const selectId = `effort-by-type-${entry.key}`
          return (
            <div key={entry.key} className="effort-panel__by-type-row">
              <div className="effort-panel__by-type-meta">
                <label htmlFor={selectId} className="effort-panel__by-type-label">
                  Esforço para {entry.label}
                </label>
                <span className="effort-panel__by-type-desc">{entry.hint}</span>
              </div>
              <select
                id={selectId}
                className="effort-panel__select"
                value={currentMap[entry.key] ?? 'inherit'}
                disabled={disabled}
                onChange={(event) => updateType(entry.key, event.target.value)}
              >
                {choices.map((choice) => (
                  <option key={choice} value={choice}>
                    {choiceLabel(choice)}
                  </option>
                ))}
              </select>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function EffortByModelRow({
  item,
  busy,
  onSave,
}: {
  item: OrchestrationSettingItem
  busy: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const disabled = busy || item.locked
  const rawMap =
    typeof item.value === 'object' && item.value !== null && !Array.isArray(item.value)
      ? (item.value as Record<string, unknown>)
      : {}
  const entries = Object.entries(rawMap).filter(([, val]) => typeof val === 'string') as Array<[string, string]>

  if (entries.length === 0) return null

  function removeModel(modelKey: string) {
    if (disabled) return
    const next: Record<string, string> = {}
    for (const [k, v] of entries) {
      if (k !== modelKey) next[k] = v
    }
    void onSave({ [item.path]: next })
  }

  return (
    <div
      className={`orch-settings__row${item.locked ? ' is-locked' : ''}`}
      data-testid={`effort-setting-${item.path}`}
    >
      <RowHeader item={item} busy={busy} onSave={onSave} />
      <ul className="orch-settings__items">
        {entries.map(([modelKey, level]) => (
          <li key={modelKey} className="orch-settings__item">
            <span>
              <code>{modelKey}</code> = <strong>{choiceLabel(level)}</strong>
            </span>
            <button
              type="button"
              className="button button--quiet"
              disabled={disabled}
              onClick={() => removeModel(modelKey)}
            >
              Remover
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function EffortSettingsPanel({
  items,
  onSave,
  busy = false,
}: {
  items: OrchestrationSettingItem[]
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
  busy?: boolean
}) {
  const byPath = useMemo(() => new Map(items.map((item) => [item.path, item])), [items])

  const subagentPolicyItem = byPath.get('effort.subagentPolicy')
  const isByTypeActive = subagentPolicyItem?.value === 'by_type'

  const orderedItems = useMemo(() => {
    const result: OrchestrationSettingItem[] = []
    const seen = new Set<string>()
    for (const path of ORDERED_PATHS) {
      const found = byPath.get(path)
      if (found) {
        result.push(found)
        seen.add(path)
      }
    }
    for (const item of items) {
      if (!seen.has(item.path) && item.path !== 'effortByModel') {
        result.push(item)
      }
    }
    return result
  }, [byPath, items])

  const effortByModelItem = byPath.get('effortByModel')

  return (
    <div className="orch-settings effort-panel" data-testid="effort-settings-panel">
      <div className="orch-settings__list">
        {orderedItems.map((item) => {
          const disabled = busy || item.locked
          const inputId = controlIdFor(item.path)

          if (item.path === 'effort.byType') {
            return (
              <ByTypeEditor
                key={item.path}
                item={item}
                isActivePolicy={isByTypeActive}
                busy={busy}
                onSave={onSave}
              />
            )
          }

          return (
            <div
              key={item.path}
              className={`orch-settings__row${item.locked ? ' is-locked' : ''}`}
              data-testid={`effort-setting-${item.path}`}
            >
              <RowHeader item={item} inputId={inputId} busy={busy} onSave={onSave} />

              {item.type === 'boolean' && (
                <div className="orch-settings__switch">
                  <input
                    id={inputId}
                    type="checkbox"
                    role="switch"
                    aria-checked={Boolean(item.value)}
                    checked={Boolean(item.value)}
                    disabled={disabled}
                    onChange={(event) => void onSave({ [item.path]: event.target.checked })}
                  />
                  <span aria-hidden="true">{item.value ? 'Ligado' : 'Desligado'}</span>
                </div>
              )}

              {item.type === 'choice' && (
                <div className="orch-settings__control">
                  <select
                    id={inputId}
                    value={typeof item.value === 'string' ? item.value : ''}
                    disabled={disabled}
                    onChange={(event) => {
                      const nextValue = event.target.value
                      void onSave({ [item.path]: nextValue === '' ? null : nextValue })
                    }}
                  >
                    {item.defaultValue === null && (
                      <option value="">
                        {item.path === 'effort.maxForSubagents'
                          ? 'Sem teto'
                          : item.path === 'effort.planMode'
                            ? 'Herdar esforço atual'
                            : 'Padrão do modelo'}
                      </option>
                    )}
                    {(item.choices ?? []).map((choice) => (
                      <option key={choice} value={choice}>
                        {choiceLabel(choice)}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
          )
        })}

        {effortByModelItem && (
          <EffortByModelRow item={effortByModelItem} busy={busy} onSave={onSave} />
        )}
      </div>
    </div>
  )
}

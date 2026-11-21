import { useCallback, useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import {
  getOrchestrationSettings,
  updateOrchestrationSettings,
  type OrchestrationSettingItem,
  type OrchestrationSettingLayer,
} from '../../api/orchestrationSettings'
import '../settings/orchestrationSettings.css'
import './memoryReview.css'

const LAYER_LABELS: Record<OrchestrationSettingLayer, string> = {
  default: 'Padrão',
  user: 'Usuário',
  project: 'Projeto',
  local: 'Local',
  cli: 'Linha de comando',
  managed: 'Gerenciado',
}

const ORDERED_MEMORY_PATHS = [
  'memory.agent.enabled',
  'memory.agent.maxPromptChars',
  'memory.agent.learnFromVerifiedOnly',
  'memory.quarantine.enabled',
  'memory.conflicts.llmAdjudication',
  'memory.instructionLint.enabled',
  'memory.tombstoneRetentionDays',
] as const

function controlIdFor(path: string): string {
  return `memory-setting-${path.replace(/[^A-Za-z0-9_-]/g, '-')}`
}

function NumberSettingRow({
  item,
  busy,
  onSave,
}: {
  item: OrchestrationSettingItem
  busy: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const numericValue = typeof item.value === 'number' ? item.value : Number(item.defaultValue ?? 0)
  const [draft, setDraft] = useState<string>(String(numericValue))
  const [prevRemote, setPrevRemote] = useState<number>(numericValue)

  if (prevRemote !== numericValue) {
    setPrevRemote(numericValue)
    setDraft(String(numericValue))
  }

  const inputId = controlIdFor(item.path)
  const disabled = busy || item.locked
  const parsed = Number(draft)
  const isValid =
    draft.trim() !== '' &&
    Number.isFinite(parsed) &&
    Number.isInteger(parsed) &&
    (item.minimum === null || parsed >= item.minimum) &&
    (item.maximum === null || parsed <= item.maximum)
  const isDirty = isValid && parsed !== numericValue

  return (
    <div
      className={`orch-settings__row${item.locked ? ' is-locked' : ''}`}
      data-testid={`memory-setting-row-${item.path}`}
    >
      <div className="orch-settings__row-head">
        <div className="orch-settings__meta">
          <label className="orch-settings__label" htmlFor={inputId}>
            {item.label}
          </label>
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
            data-testid={`memory-setting-layer-${item.path}`}
          >
            {LAYER_LABELS[item.layer]}
          </span>
          {item.hasUserOverride && !item.locked && (
            <button
              type="button"
              className="button button--quiet orch-settings__reset"
              data-testid={`memory-setting-reset-${item.path}`}
              disabled={busy}
              onClick={() => void onSave({ [item.path]: null })}
            >
              Restaurar padrão
            </button>
          )}
        </div>
      </div>

      <div className="orch-settings__control">
        <input
          id={inputId}
          type="number"
          className="orch-settings__input"
          min={item.minimum ?? undefined}
          max={item.maximum ?? undefined}
          step={1}
          value={draft}
          disabled={disabled}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button
          type="button"
          className="button button--primary"
          disabled={disabled || !isDirty}
          onClick={() => {
            if (isValid) void onSave({ [item.path]: parsed })
          }}
        >
          Aplicar
        </button>
      </div>
    </div>
  )
}

function BooleanSettingRow({
  item,
  busy,
  onSave,
}: {
  item: OrchestrationSettingItem
  busy: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const inputId = controlIdFor(item.path)
  const disabled = busy || item.locked
  const checked = Boolean(item.value)
  const showManualApprovalWarning = item.path === 'memory.agent.learnFromVerifiedOnly'

  return (
    <div
      className={`orch-settings__row${item.locked ? ' is-locked' : ''}`}
      data-testid={`memory-setting-row-${item.path}`}
    >
      <div className="orch-settings__row-head">
        <div className="orch-settings__meta">
          <label className="orch-settings__label" htmlFor={inputId}>
            {item.label}
          </label>
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
            data-testid={`memory-setting-layer-${item.path}`}
          >
            {LAYER_LABELS[item.layer]}
          </span>
          {item.hasUserOverride && !item.locked && (
            <button
              type="button"
              className="button button--quiet orch-settings__reset"
              data-testid={`memory-setting-reset-${item.path}`}
              disabled={busy}
              onClick={() => void onSave({ [item.path]: null })}
            >
              Restaurar padrão
            </button>
          )}
        </div>
      </div>

      <div className="orch-settings__control">
        <label className="orch-settings__toggle" htmlFor={inputId}>
          <input
            id={inputId}
            type="checkbox"
            checked={checked}
            disabled={disabled}
            onChange={(event) => void onSave({ [item.path]: event.target.checked })}
          />
          <span>{checked ? 'Ligado' : 'Desligado'}</span>
        </label>
      </div>

      {showManualApprovalWarning && (
        <p
          className="memory-settings-panel__notice"
          data-testid="memory-verified-only-warning"
        >
          Quando a auto-verificação e o verificador independente estiverem desligados, não há
          veredito automático: as propostas de subagentes ficam na aba &ldquo;Para revisar&rdquo;
          aguardando aprovação manual (e expiram após 14 dias se não forem aprovadas).
        </p>
      )}
    </div>
  )
}

export type MemorySettingsPanelProps = {
  client?: ApiClient
  items?: OrchestrationSettingItem[]
  busy?: boolean
  onSave?: (changes: Record<string, unknown>) => Promise<void> | void
}

export function MemorySettingsPanel({
  client: providedClient,
  items: controlledItems,
  busy: controlledBusy,
  onSave: controlledOnSave,
}: MemorySettingsPanelProps) {
  const client = useMemo(() => providedClient ?? createBrowserApiClient(), [providedClient])
  const [loadedItems, setLoadedItems] = useState<OrchestrationSettingItem[]>([])
  const [loaded, setLoaded] = useState<boolean>(Boolean(controlledItems))
  const [saving, setSaving] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const loading = !controlledItems && !loaded

  useEffect(() => {
    if (controlledItems) return
    let active = true
    getOrchestrationSettings(client, { group: 'memory' })
      .then((report) => {
        if (!active) return
        setLoadedItems(report.items)
        setError(null)
      })
      .catch(() => {
        if (active) setError('Não foi possível carregar as configurações de memória.')
      })
      .finally(() => {
        if (active) setLoaded(true)
      })
    return () => {
      active = false
    }
  }, [client, controlledItems])

  const handleSave = useCallback(
    async (changes: Record<string, unknown>) => {
      if (controlledOnSave) {
        await controlledOnSave(changes)
        return
      }
      setSaving(true)
      setError(null)
      setNotice(null)
      try {
        const updated = await updateOrchestrationSettings(client, changes, { group: 'memory' })
        setLoadedItems(updated.items)
        setNotice('Configuração de memória salva na camada do usuário.')
      } catch {
        setError('Não foi possível salvar a configuração de memória.')
      } finally {
        setSaving(false)
      }
    },
    [client, controlledOnSave],
  )

  const orderedItems = useMemo(() => {
    const source = controlledItems ?? loadedItems
    const byPath = new Map(source.map((item) => [item.path, item]))
    const result: OrchestrationSettingItem[] = []
    for (const path of ORDERED_MEMORY_PATHS) {
      const found = byPath.get(path)
      if (found) result.push(found)
    }
    for (const item of source) {
      if (!ORDERED_MEMORY_PATHS.includes(item.path as (typeof ORDERED_MEMORY_PATHS)[number])) {
        result.push(item)
      }
    }
    return result
  }, [controlledItems, loadedItems])

  const busy = controlledBusy ?? saving

  return (
    <section
      className="memory-settings-panel"
      data-testid="memory-settings-panel"
      aria-label="Configurações de memória"
    >
      <header className="memory-settings-panel__header">
        <h2>Configurações de memória</h2>
        <p>
          Controle quarentena, resolução de conflitos, memória própria por agente, verificação de
          instruções e retenção de tombstones.
        </p>
      </header>

      {error && (
        <p role="alert" data-testid="memory-settings-error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="orch-settings__status" data-testid="memory-settings-notice">
          {notice}
        </p>
      )}
      {loading && orderedItems.length === 0 && (
        <p className="code-settings__muted">Carregando configurações de memória...</p>
      )}

      {orderedItems.length > 0 && (
        <div className="orch-settings__list">
          {orderedItems.map((item) =>
            item.type === 'number' ? (
              <NumberSettingRow key={item.path} item={item} busy={busy} onSave={handleSave} />
            ) : (
              <BooleanSettingRow key={item.path} item={item} busy={busy} onSave={handleSave} />
            ),
          )}
        </div>
      )}
    </section>
  )
}

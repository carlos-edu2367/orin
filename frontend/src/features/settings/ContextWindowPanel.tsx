import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import type { ApiClient } from '../../api/client'
import {
  describeWindowSource,
  forgetLearnedContextWindow,
  getContextWindows,
  validateContextOverride,
  type ContextWindowEntry,
} from '../../api/contextWindows'
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

function formatObservedDate(iso: string): string {
  if (!iso) return ''
  const parsed = new Date(iso)
  if (!Number.isFinite(parsed.getTime())) return iso
  return parsed.toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function readOverridesMap(item: OrchestrationSettingItem | undefined): Record<string, number> {
  if (!item || typeof item.value !== 'object' || item.value === null || Array.isArray(item.value)) {
    return {}
  }
  const out: Record<string, number> = {}
  for (const [key, val] of Object.entries(item.value as Record<string, unknown>)) {
    if (typeof val === 'number' && Number.isFinite(val)) {
      out[key] = Math.round(val)
    }
  }
  return out
}

export type ContextWindowPanelProps = {
  client?: ApiClient
  projectRoot?: string | null
  items: OrchestrationSettingItem[]
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
  busy?: boolean
  initialWindows?: ContextWindowEntry[]
}

export function ContextWindowPanel({
  client,
  projectRoot = null,
  items,
  onSave,
  busy = false,
  initialWindows,
}: ContextWindowPanelProps) {
  const [windows, setWindows] = useState<ContextWindowEntry[]>(initialWindows ?? [])
  const [loadingWindows, setLoadingWindows] = useState(Boolean(client && !initialWindows))
  const [forgettingKey, setForgettingKey] = useState<string | null>(null)
  const [overrideDrafts, setOverrideDrafts] = useState<Record<string, string>>({})
  const [customModelKey, setCustomModelKey] = useState('')
  const [customLimit, setCustomLimit] = useState('')
  const [validationError, setValidationError] = useState<string | null>(null)

  const byPath = useMemo(() => new Map(items.map((item) => [item.path, item])), [items])
  const overridesItem = byPath.get('context.windowOverrides')
  const longCreditsItem = byPath.get('claudeCli.longContextWithCredits')
  const overridesMap = useMemo(() => readOverridesMap(overridesItem), [overridesItem])

  const loadWindows = useCallback(
    async (signal?: AbortSignal) => {
      if (!client) return
      setLoadingWindows(true)
      try {
        const report = await getContextWindows(client, { projectRoot, signal })
        if (!signal?.aborted) {
          setWindows(report.windows)
        }
      } catch {
        // Keep current table gracefully when route is unavailable.
      } finally {
        if (!signal?.aborted) setLoadingWindows(false)
      }
    },
    [client, projectRoot],
  )

  useEffect(() => {
    if (!client) return
    const controller = new AbortController()
    getContextWindows(client, { projectRoot, signal: controller.signal })
      .then((report) => {
        if (!controller.signal.aborted) {
          setWindows(report.windows)
        }
      })
      .catch(() => {
        // Keep current table gracefully when route is unavailable.
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingWindows(false)
      })
    return () => controller.abort()
  }, [client, projectRoot])

  const overridesLocked = Boolean(overridesItem?.locked)
  const overridesDisabled = busy || overridesLocked

  async function saveOverrideForKey(modelKey: string, rawLimit: string) {
    if (overridesDisabled) return
    const cleanKey = modelKey.trim()
    if (!cleanKey) {
      setValidationError('Informe o identificador provider/modelo para o override manual.')
      return
    }
    const { value, error } = validateContextOverride(rawLimit)
    if (error || value === null) {
      setValidationError(error ?? 'O limite manual deve ficar entre 50.000 e 1.000.000 tokens.')
      return
    }
    setValidationError(null)
    const nextOverrides: Record<string, number> = { ...overridesMap, [cleanKey]: value }
    await onSave({ 'context.windowOverrides': nextOverrides })
    if (client) {
      void loadWindows()
    }
  }

  async function removeOverrideForKey(modelKey: string) {
    if (overridesDisabled) return
    setValidationError(null)
    const nextOverrides: Record<string, number> = { ...overridesMap }
    delete nextOverrides[modelKey]
    await onSave({ 'context.windowOverrides': nextOverrides })
    setOverrideDrafts((prev) => {
      const next = { ...prev }
      delete next[modelKey]
      return next
    })
    if (client) {
      void loadWindows()
    }
  }

  async function handleForgetLearned(entry: ContextWindowEntry) {
    if (!client) return
    const key = `${entry.provider}/${entry.model_id}`
    setForgettingKey(key)
    setValidationError(null)
    try {
      await forgetLearnedContextWindow(client, entry.provider, entry.model_id)
      await loadWindows()
    } finally {
      setForgettingKey(null)
    }
  }

  function handleCustomSubmit(event: FormEvent) {
    event.preventDefault()
    void saveOverrideForKey(customModelKey, customLimit).then(() => {
      const { error } = validateContextOverride(customLimit)
      if (!error && customModelKey.trim()) {
        setCustomModelKey('')
        setCustomLimit('')
      }
    })
  }

  return (
    <div className="context-window-panel" data-testid="context-window-panel">
      {longCreditsItem && (
        <div
          className={`orch-settings__row${longCreditsItem.locked ? ' is-locked' : ''}`}
          data-testid="context-setting-claudeCli.longContextWithCredits"
        >
          <div className="orch-settings__row-head">
            <div className="orch-settings__meta">
              <label
                className="orch-settings__label"
                htmlFor="context-setting-long-credits"
              >
                {longCreditsItem.label}
              </label>
              {longCreditsItem.description && (
                <p className="orch-settings__description">{longCreditsItem.description}</p>
              )}
              <span className="context-window-panel__credits-warning">
                Gasta créditos pagos
              </span>
              <span className="orch-settings__path">{longCreditsItem.path}</span>
            </div>
            <div className="orch-settings__badges">
              <span
                className={
                  longCreditsItem.layer === 'managed'
                    ? 'code-settings__layer code-settings__layer--managed'
                    : 'code-settings__layer'
                }
              >
                {LAYER_LABELS[longCreditsItem.layer]}
              </span>
              {longCreditsItem.hasUserOverride && !longCreditsItem.locked && (
                <button
                  type="button"
                  className="button button--quiet orch-settings__reset"
                  disabled={busy}
                  onClick={() => void onSave({ [longCreditsItem.path]: null })}
                >
                  Restaurar padrão
                </button>
              )}
            </div>
          </div>

          <div className="orch-settings__switch">
            <input
              id="context-setting-long-credits"
              type="checkbox"
              role="switch"
              aria-checked={Boolean(longCreditsItem.value)}
              checked={Boolean(longCreditsItem.value)}
              disabled={busy || longCreditsItem.locked}
              onChange={(event) =>
                void onSave({ [longCreditsItem.path]: event.target.checked })
              }
            />
            <span aria-hidden="true">{longCreditsItem.value ? 'Ligado' : 'Desligado'}</span>
          </div>
        </div>
      )}

      <div className="orch-settings__row" data-testid="context-window-overrides-section">
        <div className="orch-settings__row-head">
          <div className="orch-settings__meta">
            <span className="orch-settings__label">
              {overridesItem?.label ?? 'Janela de contexto por modelo'}
            </span>
            <p className="orch-settings__description">
              Compare catálogo, limite aprendido após erro do modelo e janela efetiva. Overrides manuais aceitam entre 50.000 e 1.000.000 tokens.
            </p>
            <span className="orch-settings__path">context.windowOverrides</span>
          </div>
          {overridesItem && (
            <div className="orch-settings__badges">
              <span
                className={
                  overridesItem.layer === 'managed'
                    ? 'code-settings__layer code-settings__layer--managed'
                    : 'code-settings__layer'
                }
              >
                {LAYER_LABELS[overridesItem.layer]}
              </span>
              {overridesItem.hasUserOverride && !overridesItem.locked && (
                <button
                  type="button"
                  className="button button--quiet orch-settings__reset"
                  disabled={busy}
                  onClick={() => void onSave({ 'context.windowOverrides': null })}
                >
                  Restaurar padrão
                </button>
              )}
            </div>
          )}
        </div>

        {validationError && (
          <p className="context-window-panel__error" role="alert">
            {validationError}
          </p>
        )}

        {loadingWindows && windows.length === 0 ? (
          <p className="code-settings__muted">Carregando janelas por modelo…</p>
        ) : windows.length > 0 ? (
          <div className="context-window-panel__list">
            {windows.map((entry) => {
              const key = `${entry.provider}/${entry.model_id}`
              const currentOverride =
                overridesMap[key] ?? overridesMap[entry.model_id] ?? entry.override_tokens
              const draft =
                overrideDrafts[key] ??
                (currentOverride !== null && currentOverride !== undefined
                  ? String(currentOverride)
                  : '')
              const effectiveDetail =
                describeWindowSource(
                  entry.effective_source,
                  entry.effective_detail,
                  entry.effective_tokens,
                ) ?? entry.effective_source

              return (
                <div
                  key={key}
                  className="context-window-panel__item"
                  data-testid={`context-window-row-${key}`}
                >
                  <div className="context-window-panel__item-head">
                    <div>
                      <strong className="context-window-panel__model-name">
                        {entry.display_name}
                      </strong>
                      <span className="context-window-panel__model-key">{key}</span>
                    </div>
                    <span className="context-window-panel__effective-pill">
                      Efetiva: {entry.effective_tokens.toLocaleString('pt-BR')} tokens
                    </span>
                  </div>

                  <dl className="context-window-panel__facts">
                    <div>
                      <dt>Catálogo</dt>
                      <dd>
                        {entry.catalog_tokens !== null
                          ? `${entry.catalog_tokens.toLocaleString('pt-BR')} tokens`
                          : '—'}
                      </dd>
                    </div>
                    <div>
                      <dt>Aprendida</dt>
                      <dd>
                        {entry.learned ? (
                          <span className="context-window-panel__learned">
                            <span>
                              {entry.learned.tokens.toLocaleString('pt-BR')} tokens
                              {entry.learned.observed_at
                                ? ` (${formatObservedDate(entry.learned.observed_at)})`
                                : ''}
                            </span>
                            {client && (
                              <button
                                type="button"
                                className="button button--quiet context-window-panel__forget"
                                disabled={busy || forgettingKey === key}
                                onClick={() => void handleForgetLearned(entry)}
                              >
                                {forgettingKey === key ? 'Esquecendo…' : 'Esquecer'}
                              </button>
                            )}
                          </span>
                        ) : (
                          'Nenhuma'
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>Fonte efetiva</dt>
                      <dd>{effectiveDetail}</dd>
                    </div>
                  </dl>

                  <form
                    className="context-window-panel__override-form"
                    onSubmit={(event) => {
                      event.preventDefault()
                      void saveOverrideForKey(key, draft)
                    }}
                  >
                    <label
                      className="context-window-panel__override-label"
                      htmlFor={`override-${key.replace(/[^A-Za-z0-9_-]/g, '-')}`}
                    >
                      Override manual (tokens)
                    </label>
                    <div className="orch-settings__control">
                      <input
                        id={`override-${key.replace(/[^A-Za-z0-9_-]/g, '-')}`}
                        type="text"
                        inputMode="numeric"
                        placeholder="50000 a 1000000"
                        value={draft}
                        disabled={overridesDisabled}
                        onChange={(event) =>
                          setOverrideDrafts((prev) => ({
                            ...prev,
                            [key]: event.target.value,
                          }))
                        }
                      />
                      <button
                        type="submit"
                        className="button button--quiet"
                        disabled={overridesDisabled}
                      >
                        Salvar
                      </button>
                      {currentOverride !== null && currentOverride !== undefined && (
                        <button
                          type="button"
                          className="button button--quiet"
                          disabled={overridesDisabled}
                          onClick={() => void removeOverrideForKey(key)}
                        >
                          Remover
                        </button>
                      )}
                    </div>
                  </form>
                </div>
              )
            })}
          </div>
        ) : (
          <p className="code-settings__muted">Nenhum modelo listado no catálogo local.</p>
        )}

        <form className="context-window-panel__add-custom" onSubmit={handleCustomSubmit}>
          <input
            type="text"
            aria-label="Novo identificador provider/modelo para override manual"
            placeholder="provider/modelo (ex.: anthropic-cli/claude-fable-5-2)"
            value={customModelKey}
            disabled={overridesDisabled}
            onChange={(event) => setCustomModelKey(event.target.value)}
          />
          <input
            type="text"
            inputMode="numeric"
            aria-label="Novo limite em tokens (50.000 a 1.000.000)"
            placeholder="Tokens (50000..1000000)"
            value={customLimit}
            disabled={overridesDisabled}
            onChange={(event) => setCustomLimit(event.target.value)}
          />
          <button type="submit" className="button button--quiet" disabled={overridesDisabled}>
            Adicionar override
          </button>
        </form>
      </div>
    </div>
  )
}

import { useState, type FormEvent } from 'react'
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

const DEFAULT_GROUP_LABELS: Record<string, string> = {
  effort: 'Esforço de raciocínio',
  quota: 'Cota e política de assinatura',
  context: 'Janela de contexto',
  messages: 'Mensagens a subagentes',
  friend: 'Orin Friend',
  worktree: 'Worktree por subagente',
  task_board: 'Quadro de tarefas compartilhado',
  background: 'Agentes em segundo plano e travamento',
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
  now: 'Entregar no turno em andamento (now)',
  queue: 'Deixar na fila para depois do turno (queue)',
}

function choiceLabel(choice: string): string {
  return CHOICE_LABELS[choice] ?? choice
}

function controlIdFor(path: string): string {
  return `orch-setting-${path.replace(/[^A-Za-z0-9_-]/g, '-')}`
}

function NumberControl({
  item,
  inputId,
  disabled,
  onSave,
}: {
  item: OrchestrationSettingItem
  inputId: string
  disabled: boolean
  onSave: (value: number | null) => void
}) {
  const [draft, setDraft] = useState(() => (typeof item.value === 'number' ? String(item.value) : ''))

  function submit(event: FormEvent) {
    event.preventDefault()
    if (disabled) return
    // An empty field clears the value (0.13 WS-C11: the budget ceilings are "no ceiling" when empty).
    if (draft.trim() === '') {
      if (item.value !== null) onSave(null)
      return
    }
    const parsed = Number(draft)
    if (!Number.isFinite(parsed)) return
    onSave(parsed)
  }

  return (
    <form className="orch-settings__control" onSubmit={submit}>
      <input
        id={inputId}
        type="number"
        min={item.minimum ?? undefined}
        max={item.maximum ?? undefined}
        step={item.minimum !== null && !Number.isInteger(item.minimum) ? 'any' : '1'}
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
      />
      <button type="submit" className="button button--quiet" disabled={disabled}>
        Salvar
      </button>
    </form>
  )
}

function StringControl({
  item,
  inputId,
  disabled,
  onSave,
}: {
  item: OrchestrationSettingItem
  inputId: string
  disabled: boolean
  onSave: (value: string) => void
}) {
  const [draft, setDraft] = useState(() => (typeof item.value === 'string' ? item.value : ''))

  function submit(event: FormEvent) {
    event.preventDefault()
    if (disabled) return
    onSave(draft)
  }

  return (
    <form className="orch-settings__control" onSubmit={submit}>
      <input
        id={inputId}
        type="text"
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
      />
      <button type="submit" className="button button--quiet" disabled={disabled}>
        Salvar
      </button>
    </form>
  )
}

function StringListControl({
  item,
  disabled,
  onSave,
}: {
  item: OrchestrationSettingItem
  disabled: boolean
  onSave: (value: string[]) => void
}) {
  const entries = Array.isArray(item.value)
    ? item.value.filter((entry): entry is string => typeof entry === 'string')
    : []
  const [nextItem, setNextItem] = useState('')

  function addItem(event: FormEvent) {
    event.preventDefault()
    const clean = nextItem.trim()
    if (!clean || disabled) return
    onSave([...entries, clean])
    setNextItem('')
  }

  function removeAt(index: number) {
    if (disabled) return
    onSave(entries.filter((_, idx) => idx !== index))
  }

  return (
    <div className="orch-settings__collection">
      {entries.length > 0 && (
        <ul className="orch-settings__items">
          {entries.map((entry, index) => (
            <li key={`${entry}-${index}`} className="orch-settings__item">
              <code>{entry}</code>
              <button
                type="button"
                className="button button--quiet"
                disabled={disabled}
                onClick={() => removeAt(index)}
              >
                Remover
              </button>
            </li>
          ))}
        </ul>
      )}
      <form className="orch-settings__add-row" onSubmit={addItem}>
        <input
          type="text"
          aria-label={`Adicionar item em ${item.label}`}
          placeholder="provider/modelo"
          value={nextItem}
          disabled={disabled}
          onChange={(event) => setNextItem(event.target.value)}
        />
        <button type="submit" className="button button--quiet" disabled={disabled || !nextItem.trim()}>
          Adicionar
        </button>
      </form>
    </div>
  )
}

function MapControl({
  item,
  disabled,
  onSave,
}: {
  item: OrchestrationSettingItem
  disabled: boolean
  onSave: (value: Record<string, unknown>) => void
}) {
  const currentMap: Record<string, unknown> =
    item.value && typeof item.value === 'object' && !Array.isArray(item.value)
      ? (item.value as Record<string, unknown>)
      : {}
  const entries = Object.entries(currentMap)
  const [newKey, setNewKey] = useState('')
  const [newVal, setNewVal] = useState(() => (item.itemType === 'choice' && item.choices?.[0] ? item.choices[0] : ''))

  function parseMapValue(raw: string): unknown {
    if (item.itemType === 'number') {
      const parsed = Number(raw)
      return Number.isFinite(parsed) ? parsed : null
    }
    return raw.trim()
  }

  function addEntry(event: FormEvent) {
    event.preventDefault()
    const cleanKey = newKey.trim()
    if (!cleanKey || disabled) return
    const parsedValue = parseMapValue(newVal)
    if (parsedValue === null || parsedValue === '') return
    onSave({ ...currentMap, [cleanKey]: parsedValue })
    setNewKey('')
    if (item.itemType !== 'choice') setNewVal('')
  }

  function updateEntry(key: string, nextValue: unknown) {
    if (disabled) return
    onSave({ ...currentMap, [key]: nextValue })
  }

  function removeEntry(key: string) {
    if (disabled) return
    const next = { ...currentMap }
    delete next[key]
    onSave(next)
  }

  return (
    <div className="orch-settings__collection">
      {entries.length > 0 && (
        <ul className="orch-settings__items">
          {entries.map(([entryKey, entryVal]) => (
            <li key={entryKey} className="orch-settings__item">
              <code>{entryKey}</code>
              <div className="orch-settings__control">
                {item.itemType === 'choice' && item.choices ? (
                  <select
                    aria-label={`${item.label}: ${entryKey}`}
                    value={typeof entryVal === 'string' ? entryVal : ''}
                    disabled={disabled}
                    onChange={(event) => updateEntry(entryKey, event.target.value)}
                  >
                    {item.choices.map((choice) => (
                      <option key={choice} value={choice}>
                        {choiceLabel(choice)}
                      </option>
                    ))}
                  </select>
                ) : (
                  <code>{String(entryVal)}</code>
                )}
                <button
                  type="button"
                  className="button button--quiet"
                  disabled={disabled}
                  onClick={() => removeEntry(entryKey)}
                >
                  Remover
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <form className="orch-settings__add-row" onSubmit={addEntry}>
        <input
          type="text"
          aria-label={`Nova chave em ${item.label}`}
          placeholder="provider/modelo"
          value={newKey}
          disabled={disabled}
          onChange={(event) => setNewKey(event.target.value)}
        />
        {item.itemType === 'choice' && item.choices ? (
          <select
            aria-label={`Novo valor em ${item.label}`}
            value={newVal}
            disabled={disabled}
            onChange={(event) => setNewVal(event.target.value)}
          >
            {item.choices.map((choice) => (
              <option key={choice} value={choice}>
                {choiceLabel(choice)}
              </option>
            ))}
          </select>
        ) : (
          <input
            type={item.itemType === 'number' ? 'number' : 'text'}
            aria-label={`Novo valor em ${item.label}`}
            min={item.valueMinimum ?? undefined}
            max={item.valueMaximum ?? undefined}
            placeholder={item.itemType === 'number' ? '200000' : 'valor'}
            value={newVal}
            disabled={disabled}
            onChange={(event) => setNewVal(event.target.value)}
          />
        )}
        <button type="submit" className="button button--quiet" disabled={disabled || !newKey.trim()}>
          Adicionar
        </button>
      </form>
    </div>
  )
}

function SettingRow({
  item,
  busy,
  onSave,
}: {
  item: OrchestrationSettingItem
  busy: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const inputId = controlIdFor(item.path)
  const isLocked = item.locked || item.layer === 'managed'
  const disabled = busy || isLocked
  const canReset = !isLocked && (item.hasUserOverride || item.layer === 'user')

  return (
    <div
      className={isLocked ? 'orch-settings__row is-locked' : 'orch-settings__row'}
      data-testid={`setting-row-${item.path}`}
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
            data-testid={`layer-${item.path}`}
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
            {item.defaultValue === null && <option value="">Padrão do modelo</option>}
            {(item.choices ?? []).map((choice) => (
              <option key={choice} value={choice}>
                {choiceLabel(choice)}
              </option>
            ))}
          </select>
        </div>
      )}

      {item.type === 'number' && (
        <NumberControl
          key={`${item.path}:${String(item.value)}`}
          item={item}
          inputId={inputId}
          disabled={disabled}
          onSave={(next) => void onSave({ [item.path]: next })}
        />
      )}

      {item.type === 'string' && (
        <StringControl
          key={`${item.path}:${String(item.value)}`}
          item={item}
          inputId={inputId}
          disabled={disabled}
          onSave={(next) => void onSave({ [item.path]: next })}
        />
      )}

      {item.type === 'string_list' && (
        <StringListControl
          item={item}
          disabled={disabled}
          onSave={(next) => void onSave({ [item.path]: next })}
        />
      )}

      {item.type === 'map' && (
        <MapControl
          item={item}
          disabled={disabled}
          onSave={(next) => void onSave({ [item.path]: next })}
        />
      )}
    </div>
  )
}

export function OrchestrationSettingsForm({
  items,
  onSave,
  busy = false,
  groupLabels = DEFAULT_GROUP_LABELS,
  hideGroupHeaders = false,
}: {
  items: OrchestrationSettingItem[]
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
  busy?: boolean
  groupLabels?: Record<string, string>
  hideGroupHeaders?: boolean
}) {
  const grouped = new Map<string, OrchestrationSettingItem[]>()
  for (const item of items) {
    const list = grouped.get(item.group) ?? []
    list.push(item)
    grouped.set(item.group, list)
  }

  return (
    <div className="orch-settings">
      {Array.from(grouped.entries()).map(([group, groupItems]) => (
        <div className="orch-settings__group" key={group}>
          {!hideGroupHeaders && (
            <h3 className="orch-settings__group-title">{groupLabels[group] ?? group}</h3>
          )}
          <div className="orch-settings__list">
            {groupItems.map((item) => (
              <SettingRow key={item.path} item={item} busy={busy} onSave={onSave} />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

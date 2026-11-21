import { useState, type FormEvent, type ReactNode } from 'react'
import type {
  OrchestrationSettingItem,
  OrchestrationSettingLayer,
} from '../../api/orchestrationSettings'
import { Card } from '../codeSettings/shared'
import './orchestrationSettings.css'

const LAYER_LABELS: Record<OrchestrationSettingLayer, string> = {
  default: 'Padrão',
  user: 'Usuário',
  project: 'Projeto',
  local: 'Local',
  cli: 'Linha de comando',
  managed: 'Gerenciado',
}

const POLICY_LABELS: Record<string, string> = {
  cross_family: 'Outra família (cross_family)',
  same_model: 'Mesmo modelo em contexto novo (same_model)',
  explicit: 'Modelo fixo (explicit)',
}

const EFFORT_LABELS: Record<string, string> = {
  low: 'Baixo (low)',
  medium: 'Médio (medium)',
  high: 'Alto (high)',
  xhigh: 'Muito alto (xhigh)',
  max: 'Máximo (max)',
  ultra: 'Ultra (ultra)',
}

const EFFORT_OPTIONS: readonly string[] = ['low', 'medium', 'high', 'xhigh', 'max', 'ultra']

type ModelTierEntry = {
  family: string
  tier: number
}

type ExplicitVerifierConfig = {
  provider: string
  model: string
  effort?: string
}

function controlIdFor(path: string): string {
  return `orch-setting-${path.replace(/[^A-Za-z0-9_-]/g, '-')}`
}

function parseExplicitConfig(raw: unknown): ExplicitVerifierConfig {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { provider: '', model: '', effort: '' }
  }
  const rec = raw as Record<string, unknown>
  return {
    provider: typeof rec.provider === 'string' ? rec.provider : '',
    model: typeof rec.model === 'string' ? rec.model : '',
    effort: typeof rec.effort === 'string' ? rec.effort : '',
  }
}

function parseModelTiers(raw: unknown): Record<string, ModelTierEntry> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  const out: Record<string, ModelTierEntry> = {}
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      out[key] = { family: 'other', tier: Math.round(value) }
    } else if (value && typeof value === 'object' && !Array.isArray(value)) {
      const entry = value as Record<string, unknown>
      const family = typeof entry.family === 'string' && entry.family.trim() ? entry.family.trim() : 'other'
      const tier = typeof entry.tier === 'number' && Number.isFinite(entry.tier) ? Math.round(entry.tier) : 0
      out[key] = { family, tier }
    }
  }
  return out
}

function SettingHeader({
  item,
  inputId,
  busy,
  onReset,
  extraDescription,
}: {
  item: OrchestrationSettingItem
  inputId: string
  busy: boolean
  onReset: () => void
  extraDescription?: string
}) {
  const canReset = !item.locked && item.hasUserOverride
  return (
    <div className="orch-settings__row-head">
      <div className="orch-settings__meta">
        <label className="orch-settings__label" htmlFor={inputId}>
          {item.label}
        </label>
        {(item.description || extraDescription) && (
          <p className="orch-settings__description">
            {item.description}
            {extraDescription && !item.description.includes(extraDescription) ? ` ${extraDescription}` : ''}
          </p>
        )}
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
            onClick={onReset}
          >
            Restaurar padrão
          </button>
        )}
      </div>
    </div>
  )
}

function BooleanRow({
  item,
  busy,
  onSave,
  extraDescription,
  banner,
}: {
  item: OrchestrationSettingItem
  busy: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
  extraDescription?: string
  banner?: ReactNode
}) {
  const inputId = controlIdFor(item.path)
  const disabled = busy || item.locked
  const checked = Boolean(item.value)

  return (
    <div className={`orch-settings__row${item.locked ? ' is-locked' : ''}`} data-setting-path={item.path}>
      <SettingHeader
        item={item}
        inputId={inputId}
        busy={busy}
        extraDescription={extraDescription}
        onReset={() => void onSave({ [item.path]: null })}
      />
      <div className="orch-settings__switch">
        <input
          id={inputId}
          type="checkbox"
          role="switch"
          aria-checked={checked}
          checked={checked}
          disabled={disabled}
          onChange={(event) => void onSave({ [item.path]: event.target.checked })}
        />
        <span aria-hidden="true">{checked ? 'Ligado' : 'Desligado'}</span>
      </div>
      {banner}
    </div>
  )
}

function NumberRow({
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
  const [draft, setDraft] = useState(() => (typeof item.value === 'number' ? String(item.value) : ''))

  function submit(event: FormEvent) {
    event.preventDefault()
    if (disabled) return
    const parsed = Number(draft)
    if (!Number.isFinite(parsed)) return
    void onSave({ [item.path]: Math.round(parsed) })
  }

  return (
    <div className={`orch-settings__row${item.locked ? ' is-locked' : ''}`} data-setting-path={item.path}>
      <SettingHeader
        item={item}
        inputId={inputId}
        busy={busy}
        onReset={() => void onSave({ [item.path]: null })}
      />
      <form className="orch-settings__control" onSubmit={submit}>
        <input
          id={inputId}
          type="number"
          min={item.minimum ?? undefined}
          max={item.maximum ?? undefined}
          step="1"
          value={draft}
          disabled={disabled}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="submit" className="button button--quiet" disabled={disabled}>
          Salvar
        </button>
      </form>
    </div>
  )
}

function ChoiceRow({
  item,
  busy,
  labels,
  onSave,
}: {
  item: OrchestrationSettingItem
  busy: boolean
  labels: Record<string, string>
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const inputId = controlIdFor(item.path)
  const disabled = busy || item.locked

  return (
    <div className={`orch-settings__row${item.locked ? ' is-locked' : ''}`} data-setting-path={item.path}>
      <SettingHeader
        item={item}
        inputId={inputId}
        busy={busy}
        onReset={() => void onSave({ [item.path]: null })}
      />
      <div className="orch-settings__control">
        <select
          id={inputId}
          value={typeof item.value === 'string' ? item.value : ''}
          disabled={disabled}
          onChange={(event) => void onSave({ [item.path]: event.target.value })}
        >
          {(item.choices ?? []).map((choice) => (
            <option key={choice} value={choice}>
              {labels[choice] ?? choice}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}

function GlobListRow({
  item,
  busy,
  placeholder,
  onSave,
}: {
  item: OrchestrationSettingItem
  busy: boolean
  placeholder: string
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const inputId = controlIdFor(item.path)
  const disabled = busy || item.locked
  const entries = Array.isArray(item.value)
    ? item.value.filter((entry): entry is string => typeof entry === 'string')
    : []
  const [nextPattern, setNextPattern] = useState('')

  function addPattern(event: FormEvent) {
    event.preventDefault()
    const clean = nextPattern.trim()
    if (!clean || disabled) return
    if (entries.includes(clean)) {
      setNextPattern('')
      return
    }
    void onSave({ [item.path]: [...entries, clean] })
    setNextPattern('')
  }

  function removeAt(index: number) {
    if (disabled) return
    void onSave({ [item.path]: entries.filter((_, idx) => idx !== index) })
  }

  return (
    <div className={`orch-settings__row${item.locked ? ' is-locked' : ''}`} data-setting-path={item.path}>
      <SettingHeader
        item={item}
        inputId={inputId}
        busy={busy}
        onReset={() => void onSave({ [item.path]: null })}
      />
      <div className="orch-settings__collection">
        {entries.length > 0 ? (
          <ul className="orch-settings__items">
            {entries.map((entry, index) => (
              <li className="orch-settings__item" key={`${entry}-${index}`}>
                <code>{entry}</code>
                <button
                  type="button"
                  className="button button--quiet"
                  disabled={disabled}
                  aria-label={`Remover padrão ${entry}`}
                  onClick={() => removeAt(index)}
                >
                  Remover
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="code-settings__muted">Nenhum padrão configurado.</p>
        )}
        <form className="orch-settings__add-row" onSubmit={addPattern}>
          <input
            id={inputId}
            type="text"
            placeholder={placeholder}
            value={nextPattern}
            disabled={disabled}
            onChange={(event) => setNextPattern(event.target.value)}
          />
          <button type="submit" className="button button--quiet" disabled={disabled || !nextPattern.trim()}>
            Adicionar
          </button>
        </form>
      </div>
    </div>
  )
}

function ExplicitModelRow({
  item,
  policy,
  busy,
  onSave,
}: {
  item: OrchestrationSettingItem
  policy: string
  busy: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const inputId = controlIdFor(item.path)
  const disabled = busy || item.locked
  const initial = parseExplicitConfig(item.value)
  const [provider, setProvider] = useState(initial.provider)
  const [model, setModel] = useState(initial.model)
  const [effort, setEffort] = useState(initial.effort ?? '')

  const isExplicitActive = policy === 'explicit'
  const canSubmit = !disabled && provider.trim().length > 0 && model.trim().length > 0

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!canSubmit) return
    const next: Record<string, string> = {
      provider: provider.trim(),
      model: model.trim(),
    }
    if (effort.trim()) {
      next.effort = effort.trim()
    }
    void onSave({ [item.path]: next })
  }

  return (
    <div
      className={`orch-settings__row${item.locked ? ' is-locked' : ''}${isExplicitActive ? ' verifier-settings__explicit--active' : ' verifier-settings__explicit--muted'}`}
      data-setting-path={item.path}
    >
      <SettingHeader
        item={item}
        inputId={inputId}
        busy={busy}
        onReset={() => void onSave({ [item.path]: null })}
      />
      {!isExplicitActive && (
        <p className="code-settings__muted">
          Usado apenas quando a política do verificador está definida como <strong>explicit</strong>.
        </p>
      )}
      <form className="verifier-settings__explicit-grid" onSubmit={submit}>
        <label className="verifier-settings__field">
          <span>Provedor</span>
          <input
            id={inputId}
            type="text"
            placeholder="ex.: openai-codex"
            value={provider}
            disabled={disabled}
            onChange={(event) => setProvider(event.target.value)}
          />
        </label>
        <label className="verifier-settings__field">
          <span>Modelo</span>
          <input
            type="text"
            placeholder="ex.: gpt-6.1-sol"
            value={model}
            disabled={disabled}
            onChange={(event) => setModel(event.target.value)}
          />
        </label>
        <label className="verifier-settings__field">
          <span>Esforço (opcional)</span>
          <select
            value={effort}
            disabled={disabled}
            onChange={(event) => setEffort(event.target.value)}
          >
            <option value="">Usar esforço do verificador</option>
            {EFFORT_OPTIONS.map((level) => (
              <option key={level} value={level}>
                {EFFORT_LABELS[level] ?? level}
              </option>
            ))}
          </select>
        </label>
        <div className="verifier-settings__explicit-actions">
          <button type="submit" className="button button--quiet" disabled={!canSubmit}>
            Salvar modelo fixo
          </button>
        </div>
      </form>
    </div>
  )
}

function ModelTierRowEditor({
  modelId,
  entry,
  minTier,
  maxTier,
  disabled,
  onSaveEntry,
  onRemove,
}: {
  modelId: string
  entry: ModelTierEntry
  minTier: number
  maxTier: number
  disabled: boolean
  onSaveEntry: (modelId: string, next: ModelTierEntry) => void
  onRemove: (modelId: string) => void
}) {
  const [family, setFamily] = useState(entry.family)
  const [tier, setTier] = useState(String(entry.tier))

  const parsedTier = Number(tier)
  const valid =
    family.trim().length > 0 &&
    Number.isInteger(parsedTier) &&
    parsedTier >= minTier &&
    parsedTier <= maxTier
  const dirty = family.trim().toLowerCase() !== entry.family.toLowerCase() || parsedTier !== entry.tier

  return (
    <tr>
      <td>
        <code className="verifier-tiers__model">{modelId}</code>
      </td>
      <td>
        <input
          type="text"
          className="verifier-tiers__input"
          aria-label={`Família de ${modelId}`}
          value={family}
          disabled={disabled}
          onChange={(event) => setFamily(event.target.value)}
        />
      </td>
      <td>
        <input
          type="number"
          className="verifier-tiers__tier-input"
          aria-label={`Nível de ${modelId}`}
          min={minTier}
          max={maxTier}
          step="1"
          value={tier}
          disabled={disabled}
          onChange={(event) => setTier(event.target.value)}
        />
      </td>
      <td className="verifier-tiers__actions">
        <button
          type="button"
          className="button button--quiet"
          disabled={disabled || !valid || !dirty}
          aria-label={`Salvar ${modelId}`}
          onClick={() => onSaveEntry(modelId, { family: family.trim().toLowerCase(), tier: parsedTier })}
        >
          Salvar
        </button>
        <button
          type="button"
          className="button button--quiet"
          disabled={disabled}
          aria-label={`Remover ${modelId}`}
          onClick={() => onRemove(modelId)}
        >
          Remover
        </button>
      </td>
    </tr>
  )
}

function ModelTiersBlock({
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
  const minTier = item.valueMinimum ?? 0
  const maxTier = item.valueMaximum ?? 10
  const tiers = parseModelTiers(item.value)
  const entries = Object.entries(tiers).sort(([a], [b]) => a.localeCompare(b))

  const [newModel, setNewModel] = useState('')
  const [newFamily, setNewFamily] = useState('')
  const [newTier, setNewTier] = useState('3')

  function saveEntry(modelId: string, nextEntry: ModelTierEntry) {
    if (disabled) return
    const nextMap: Record<string, ModelTierEntry> = { ...tiers, [modelId]: nextEntry }
    void onSave({ [item.path]: nextMap })
  }

  function removeEntry(modelId: string) {
    if (disabled) return
    const nextMap: Record<string, ModelTierEntry> = { ...tiers }
    delete nextMap[modelId]
    void onSave({ [item.path]: nextMap })
  }

  function addEntry(event: FormEvent) {
    event.preventDefault()
    if (disabled) return
    const cleanModel = newModel.trim().toLowerCase()
    const cleanFamily = newFamily.trim().toLowerCase()
    const parsedTier = Number(newTier)
    if (
      !cleanModel ||
      !cleanFamily ||
      !Number.isInteger(parsedTier) ||
      parsedTier < minTier ||
      parsedTier > maxTier
    ) {
      return
    }
    const nextMap: Record<string, ModelTierEntry> = {
      ...tiers,
      [cleanModel]: { family: cleanFamily, tier: parsedTier },
    }
    void onSave({ [item.path]: nextMap })
    setNewModel('')
    setNewFamily('')
    setNewTier('3')
  }

  const parsedNewTier = Number(newTier)
  const canAdd =
    !disabled &&
    newModel.trim().length > 0 &&
    newFamily.trim().length > 0 &&
    Number.isInteger(parsedNewTier) &&
    parsedNewTier >= minTier &&
    parsedNewTier <= maxTier

  return (
    <div className={`orch-settings__row${item.locked ? ' is-locked' : ''}`} data-setting-path={item.path}>
      <SettingHeader
        item={item}
        inputId={inputId}
        busy={busy}
        onReset={() => void onSave({ [item.path]: null })}
      />

      <div className="orch-settings__table-wrap">
        <table className="verifier-tiers__table" aria-label="Tabela de famílias e níveis de modelos">
          <thead>
            <tr>
              <th>Modelo</th>
              <th>Família</th>
              <th>Nível ({minTier}–{maxTier})</th>
              <th>Ações</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([modelId, entry]) => (
              <ModelTierRowEditor
                key={`${modelId}:${entry.family}:${entry.tier}`}
                modelId={modelId}
                entry={entry}
                minTier={minTier}
                maxTier={maxTier}
                disabled={disabled}
                onSaveEntry={saveEntry}
                onRemove={removeEntry}
              />
            ))}
          </tbody>
        </table>
      </div>

      <form className="verifier-tiers__add-form" onSubmit={addEntry}>
        <input
          id={inputId}
          type="text"
          placeholder="Novo modelo (ex.: claude-opus-6)"
          aria-label="Novo modelo"
          value={newModel}
          disabled={disabled}
          onChange={(event) => setNewModel(event.target.value)}
        />
        <input
          type="text"
          placeholder="Família (ex.: anthropic)"
          aria-label="Família do novo modelo"
          value={newFamily}
          disabled={disabled}
          onChange={(event) => setNewFamily(event.target.value)}
        />
        <input
          type="number"
          min={minTier}
          max={maxTier}
          step="1"
          aria-label="Nível do novo modelo"
          value={newTier}
          disabled={disabled}
          onChange={(event) => setNewTier(event.target.value)}
        />
        <button type="submit" className="button button--quiet" disabled={!canAdd}>
          Adicionar modelo
        </button>
      </form>
    </div>
  )
}

export function VerifierSettingsBlocks({
  items,
  busy = false,
  onSave,
}: {
  items: OrchestrationSettingItem[]
  busy?: boolean
  onSave: (changes: Record<string, unknown>) => Promise<void> | void
}) {
  const byPath = new Map(items.map((item) => [item.path, item]))

  const enabledItem = byPath.get('orchestration.verifier.enabled')
  const selfVerifyItem = byPath.get('orchestration.selfVerify.enabled')
  const includeSubagentsItem = byPath.get('orchestration.verifier.includeSubagents')

  const modelPolicyItem = byPath.get('orchestration.verifier.modelPolicy')
  const explicitItem = byPath.get('orchestration.verifier.explicit')
  const effortItem = byPath.get('orchestration.verifier.effort')
  const maxRoundsItem = byPath.get('orchestration.verifier.maxRounds')

  const filesThresholdItem = byPath.get('orchestration.verifier.threshold.files')
  const linesThresholdItem = byPath.get('orchestration.verifier.threshold.lines')
  const sensitivePathsItem = byPath.get('orchestration.verifier.threshold.sensitivePaths')
  const ignoreGlobsItem = byPath.get('orchestration.verifier.threshold.ignore')

  const modelTiersItem = byPath.get('orchestration.modelTiers')

  if (items.length === 0) {
    return (
      <p className="code-settings__muted">
        Nenhuma chave do verificador independente disponível nesta instância.
      </p>
    )
  }

  const currentPolicy = typeof modelPolicyItem?.value === 'string' ? modelPolicyItem.value : 'cross_family'
  const verifierEnabled = Boolean(enabledItem?.value)

  return (
    <div className="verifier-settings__stack">
      <div data-testid="orchestration-section-verifier-core">
        <Card title="Verificador independente" id="orchestration-heading-verifier">
          <p className="code-settings__muted">
            Executa uma revisão somente leitura com outro modelo após alterações relevantes para detectar regressões com evidência objetiva.
          </p>
          <div className="orch-settings__list">
            {enabledItem && (
              <BooleanRow
                item={enabledItem}
                busy={busy}
                onSave={onSave}
                banner={
                  <p
                    role="note"
                    className={`verifier-settings__cost-notice${verifierEnabled ? ' is-active' : ''}`}
                    data-testid="verifier-cost-warning"
                  >
                    <strong>Aviso de custo:</strong> o verificador independente pode consumir tokens e cota de outra assinatura ou provedor configurado.
                  </p>
                }
              />
            )}
            {selfVerifyItem && (
              <BooleanRow
                item={selfVerifyItem}
                busy={busy}
                onSave={onSave}
                extraDescription="Antes da 0.13 esta rodada era automática."
              />
            )}
            {includeSubagentsItem && (
              <BooleanRow item={includeSubagentsItem} busy={busy} onSave={onSave} />
            )}
          </div>
        </Card>
      </div>

      {(modelPolicyItem || explicitItem || effortItem || maxRoundsItem) && (
        <div data-testid="orchestration-section-verifier-policy">
          <Card title="Política, modelo e rodadas" id="orchestration-heading-verifier-policy">
            <div className="orch-settings__list">
              {modelPolicyItem && (
                <ChoiceRow
                  item={modelPolicyItem}
                  busy={busy}
                  labels={POLICY_LABELS}
                  onSave={onSave}
                />
              )}
              {explicitItem && (
                <ExplicitModelRow
                  key={`${explicitItem.path}:${JSON.stringify(explicitItem.value)}`}
                  item={explicitItem}
                  policy={currentPolicy}
                  busy={busy}
                  onSave={onSave}
                />
              )}
              {effortItem && (
                <ChoiceRow
                  item={effortItem}
                  busy={busy}
                  labels={EFFORT_LABELS}
                  onSave={onSave}
                />
              )}
              {maxRoundsItem && (
                <NumberRow
                  key={`${maxRoundsItem.path}:${String(maxRoundsItem.value)}`}
                  item={maxRoundsItem}
                  busy={busy}
                  onSave={onSave}
                />
              )}
            </div>
          </Card>
        </div>
      )}

      {(filesThresholdItem || linesThresholdItem || sensitivePathsItem || ignoreGlobsItem) && (
        <div data-testid="orchestration-section-verifier-thresholds">
          <Card title="Limiares e caminhos" id="orchestration-heading-verifier-thresholds">
            <div className="orch-settings__list">
              {filesThresholdItem && (
                <NumberRow
                  key={`${filesThresholdItem.path}:${String(filesThresholdItem.value)}`}
                  item={filesThresholdItem}
                  busy={busy}
                  onSave={onSave}
                />
              )}
              {linesThresholdItem && (
                <NumberRow
                  key={`${linesThresholdItem.path}:${String(linesThresholdItem.value)}`}
                  item={linesThresholdItem}
                  busy={busy}
                  onSave={onSave}
                />
              )}
              {sensitivePathsItem && (
                <GlobListRow
                  item={sensitivePathsItem}
                  busy={busy}
                  placeholder="ex.: **/migrations/**"
                  onSave={onSave}
                />
              )}
              {ignoreGlobsItem && (
                <GlobListRow
                  item={ignoreGlobsItem}
                  busy={busy}
                  placeholder="ex.: **/*.lock"
                  onSave={onSave}
                />
              )}
            </div>
          </Card>
        </div>
      )}

      {modelTiersItem && (
        <div data-testid="orchestration-section-model-tiers">
          <Card title="Famílias e níveis de modelos" id="orchestration-heading-model-tiers">
            <p className="code-settings__muted">
              A família é determinada pelo modelo (nunca pelo provedor CLI) e usada para selecionar um revisor independente de nível equivalente ou superior.
            </p>
            <ModelTiersBlock item={modelTiersItem} busy={busy} onSave={onSave} />
          </Card>
        </div>
      )}
    </div>
  )
}

import { useState } from 'react'
import type { ProviderApiKeyState } from '../../api/providers'

function formatStatusLabel(apiKey: ProviderApiKeyState): string {
  if (apiKey.status === 'exhausted') return 'Sem saldo / Esgotada'
  if (apiKey.status === 'cooldown') return `Em cooldown até ${formatCooldown(apiKey.cooldownUntil)}`
  return 'Ativa'
}

function KeyRow({ apiKey, index, isLast, pending, onRename, onRemove, onMoveUp, onMoveDown, onActivate }: {
  apiKey: ProviderApiKeyState
  index: number
  isLast: boolean
  pending: boolean
  onRename: (keyId: number, label: string | null) => void
  onRemove: (keyId: number) => void
  onMoveUp: (keyId: number) => void
  onMoveDown: (keyId: number) => void
  onActivate?: (keyId: number) => Promise<void> | void
}) {
  // A draft kept separate from `apiKey.label` so typing a new label doesn't
  // fire a request (and self-disable via `pending`) on every keystroke; the
  // rename only goes out once the field loses focus, and only if it changed.
  const [draft, setDraft] = useState(apiKey.label ?? '')
  // Resetting the draft during render, rather than in an effect, means the
  // field never paints one frame showing the previous key's label.
  const [seenLabel, setSeenLabel] = useState(apiKey.label)
  if (apiKey.label !== seenLabel) {
    setSeenLabel(apiKey.label)
    setDraft(apiKey.label ?? '')
  }

  function commit() {
    const next = draft || null
    if (next !== (apiKey.label ?? null)) onRename(apiKey.id, next)
  }

  const canReactivate = apiKey.status === 'exhausted' || apiKey.status === 'cooldown'

  return <li>
    <div className="provider-key-list__identity"><span className="provider-key-list__label">{apiKey.label ?? `Chave ${index + 1}`}</span>{index === 0 && <span className="provider-key-list__badge">Principal</span>}<span className={`provider-key-list__status provider-key-list__status--${apiKey.status}`} role="status">{formatStatusLabel(apiKey)}</span></div>
    <div className="provider-key-list__controls">{canReactivate && <button type="button" className="button button--secondary provider-key-list__activate" disabled={pending} onClick={() => void onActivate?.(apiKey.id)}>Reativar</button>}<button type="button" className="icon-button" aria-label="Mover para cima" disabled={pending || index === 0} onClick={() => onMoveUp(apiKey.id)}>↑</button><button type="button" className="icon-button" aria-label="Mover para baixo" disabled={pending || isLast} onClick={() => onMoveDown(apiKey.id)}>↓</button><button type="button" className="button button--quiet provider-key-list__remove" aria-label="Remover chave" disabled={pending} onClick={() => onRemove(apiKey.id)}>Remover</button></div>
    <label className="provider-key-list__rename">Apelido<input aria-label="Apelido" value={draft} onChange={(event) => setDraft(event.target.value)} onBlur={commit} disabled={pending} /></label>
  </li>
}

export function ProviderKeyList({ keys, pending = false, cooldownSeconds = 60, onAdd, onRename, onRemove, onMoveUp, onMoveDown, onCooldownSecondsChange, onActivate }: {
  keys: ProviderApiKeyState[]
  pending?: boolean
  cooldownSeconds?: number
  onAdd: (apiKey: string, label?: string) => Promise<boolean>
  onRename: (keyId: number, label: string | null) => void
  onRemove: (keyId: number) => void
  onMoveUp: (keyId: number) => void
  onMoveDown: (keyId: number) => void
  onCooldownSecondsChange: (seconds: number) => void
  onActivate?: (id: number) => Promise<void> | void
}) {
  const [newKey, setNewKey] = useState('')
  const [newLabel, setNewLabel] = useState('')
  const [cooldownInput, setCooldownInput] = useState(String(cooldownSeconds))
  const [seenCooldown, setSeenCooldown] = useState(cooldownSeconds)
  if (cooldownSeconds !== seenCooldown) {
    setSeenCooldown(cooldownSeconds)
    setCooldownInput(String(cooldownSeconds))
  }

  async function submit() {
    if (!newKey) return
    // Only clear the form once the key was actually saved: on failure (a
    // rejected duplicate, a rate limit) the user would otherwise lose the
    // secret they just pasted and have to go find and retype it.
    const saved = await onAdd(newKey, newLabel || undefined)
    if (saved) {
      setNewKey('')
      setNewLabel('')
    }
  }

  function submitCooldown() {
    const seconds = Number.parseInt(cooldownInput, 10)
    if (!Number.isInteger(seconds) || seconds < 1) return
    onCooldownSecondsChange(seconds)
  }

  return <section className="provider-key-list" aria-label="Chaves de API">
    <div className="provider-panel__section-heading"><div><p className="eyebrow">FALLBACK E ROTACIONAMENTO</p><h3>Chaves de API</h3></div><span>{keys.length === 0 ? 'Nenhuma adicionada' : `${keys.length} ${keys.length === 1 ? 'chave' : 'chaves'}`}</span></div>
    <p className="provider-key-list__lede">Adicione chaves alternativas para manter o acesso disponível quando uma delas entrar em cooldown ou ficar sem saldo.</p>
    <div className="provider-key-list__notice" role="note"><span aria-hidden="true">↗</span><p><strong>Fallback automático</strong> Ao adicionar mais de uma chave API, o fallback automático será ativado.</p></div>
    <ul aria-label="Chaves configuradas">
      {keys.map((key, index) => <KeyRow
        key={key.id} apiKey={key} index={index} isLast={index === keys.length - 1} pending={pending}
        onRename={onRename} onRemove={onRemove} onMoveUp={onMoveUp} onMoveDown={onMoveDown} onActivate={onActivate}
      />)}
    </ul>
    <div className="provider-key-list__section provider-key-list__add">
      <div><h4>Adicionar uma chave</h4><p>A chave fica protegida e não será exibida novamente.</p></div>
      <div className="provider-key-list__fields"><label htmlFor="provider-key-list-new-key">Nova chave<input id="provider-key-list-new-key" type="password" autoComplete="off" value={newKey} onChange={(event) => setNewKey(event.target.value)} /></label><label htmlFor="provider-key-list-new-label">Apelido <span>(opcional)</span><input id="provider-key-list-new-label" type="text" value={newLabel} onChange={(event) => setNewLabel(event.target.value)} /></label><button type="button" className="button button--secondary" disabled={pending || !newKey} onClick={() => void submit()}>Adicionar chave</button></div>
    </div>
    <div className="provider-key-list__section provider-key-list__cooldown">
      <div><h4>Tempo de cooldown</h4><p>Intervalo antes de tentar novamente uma chave que falhou.</p></div>
      <div className="provider-key-list__cooldown-controls"><label htmlFor="provider-key-list-cooldown">Tempo de cooldown (s)<input id="provider-key-list-cooldown" type="number" min={1} value={cooldownInput} onChange={(event) => setCooldownInput(event.target.value)} /></label><button type="button" className="button button--secondary" disabled={pending || cooldownInput === String(cooldownSeconds)} onClick={submitCooldown}>Salvar tempo de cooldown</button></div>
    </div>
  </section>
}

function formatCooldown(value: string | null): string {
  if (!value) return ''
  try {
    return new Date(value).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
  } catch {
    return value
  }
}

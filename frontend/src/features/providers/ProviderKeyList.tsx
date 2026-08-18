import { useEffect, useState } from 'react'
import type { ProviderApiKeyState } from '../../api/providers'

export function ProviderKeyList({ keys, pending, cooldownSeconds, onAdd, onRename, onRemove, onMoveUp, onMoveDown, onCooldownSecondsChange }: {
  keys: ProviderApiKeyState[]
  pending: boolean
  cooldownSeconds: number
  onAdd: (apiKey: string, label?: string) => void
  onRename: (keyId: number, label: string | null) => void
  onRemove: (keyId: number) => void
  onMoveUp: (keyId: number) => void
  onMoveDown: (keyId: number) => void
  onCooldownSecondsChange: (seconds: number) => void
}) {
  const [newKey, setNewKey] = useState('')
  const [newLabel, setNewLabel] = useState('')
  const [cooldownInput, setCooldownInput] = useState(String(cooldownSeconds))
  useEffect(() => setCooldownInput(String(cooldownSeconds)), [cooldownSeconds])

  function submit() {
    if (!newKey) return
    onAdd(newKey, newLabel || undefined)
    setNewKey('')
    setNewLabel('')
  }

  function submitCooldown() {
    const seconds = Number.parseInt(cooldownInput, 10)
    if (!Number.isInteger(seconds) || seconds < 1) return
    onCooldownSecondsChange(seconds)
  }

  return <section className="provider-key-list" aria-label="Chaves de API">
    <ul>
      {keys.map((key, index) => <li key={key.id}>
        <span className="provider-key-list__label">{key.label ?? `Chave ${index + 1}`}</span>
        {index === 0 && <span className="provider-key-list__badge">Principal</span>}
        <span className="provider-key-list__status" role="status">
          {key.status === 'cooldown' ? `Em cooldown até ${formatCooldown(key.cooldownUntil)}` : 'Ativa'}
        </span>
        <button type="button" aria-label="Mover para cima" disabled={pending || index === 0} onClick={() => onMoveUp(key.id)}>↑</button>
        <button type="button" aria-label="Mover para baixo" disabled={pending || index === keys.length - 1} onClick={() => onMoveDown(key.id)}>↓</button>
        <button type="button" aria-label="Remover chave" disabled={pending} onClick={() => onRemove(key.id)}>Remover</button>
        <input
          aria-label="Apelido"
          value={key.label ?? ''}
          onChange={(event) => onRename(key.id, event.target.value || null)}
          disabled={pending}
        />
      </li>)}
    </ul>
    <div className="provider-key-list__add">
      <label htmlFor="provider-key-list-new-key">Nova chave</label>
      <input id="provider-key-list-new-key" type="password" autoComplete="off" value={newKey} onChange={(event) => setNewKey(event.target.value)} />
      <label htmlFor="provider-key-list-new-label">Apelido (opcional)</label>
      <input id="provider-key-list-new-label" type="text" value={newLabel} onChange={(event) => setNewLabel(event.target.value)} />
      <button type="button" disabled={pending || !newKey} onClick={submit}>Adicionar chave</button>
    </div>
    <div className="provider-key-list__cooldown">
      <label htmlFor="provider-key-list-cooldown">Tempo de cooldown (s)</label>
      <input id="provider-key-list-cooldown" type="number" min={1} value={cooldownInput} onChange={(event) => setCooldownInput(event.target.value)} />
      <button type="button" disabled={pending || cooldownInput === String(cooldownSeconds)} onClick={submitCooldown}>Salvar tempo de cooldown</button>
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

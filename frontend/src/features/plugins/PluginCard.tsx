import { useState } from 'react'
import { removePlugin, setPluginEnabled, type PluginSummary } from '../../api/plugins'
import type { ApiClient } from '../../api/client'

const STATE_LABEL: Record<PluginSummary['state'], string> = {
  active: 'Ativo',
  disabled: 'Desligado',
  pending_approval: 'Aguardando aprovação',
  rejected: 'Rejeitado',
}

export function PluginCard({ plugin, client, onChanged }: { plugin: PluginSummary; client: ApiClient; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  async function toggle() { setBusy(true); try { await setPluginEnabled(client, plugin.plugin_id, plugin.state !== 'active'); onChanged() } finally { setBusy(false) } }
  async function remove() { if (!confirm) { setConfirm(true); return }; setBusy(true); try { await removePlugin(client, plugin.plugin_id); onChanged() } finally { setBusy(false) } }
  return <article className="plugin-card" aria-label={plugin.display_name}>
    <header className="plugin-card__head">
      <button type="button" className="plugin-card__toggle" aria-expanded={open} aria-controls={`plugin-${plugin.plugin_id}-details`} onClick={() => setOpen((value) => !value)}>
        <span className="plugin-card__icon" aria-hidden="true">✦</span>
        <span className="plugin-card__identity"><strong>{plugin.display_name}</strong><span>{plugin.author || 'Autor não informado'} <code>v{plugin.version}</code></span></span>
        <span className="plugin-card__chevron" aria-hidden="true">{open ? '−' : '+'}</span>
      </button>
      <span className={`plugin-card__state is-${plugin.state}`}><span aria-hidden="true" />{STATE_LABEL[plugin.state]}</span>
    </header>
    <div className="plugin-card__summary"><span>{plugin.contribution_count} {plugin.contribution_count === 1 ? 'contribuição' : 'contribuições'}</span>{plugin.homepage && <a href={plugin.homepage} target="_blank" rel="noreferrer">Abrir página <span aria-hidden="true">↗</span></a>}</div>
    {plugin.warnings.map((warning) => <p className="plugin-card__warning" role="alert" key={warning}><span aria-hidden="true">!</span>{warning}</p>)}
    {open && <div className="plugin-card__body" id={`plugin-${plugin.plugin_id}-details`}><p className="plugin-card__description">{plugin.description || 'Sem descrição disponível para este plugin.'}</p><dl className="plugin-card__facts"><div><dt>Identificador</dt><dd><code>{plugin.plugin_id}</code></dd></div><div><dt>Contribuições</dt><dd>{plugin.contribution_count}</dd></div></dl><div className="plugin-card__actions"><button type="button" className="button button--secondary" onClick={() => void toggle()} disabled={busy || plugin.state === 'pending_approval'}>{busy ? 'Atualizando…' : plugin.state === 'active' ? 'Desligar plugin' : 'Ativar plugin'}</button><button type="button" className="button button--quiet plugin-card__remove" onClick={() => void remove()} disabled={busy}>{confirm ? 'Confirmar remoção' : 'Remover plugin'}</button></div></div>}
  </article>
}

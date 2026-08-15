import { useState } from 'react'
import { removePlugin, setPluginEnabled, type PluginSummary } from '../../api/plugins'
import type { ApiClient } from '../../api/client'

export function PluginCard({ plugin, client, onChanged }: { plugin: PluginSummary; client: ApiClient; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  async function toggle() { setBusy(true); try { await setPluginEnabled(client, plugin.plugin_id, plugin.state !== 'active'); onChanged() } finally { setBusy(false) } }
  async function remove() { if (!confirm) { setConfirm(true); return }; setBusy(true); try { await removePlugin(client, plugin.plugin_id); onChanged() } finally { setBusy(false) } }
  return <article className="plugin-card" aria-label={plugin.display_name}>
    <header className="plugin-card__head"><button type="button" className="plugin-card__toggle" aria-expanded={open} onClick={() => setOpen((value) => !value)}><strong>{plugin.display_name}</strong><span><code>v{plugin.version}</code> · {plugin.contribution_count} contribuições</span></button><span className={`plugin-card__state is-${plugin.state}`}>{plugin.state}</span></header>
    {plugin.warnings.map((warning) => <p className="plugin-card__warning" role="alert" key={warning}>{warning}</p>)}
    {open && <div className="plugin-card__body"><p>{plugin.description || 'Sem descrição.'}</p><div className="plugin-card__actions"><button type="button" onClick={() => void toggle()} disabled={busy || plugin.state === 'pending_approval'}>{plugin.state === 'active' ? 'Desligar' : 'Ativar'}</button><button type="button" onClick={() => void remove()} disabled={busy}>{confirm ? 'Confirmar remoção' : 'Remover'}</button></div></div>}
  </article>
}

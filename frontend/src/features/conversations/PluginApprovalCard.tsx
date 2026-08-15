import { useState } from 'react'
import type { PluginApprovalRequest } from './activityTypes'

type Props = { plugin: PluginApprovalRequest; active: boolean; onApprove: () => Promise<void>; onDecline: () => Promise<void> }

export function PluginApprovalCard({ plugin, active, onApprove, onDecline }: Props) {
  const [busy, setBusy] = useState<'approve' | 'decline' | null>(null)
  const [error, setError] = useState<string | null>(null)
  async function act(kind: 'approve' | 'decline') {
    if (!active || busy) return
    setBusy(kind); setError(null)
    try { await (kind === 'approve' ? onApprove() : onDecline()) } catch { setError('Não foi possível registrar esta decisão. Tente novamente.') } finally { setBusy(null) }
  }
  return <section className="approval-card" data-active={active} aria-label={`Instalar ${plugin.display_name}`}>
    <header className="approval-card__header"><span className="approval-card__glyph" aria-hidden="true">✦</span><div><strong>{plugin.display_name} · v{plugin.version}</strong><p>{plugin.description || 'Plugin declarativo'} · {plugin.author || 'autor não informado'}</p></div></header>
    {plugin.skills.length + plugin.mcp_servers.length + plugin.agents.length === 0 ? <p className="approval-card__error">Este plugin não oferece contribuições compatíveis com o Orin.</p> : <div className="approval-card__form">
      <p className="approval-card__hint">Contribuições que serão adicionadas:</p>
      <ul>{plugin.skills.map((item) => <li key={item.skill_id}>Skill · {item.name}</li>)}{plugin.mcp_servers.map((item) => <li key={item.slug}>MCP · {item.display_name} <small>(precisa de aprovação separada)</small></li>)}{plugin.agents.map((item) => <li key={item.agent_id}>Subagente · {item.name}</li>)}</ul>
      {plugin.warnings.map((warning) => <p className="approval-card__error" role="alert" key={warning}>{warning}</p>)}
      {active && <footer className="approval-card__actions"><button type="button" className="approval-card__decline" onClick={() => void act('decline')} disabled={busy !== null}>{busy === 'decline' ? 'Recusando…' : 'Recusar'}</button><button type="button" className="approval-card__submit" onClick={() => void act('approve')} disabled={busy !== null}>{busy === 'approve' ? 'Instalando…' : 'Instalar'}</button></footer>}
      {error && <p className="approval-card__error" role="alert">{error}</p>}
    </div>}
  </section>
}

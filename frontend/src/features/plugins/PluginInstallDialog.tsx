import { useState, type FormEvent } from 'react'
import { approvePlugin, inspectPlugin, type PluginInspectionResult } from '../../api/plugins'
import type { ApiClient } from '../../api/client'

export function PluginInstallDialog({ client, onClose, onInstalled }: { client: ApiClient; onClose: () => void; onInstalled: () => void }) {
  const [reference, setReference] = useState('')
  const [inspection, setInspection] = useState<PluginInspectionResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function inspect(event: FormEvent) { event.preventDefault(); if (!reference.trim()) return; setBusy(true); setError(null); try { setInspection(await inspectPlugin(client, reference.trim())) } catch { setError('Não foi possível inspecionar este plugin.') } finally { setBusy(false) } }
  async function install() { if (!inspection) return; setBusy(true); try { await approvePlugin(client, inspection.plugin_id); onInstalled() } catch { setError('Não foi possível instalar o plugin.') } finally { setBusy(false) } }
  return <div className="plugin-dialog" role="dialog" aria-label="Instalar plugin"><header><h2>Instalar plugin</h2><button type="button" onClick={onClose}>Fechar</button></header><form onSubmit={(event) => void inspect(event)}><label>URL, owner/repo ou nome<input value={reference} onChange={(event) => setReference(event.target.value)} /></label><button type="submit" disabled={busy}>{busy ? 'Inspecionando…' : 'Inspecionar'}</button></form>{inspection && <div className="plugin-dialog__preview"><h3>{inspection.display_name} · v{inspection.version}</h3><p>{inspection.description}</p><p>{inspection.contribution_count} contribuições · {inspection.warnings.length} aviso(s)</p><button type="button" onClick={() => void install()} disabled={busy}>Confirmar instalação</button></div>}{error && <p role="alert">{error}</p>}</div>
}

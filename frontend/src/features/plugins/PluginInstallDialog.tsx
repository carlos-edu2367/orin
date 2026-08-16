import { useEffect, useRef, useState, type FormEvent } from 'react'
import { approvePlugin, inspectPlugin, type PluginInspectionResult } from '../../api/plugins'
import type { ApiClient } from '../../api/client'

export function PluginInstallDialog({ client, onClose, onInstalled, initialReference }: { client: ApiClient; onClose: () => void; onInstalled: () => void; initialReference?: string }) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const [reference, setReference] = useState(initialReference ?? '')
  const [inspection, setInspection] = useState<PluginInspectionResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    dialogRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])
  async function runInspection(value: string) { setBusy(true); setError(null); try { setInspection(await inspectPlugin(client, value)) } catch { setError('Não foi possível inspecionar este plugin.') } finally { setBusy(false) } }
  useEffect(() => { if (initialReference) void runInspection(initialReference) }, [initialReference])
  async function inspect(event: FormEvent) { event.preventDefault(); if (!reference.trim()) return; void runInspection(reference.trim()) }
  async function install() { if (!inspection) return; setBusy(true); try { await approvePlugin(client, inspection.plugin_id); onInstalled() } catch { setError('Não foi possível instalar o plugin.') } finally { setBusy(false) } }
  return <div className="plugin-dialog__backdrop"><div className="plugin-dialog" role="dialog" aria-modal="true" aria-labelledby="plugin-dialog-title" tabIndex={-1} ref={dialogRef}><header className="plugin-dialog__head"><div><span className="plugin-dialog__eyebrow">PLUGIN INSTALLER</span><h2 id="plugin-dialog-title">Instalar plugin</h2></div><button type="button" className="button--quiet" onClick={onClose}>Fechar</button></header><p className="plugin-dialog__lede">Inspecione a origem primeiro. A instalação só acontece depois da sua aprovação.</p><form className="plugin-dialog__form" onSubmit={(event) => void inspect(event)}><label htmlFor="plugin-reference">URL, owner/repo ou nome<input id="plugin-reference" value={reference} onChange={(event) => setReference(event.target.value)} placeholder="ex.: github.com/acme/meu-plugin" /></label><button type="submit" className="button button--primary" disabled={busy || !reference.trim()}>{busy ? 'Inspecionando…' : 'Inspecionar origem'}</button></form>{inspection && <div className="plugin-dialog__preview"><div className="plugin-dialog__preview-head"><span className="plugin-dialog__preview-icon" aria-hidden="true">✦</span><div><h3>{inspection.display_name}</h3><p>{inspection.author || 'Autor não informado'} · <code>v{inspection.version}</code></p></div></div><p className="plugin-dialog__description">{inspection.description || 'Sem descrição disponível.'}</p><dl className="plugin-dialog__facts"><div><dt>Contribuições</dt><dd>{inspection.contribution_count}</dd></div><div><dt>Avisos</dt><dd className={inspection.warnings.length > 0 ? 'has-warning' : undefined}>{inspection.warnings.length}</dd></div></dl>{inspection.warnings.length > 0 && <ul className="plugin-dialog__warnings">{inspection.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}<button type="button" className="button button--primary" onClick={() => void install()} disabled={busy}>{busy ? 'Instalando…' : 'Confirmar instalação'}</button></div>}{error && <p className="plugin-dialog__error" role="alert">{error}</p>}</div></div>
}

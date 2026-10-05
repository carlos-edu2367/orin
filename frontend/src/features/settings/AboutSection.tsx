import { useEffect, useState } from 'react'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import { getInstallationStatus, removeInstalledVersion, type InstallationStatus } from '../../api/installation'
import { formatMegabytes, useUpdateJob } from '../updates/useUpdateJob'
import { useSession } from '../../app/useSession'
import { SettingsSection } from './SettingsSection'

export function AboutSection({ client: providedClient }: { client?: ApiClient }) {
  // createBrowserApiClient() builds a new instance every call; falling back to
  // it inline here would give the effect below a new `client` identity on
  // every render (its own setStatus/setError already trigger one), looping
  // forever. The lazy useState initializer runs createBrowserApiClient() at
  // most once, on mount, so the fallback client stays stable across renders
  // exactly like an explicitly passed one.
  const [client] = useState(() => providedClient ?? createBrowserApiClient())
  const { capabilities } = useSession()
  const [status, setStatus] = useState<InstallationStatus | null>(null)
  const [error, setError] = useState(false)
  const [busy, setBusy] = useState(false)
  const job = useUpdateJob(client, capabilities.ui_updater)
  const [restarting, setRestarting] = useState(false)
  const load = () => { setError(false); void getInstallationStatus(client).then(setStatus).catch(() => setError(true)) }
  useEffect(() => {
    let active = true
    getInstallationStatus(client).then((value) => { if (active) setStatus(value) }).catch(() => { if (active) setError(true) })
    return () => { active = false }
  }, [client])
  async function remove(version: string) {
    if (busy || !status || !window.confirm(`Excluir a versão ${version}? A versão atual nunca é removida.`)) return
    setBusy(true)
    try { await removeInstalledVersion(client, version); load() } catch { setError(true) } finally { setBusy(false) }
  }
  async function restart() {
    if (restarting) return
    setRestarting(true)
    if (!(await window.orinDesktop?.applyUpdate?.())) setRestarting(false)
  }
  const canInstall = status?.installation_kind === 'installed' && status?.update_available === true
  return <SettingsSection eyebrow="SISTEMA / INSTALAÇÃO"><section className="installation-status" aria-labelledby="about-installation-title"><div className="installation-status__heading"><div><p className="eyebrow">SOBRE</p><h2 id="about-installation-title">Estado do Orin</h2></div><button type="button" className="button button--quiet" onClick={load} disabled={busy}>Verificar release</button></div>{error && <p role="alert">Não foi possível consultar a instalação.</p>}{status && <><dl className="installation-status__summary"><div><dt>Versão atual</dt><dd><code>v{status.current_version}</code></dd></div><div><dt>Release mais recente</dt><dd>{status.latest_release ? <a href={status.latest_release.url} target="_blank" rel="noreferrer"><code>v{status.latest_release.version}</code></a> : 'Indisponível no momento'}</dd></div></dl>{!capabilities.ui_updater && <p className="installation-status__managed">A atualização desta instância é feita por quem administra o servidor.</p>}{capabilities.ui_updater && <UpdatePanel job={job} canInstall={canInstall} latest={status.latest_release?.version} desktop={Boolean(window.orinDesktop?.applyUpdate)} restarting={restarting} onRestart={() => void restart()} />}<div className="installation-status__versions"><h3>Versões instaladas</h3>{status.installed_versions.map((item) => <div className={`installation-version${item.is_current ? ' is-current' : ''}`} key={item.version}><code>v{item.version}</code>{item.removable && capabilities.ui_updater ? <button type="button" onClick={() => void remove(item.version)} disabled={busy}>Excluir versão antiga</button> : <span className="installation-version__protected">Protegida</span>}</div>)}</div></>}</section></SettingsSection>
}

type UpdatePanelProps = {
  job: ReturnType<typeof useUpdateJob>
  canInstall: boolean
  latest?: string
  desktop: boolean
  restarting: boolean
  onRestart: () => void
}

/** Download in the background, then restart onto it: the same flow as the desktop banner. */
function UpdatePanel({ job, canInstall, latest, desktop, restarting, onRestart }: UpdatePanelProps) {
  const state = job.status?.state ?? 'idle'
  const working = ['checking', 'downloading', 'verifying', 'extracting', 'validating'].includes(state)
  if (state === 'ready') {
    return <div className="installation-status__update" role="status">
      <p>Versão <code>v{job.status?.version}</code> baixada e verificada.</p>
      {desktop
        ? <button type="button" className="button button--primary" onClick={onRestart} disabled={restarting}>{restarting ? 'Reiniciando…' : 'Reiniciar para atualizar'}</button>
        : <p className="installation-status__managed">Para concluir, feche o Orin e rode <code>orin update</code> no terminal.</p>}
    </div>
  }
  if (working) {
    const percent = job.status?.progress != null ? Math.round(job.status.progress * 100) : null
    const bytes = job.status?.bytes_total ? ` · ${formatMegabytes(job.status.bytes_done)} de ${formatMegabytes(job.status.bytes_total)}` : ''
    return <div className="installation-status__update" role="status">
      <p>{job.status?.label ?? 'Preparando'}{percent !== null ? ` — ${percent}%` : ''}{bytes}</p>
      <progress max={100} value={percent ?? undefined} aria-label="Progresso do download da atualização" />
    </div>
  }
  if (state === 'failed') {
    return <div className="installation-status__update">
      <p role="alert">{job.status?.error?.message ?? 'Não foi possível baixar a nova versão.'} {job.status?.error?.hint}</p>
      <button type="button" className="button button--primary" onClick={() => void job.start()}>Tentar novamente</button>
    </div>
  }
  if (!canInstall) return null
  return <div className="installation-status__update">
    <button type="button" className="button button--primary" onClick={() => void job.start()}>Baixar v{latest}</button>
    {job.requestFailed && <p role="alert">Não foi possível iniciar o download da nova versão.</p>}
  </div>
}

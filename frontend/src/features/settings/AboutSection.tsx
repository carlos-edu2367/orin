import { useEffect, useState } from 'react'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import { getInstallationStatus, installLatestRelease, removeInstalledVersion, type InstallationStatus } from '../../api/installation'
import { SettingsSection } from './SettingsSection'

export function AboutSection({ client: providedClient }: { client?: ApiClient }) {
  // createBrowserApiClient() builds a new instance every call; falling back to
  // it inline here would give the effect below a new `client` identity on
  // every render (its own setStatus/setError already trigger one), looping
  // forever. The lazy useState initializer runs createBrowserApiClient() at
  // most once, on mount, so the fallback client stays stable across renders
  // exactly like an explicitly passed one.
  const [client] = useState(() => providedClient ?? createBrowserApiClient())
  const [status, setStatus] = useState<InstallationStatus | null>(null)
  const [error, setError] = useState(false)
  const [busy, setBusy] = useState(false)
  const [installState, setInstallState] = useState<'idle' | 'installing' | 'installed' | 'failed'>('idle')
  const load = () => { setError(false); setInstallState('idle'); void getInstallationStatus(client).then(setStatus).catch(() => setError(true)) }
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
  async function install() {
    if (busy || installState === 'installing' || !status?.latest_release || status.installation_kind !== 'installed') return
    setInstallState('installing')
    try { await installLatestRelease(client); setInstallState('installed') } catch { setInstallState('failed') }
  }
  const canInstall = status?.installation_kind === 'installed' && status?.update_available === true
  return <SettingsSection eyebrow="SISTEMA / INSTALAÇÃO"><section className="installation-status" aria-labelledby="about-installation-title"><div className="installation-status__heading"><div><p className="eyebrow">SOBRE</p><h2 id="about-installation-title">Estado do Orin</h2></div><button type="button" className="button button--quiet" onClick={load} disabled={busy}>Verificar release</button></div>{error && <p role="alert">Não foi possível consultar a instalação.</p>}{status && <><dl className="installation-status__summary"><div><dt>Versão atual</dt><dd><code>v{status.current_version}</code></dd></div><div><dt>Release mais recente</dt><dd>{status.latest_release ? <a href={status.latest_release.url} target="_blank" rel="noreferrer"><code>v{status.latest_release.version}</code></a> : 'Indisponível no momento'}</dd></div></dl>{canInstall && installState !== 'installed' && <div className="installation-status__update"><button type="button" className="button button--primary" onClick={() => void install()} disabled={installState === 'installing'}>{installState === 'installing' ? 'Instalando…' : `Instalar v${status.latest_release?.version}`}</button>{installState === 'failed' && <p role="alert">Não foi possível instalar a nova versão.</p>}</div>}{installState === 'installed' && <p role="status">Nova versão instalada. Feche e abra o Orin novamente para usá-la.</p>}<div className="installation-status__versions"><h3>Versões instaladas</h3>{status.installed_versions.map((item) => <div className={`installation-version${item.is_current ? ' is-current' : ''}`} key={item.version}><code>v{item.version}</code>{item.removable ? <button type="button" onClick={() => void remove(item.version)} disabled={busy}>Excluir versão antiga</button> : <span className="installation-version__protected">Protegida</span>}</div>)}</div></>}</section></SettingsSection>
}

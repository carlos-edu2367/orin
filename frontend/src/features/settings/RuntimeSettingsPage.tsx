import { useEffect, useMemo, useState } from 'react'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import { getInstallationStatus, removeInstalledVersion, type InstallationStatus } from '../../api/installation'
import { getAgentRuntimeSettings, setAgentRuntimeSettings } from '../../api/runtime'
import { SettingsPage } from './SettingsPage'

export function RuntimeSettingsPage({ client }: { client?: ApiClient }) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const [maxIterations, setMaxIterations] = useState<number | null>(null)
  const [draft, setDraft] = useState('24')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [installation, setInstallation] = useState<InstallationStatus | null>(null)
  const [installationLoading, setInstallationLoading] = useState(true)
  const [installationBusy, setInstallationBusy] = useState(false)
  const [installationNotice, setInstallationNotice] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void getAgentRuntimeSettings(apiClient, controller.signal).then((settings) => {
      setMaxIterations(settings.max_iterations)
      if (settings.max_iterations !== null) setDraft(String(settings.max_iterations))
    }).catch(() => setNotice('Não foi possível carregar o limite atual.')).finally(() => setLoading(false))
    return () => controller.abort()
  }, [apiClient])

  async function refreshInstallation() {
    setInstallationLoading(true)
    setInstallationNotice(null)
    try {
      setInstallation(await getInstallationStatus(apiClient))
    } catch {
      setInstallationNotice('Não foi possível consultar o estado da instalação.')
    } finally {
      setInstallationLoading(false)
    }
  }

  useEffect(() => {
    void refreshInstallation()
  }, [apiClient])

  async function removeVersion(version: string) {
    if (!installation || installationBusy || !window.confirm(`Excluir a versão ${version}? A versão atual nunca é removida.`)) return
    setInstallationBusy(true)
    setInstallationNotice(null)
    try {
      await removeInstalledVersion(apiClient, version)
      await refreshInstallation()
      setInstallationNotice(`Versão ${version} excluída.`)
    } catch {
      setInstallationNotice('Não foi possível excluir essa versão.')
    } finally {
      setInstallationBusy(false)
    }
  }

  async function save() {
    const next = maxIterations === null ? null : Number(draft)
    if (next !== null && (!Number.isInteger(next) || next < 1)) {
      setNotice('Informe um número inteiro maior que zero.')
      return
    }
    setSaving(true)
    setNotice(null)
    try {
      const settings = await setAgentRuntimeSettings(apiClient, next)
      setMaxIterations(settings.max_iterations)
      if (settings.max_iterations !== null) setDraft(String(settings.max_iterations))
      setNotice('Limite salvo.')
    } catch {
      setNotice('Não foi possível salvar o limite.')
    } finally {
      setSaving(false)
    }
  }

  return <SettingsPage>
    <p className="eyebrow">RUNTIME</p>
    <h1>General</h1>
    <p className="settings-content__lede">Estado do Orin nesta instalação e limites de execução do agente.</p>
    <section className="installation-status" aria-busy={installationLoading} aria-labelledby="installation-status-title">
      <div className="installation-status__heading">
        <div><p className="eyebrow">INSTALAÇÃO</p><h2 id="installation-status-title">Estado do Orin</h2></div>
        <button type="button" className="button button--quiet" onClick={() => void refreshInstallation()} disabled={installationLoading || installationBusy}>Verificar release</button>
      </div>
      {installationLoading && <p className="installation-status__muted">Consultando a instalação…</p>}
      {installation && <>
        <dl className="installation-status__summary">
          <div><dt>Versão atual</dt><dd><code>v{installation.current_version}</code><span className="installation-status__badge">Em uso</span></dd></div>
          <div><dt>Release mais recente</dt><dd>{installation.latest_release ? <a href={installation.latest_release.url} target="_blank" rel="noreferrer"><code>v{installation.latest_release.version}</code></a> : <span>Indisponível no momento</span>}</dd></div>
        </dl>
        {installation.latest_release && installation.latest_release.version !== installation.current_version && <p className="installation-status__update" role="status">Há uma versão mais recente disponível para download.</p>}
        {installation.latest_release_error && <p className="installation-status__muted" role="status">Não foi possível verificar a release mais recente agora.</p>}
        <div className="installation-status__versions">
          <div className="installation-status__versions-heading"><h3>Versões instaladas</h3><span>{installation.installed_versions.length || '—'}</span></div>
          {installation.installed_versions.length ? <div className="installation-version-list">
            {installation.installed_versions.map((item) => <div className={`installation-version${item.is_current ? ' is-current' : ''}`} key={item.version}>
              <div><code>v{item.version}</code>{item.is_current && <span className="installation-status__badge">Atual</span>}</div>
              {item.removable ? <button type="button" onClick={() => void removeVersion(item.version)} disabled={installationBusy}>Excluir versão antiga</button> : <span className="installation-version__protected">Protegida</span>}
            </div>)}
          </div> : <p className="installation-status__muted">As versões gerenciadas aparecem quando o Orin é instalado pelo pacote do Windows.</p>}
        </div>
      </>}
      {installationNotice && <p className="installation-status__notice" role="status">{installationNotice}</p>}
    </section>
    <h2 className="runtime-settings__title">Interações do agente</h2>
    <p className="settings-content__lede">Defina quantas rodadas de ferramentas e raciocínio um turno pode executar. Sem limite remove os tetos de rodadas e ações, inclusive para subagentes; a proteção contra processos travados continua ativa.</p>
    <section className="runtime-settings" aria-busy={loading}>
      <label className="provider-panel__toggle"><input type="checkbox" checked={maxIterations === null} disabled={loading || saving} onChange={(event) => setMaxIterations(event.target.checked ? null : Number(draft) || 24)} />Sem limite de interações</label>
      <label className="runtime-settings__field">Máximo de interações por turno<input aria-label="Máximo de interações por turno" type="number" min="1" step="1" value={draft} disabled={loading || saving || maxIterations === null} onChange={(event) => setDraft(event.target.value)} /></label>
      <p>Uma interação é uma nova rodada do agente após receber o resultado de uma ferramenta. O limite não interrompe uma resposta já em texto.</p>
      <button type="button" className="button button--primary" disabled={loading || saving} onClick={() => void save()}>Salvar limite</button>
      {notice && <p role="status" className="runtime-settings__notice">{notice}</p>}
    </section>
  </SettingsPage>
}

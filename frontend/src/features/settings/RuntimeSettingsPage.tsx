import { useEffect, useMemo, useState } from 'react'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import { getInstallationStatus, removeInstalledVersion, type InstallationStatus } from '../../api/installation'
import { getAgentRuntimeSettings, setAgentRuntimeSettings } from '../../api/runtime'
import { getCodeModeSettings, setCodeModeSettings, type CodeAutonomy, type CodeModeSettings } from '../../api/codeMode'
import { CustomSelect } from '../../components/CustomSelect'
import { SettingsPage } from './SettingsPage'
import { SettingsSection } from './SettingsSection'
import { requestSystemNotifications } from '../notifications/systemNotify'

const CODE_AUTONOMY_OPTIONS = [
  {
    value: 'approval_required',
    label: 'Sempre pedir aprovação',
    description: 'Solicita confirmação antes de editar arquivos ou executar comandos.',
  },
  {
    value: 'code_autonomy',
    label: 'Autonomia de código',
    description: 'Edita arquivos e roda testes livremente; pede confirmação para ações externas.',
  },
  {
    value: 'full_autonomy',
    label: 'Autonomia total',
    description: 'Executa todo o ciclo autonomamente (deploy em produção sempre pede confirmação).',
  },
] as const

export function RuntimeSettingsPage({ client, embedded = false }: { client?: ApiClient; embedded?: boolean }) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const [maxIterations, setMaxIterations] = useState<number | null>(null)
  const [draft, setDraft] = useState('24')
  const [subagentMaxIterations, setSubagentMaxIterations] = useState<number | null>(null)
  const [subagentDraft, setSubagentDraft] = useState('48')
  const [subagentConfigured, setSubagentConfigured] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [installation, setInstallation] = useState<InstallationStatus | null>(null)
  const [installationLoading, setInstallationLoading] = useState(true)
  const [installationBusy, setInstallationBusy] = useState(false)
  const [installationNotice, setInstallationNotice] = useState<string | null>(null)
  const [codeSettings, setCodeSettings] = useState<CodeModeSettings | null>(null)
  const [savingCodeSettings, setSavingCodeSettings] = useState(false)
  const [codeNotice, setCodeNotice] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void getAgentRuntimeSettings(apiClient, controller.signal).then((settings) => {
      setMaxIterations(settings.max_iterations)
      if (settings.max_iterations !== null) setDraft(String(settings.max_iterations))
      const hasSub = settings.subagent_max_iterations !== undefined
      setSubagentConfigured(hasSub)
      const resolvedSub = hasSub ? (settings.subagent_max_iterations ?? null) : settings.max_iterations
      setSubagentMaxIterations(resolvedSub)
      if (resolvedSub !== null) setSubagentDraft(String(resolvedSub))
    }).catch(() => setNotice('Não foi possível carregar o limite atual.')).finally(() => setLoading(false))
    return () => controller.abort()
  }, [apiClient])

  useEffect(() => {
    const controller = new AbortController()
    void getCodeModeSettings(apiClient, controller.signal).then((settings) => {
      if (!controller.signal.aborted) setCodeSettings(settings)
    }).catch(() => { if (!controller.signal.aborted) setCodeNotice('Não foi possível carregar as preferências do Modo Code.') })
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
    let active = true
    getInstallationStatus(apiClient).then((value) => { if (active) setInstallation(value) }).catch(() => { if (active) setInstallationNotice('Não foi possível consultar o estado da instalação.') }).finally(() => { if (active) setInstallationLoading(false) })
    return () => { active = false }
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
    const nextSub = subagentMaxIterations === null ? null : Number(subagentDraft)
    if (subagentConfigured && nextSub !== null && (!Number.isInteger(nextSub) || nextSub < 1)) {
      setNotice('Informe um número inteiro maior que zero para subagentes.')
      return
    }
    setSaving(true)
    setNotice(null)
    try {
      const settings = await setAgentRuntimeSettings(apiClient, next, subagentConfigured ? nextSub : undefined)
      setMaxIterations(settings.max_iterations)
      if (settings.max_iterations !== null) setDraft(String(settings.max_iterations))
      const resolvedSub = settings.subagent_max_iterations !== undefined ? settings.subagent_max_iterations : settings.max_iterations
      setSubagentMaxIterations(resolvedSub)
      if (resolvedSub !== null) setSubagentDraft(String(resolvedSub))
      setNotice('Limite salvo.')
    } catch {
      setNotice('Não foi possível salvar o limite.')
    } finally {
      setSaving(false)
    }
  }

  /** W7: in a browser, turning notifications on asks for its permission (the desktop app needs none). */
  async function askNotificationPermission() {
    const answer = await requestSystemNotifications()
    if (answer === 'denied') setCodeNotice('O navegador bloqueou as notificações deste site; libere-as nas configurações do navegador.')
    else if (answer === 'unsupported') setCodeNotice('Este navegador não mostra notificações do sistema.')
  }

  async function saveCodeSettings() {
    if (!codeSettings || savingCodeSettings) return
    setSavingCodeSettings(true)
    setCodeNotice(null)
    try {
      setCodeSettings(await setCodeModeSettings(apiClient, codeSettings))
      setCodeNotice('Preferências do Modo Code salvas.')
    } catch {
      setCodeNotice('Não foi possível salvar as preferências do Modo Code.')
    } finally {
      setSavingCodeSettings(false)
    }
  }

  const content = <>
    {!embedded && <section className="installation-status" aria-busy={installationLoading} aria-labelledby="installation-status-title">
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
    </section>}
    <h2 className="runtime-settings__title">Interações do agente</h2>
    <p className="settings-content__lede">Defina quantas rodadas de ferramentas e raciocínio o agente principal e os subagentes podem executar por turno. Sem limite remove os tetos de rodadas e ações; a proteção contra processos travados continua ativa.</p>
    <section className="runtime-settings" aria-busy={loading}>
      <label className="provider-panel__toggle"><input type="checkbox" checked={maxIterations === null} disabled={loading || saving} onChange={(event) => setMaxIterations(event.target.checked ? null : Number(draft) || 24)} />Sem limite de interações</label>
      <label className="runtime-settings__field">Máximo de interações por turno<input aria-label="Máximo de interações por turno" type="number" min="1" step="1" value={draft} disabled={loading || saving || maxIterations === null} onChange={(event) => setDraft(event.target.value)} /></label>
      <label className="provider-panel__toggle"><input type="checkbox" data-testid="subagent-unlimited-toggle" checked={subagentMaxIterations === null} disabled={loading || saving} onChange={(event) => { setSubagentConfigured(true); setSubagentMaxIterations(event.target.checked ? null : Number(subagentDraft) || 48) }} />Sem limite de interações em subagentes</label>
      <label className="runtime-settings__field">Máximo de interações por subagente<input aria-label="Máximo de interações por subagente" data-testid="subagent-max-iterations-input" type="number" min="1" step="1" value={subagentDraft} disabled={loading || saving || subagentMaxIterations === null} onChange={(event) => { setSubagentConfigured(true); setSubagentDraft(event.target.value) }} /></label>
      <p>Uma interação é uma nova rodada do agente após receber o resultado de uma ferramenta. O limite não interrompe uma resposta já em texto.</p>
      <button type="button" className="button button--primary" disabled={loading || saving} onClick={() => void save()}>Salvar limite</button>
      {notice && <p role="status" className="runtime-settings__notice">{notice}</p>}
    </section>
    <section className="runtime-settings code-mode-settings" aria-busy={codeSettings === null} aria-labelledby="code-mode-settings-title">
      <div><p className="eyebrow">MODO CODE</p><h3 id="code-mode-settings-title">Autonomia e acompanhamento</h3></div>
      <p>O Modo Code planeja, testa e valida alterações antes de concluir. Deploy em produção sempre pede confirmação.</p>
      <div className="runtime-settings__field">
        <label htmlFor="code-mode-autonomy-select">Autonomia padrão</label>
        <CustomSelect
          id="code-mode-autonomy-select"
          ariaLabel="Autonomia padrão"
          value={codeSettings?.autonomy ?? 'approval_required'}
          disabled={codeSettings === null || savingCodeSettings}
          options={CODE_AUTONOMY_OPTIONS}
          onChange={(nextValue) =>
            setCodeSettings((current) =>
              current ? { ...current, autonomy: nextValue as CodeAutonomy } : current,
            )
          }
        />
      </div>
      <label className="provider-panel__toggle"><input type="checkbox" checked={codeSettings?.monitoring_enabled ?? true} disabled={codeSettings === null || savingCodeSettings} onChange={(event) => setCodeSettings((current) => current ? { ...current, monitoring_enabled: event.target.checked } : current)} />Acompanhar tarefas longas em segundo plano</label>
      <label className="provider-panel__toggle"><input type="checkbox" checked={codeSettings?.system_notifications ?? false} disabled={codeSettings === null || savingCodeSettings} onChange={(event) => { const checked = event.target.checked; setCodeSettings((current) => current ? { ...current, system_notifications: checked } : current); if (checked) void askNotificationPermission() }} />Receber notificações do sistema ao concluir, bloquear ou precisar de decisão</label>
      <button type="button" className="button button--primary" disabled={codeSettings === null || savingCodeSettings} onClick={() => void saveCodeSettings()}>{savingCodeSettings ? 'Salvando…' : 'Salvar preferências do Modo Code'}</button>
      {codeNotice && <p role="status" className="runtime-settings__notice">{codeNotice}</p>}
    </section>
  </>
  return embedded ? <SettingsSection eyebrow="RUNTIME">{content}</SettingsSection> : <SettingsPage><p className="eyebrow">RUNTIME</p><h1>General</h1><p className="settings-content__lede">Estado do Orin nesta instalação e limites de execução do agente.</p>{content}</SettingsPage>
}

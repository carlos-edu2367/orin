import { useEffect, useState } from 'react'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import { getBrowserEngineStatus, installBrowserEngine, type BrowserEngineStatus } from '../../api/browserEngine'
import { useSession } from '../../app/useSession'
import { SettingsSection } from './SettingsSection'

const POLL_MS = 1000

/**
 * Chromium is not part of the release: it is a ~150 MB download the agent only
 * needs to drive web pages. This is where the person installs it, and where the
 * agent sends them when it finds the engine missing.
 */
export function BrowserSection({ client: providedClient }: { client?: ApiClient }) {
  const [client] = useState(() => providedClient ?? createBrowserApiClient())
  const { capabilities } = useSession()
  const [status, setStatus] = useState<BrowserEngineStatus | null>(null)
  const [error, setError] = useState(false)
  const installing = status?.state === 'installing'

  useEffect(() => {
    let active = true
    getBrowserEngineStatus(client).then((value) => { if (active) setStatus(value) }).catch(() => { if (active) setError(true) })
    return () => { active = false }
  }, [client])

  useEffect(() => {
    if (!installing) return undefined
    const timer = window.setInterval(() => {
      getBrowserEngineStatus(client).then(setStatus).catch(() => setError(true))
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [client, installing])

  async function install() {
    if (installing) return
    setError(false)
    try { setStatus(await installBrowserEngine(client)) } catch { setError(true) }
  }

  const canInstall = capabilities.ui_updater
  return (
    <SettingsSection eyebrow="SISTEMA / NAVEGADOR">
      <section className="installation-status browser-engine" aria-labelledby="browser-engine-title">
        <div className="installation-status__heading">
          <div><p className="eyebrow">NAVEGADOR DO AGENTE</p><h2 id="browser-engine-title">Chromium</h2></div>
        </div>
        <p className="browser-engine__lede">Permite que o agente abra páginas que dependem de JavaScript, clique, preencha formulários e mostre capturas de tela. Não vem junto com o Orin para o instalador ficar menor; o download tem cerca de 150 MB.</p>
        {error && <p role="alert">Não foi possível falar com o instalador do navegador.</p>}
        {status?.state === 'ready' && <p role="status" className="browser-engine__ok">Instalado. O agente já pode usar o navegador.</p>}
        {status?.state === 'unsupported' && <p role="status">{status.error ?? 'O navegador não está disponível nesta instalação.'}</p>}
        {installing && (
          <div role="status" className="browser-engine__progress">
            <p>{status?.message ?? 'Instalando'}{status?.progress != null ? ` — ${status.progress}%` : ''}</p>
            <progress max={100} value={status?.progress ?? undefined} aria-label="Progresso da instalação do navegador" />
          </div>
        )}
        {status?.state === 'failed' && <p role="alert">{status.error ?? 'A instalação falhou.'} Você pode tentar de novo ou rodar <code>{status.install_command}</code> no terminal.</p>}
        {(status?.state === 'missing' || status?.state === 'failed') && (
          canInstall
            ? <div className="installation-status__update"><button type="button" className="button button--primary" onClick={() => void install()}>{status.state === 'failed' ? 'Tentar novamente' : 'Instalar navegador'}</button></div>
            : <p className="installation-status__managed">O navegador é instalado por quem administra o servidor.</p>
        )}
        {status?.state === 'missing' && <p className="browser-engine__hint">Prefere o terminal? Rode <code>{status.install_command}</code>.</p>}
      </section>
    </SettingsSection>
  )
}

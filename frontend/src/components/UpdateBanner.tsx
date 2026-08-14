import { useEffect, useState } from 'react'

export type DesktopUpdate = {
  currentVersion: string
  latestVersion: string
}

export function UpdateBanner() {
  const [update, setUpdate] = useState<DesktopUpdate | null>(null)
  const [status, setStatus] = useState<'ready' | 'updating' | 'failed'>('ready')

  useEffect(() => {
    const desktop = window.orinDesktop
    if (!desktop) return undefined
    return desktop.onUpdateAvailable((candidate) => {
      if (!candidate || typeof candidate.currentVersion !== 'string' || typeof candidate.latestVersion !== 'string') return
      setUpdate(candidate)
      setStatus('ready')
    })
  }, [])

  if (!update) return null

  async function updateApp() {
    if (status === 'updating') return
    setStatus('updating')
    const started = await window.orinDesktop?.runUpdate()
    if (!started) setStatus('failed')
  }

  return (
    <aside className="update-banner" role="status" aria-label="Atualização disponível">
      <div className="update-banner__copy">
        <span className="update-banner__eyebrow">Atualização disponível</span>
        <strong>Versão atual {update.currentVersion} - Versão mais recente {update.latestVersion}</strong>
        <span className="update-banner__hint">
          {status === 'failed' ? 'Não foi possível iniciar a atualização. Tente novamente.' : 'Atualize para receber as correções e melhorias mais recentes.'}
        </span>
      </div>
      <button className="button button--primary" type="button" onClick={() => void updateApp()} disabled={status === 'updating'}>
        {status === 'updating' ? 'Atualizando…' : 'Atualizar'}
      </button>
    </aside>
  )
}

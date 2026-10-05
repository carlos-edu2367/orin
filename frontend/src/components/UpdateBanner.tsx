import { useEffect, useState, type ReactNode } from 'react'
import { createBrowserApiClient } from '../api/client'
import type { UpdateAttempt, UpdateJobStatus } from '../api/updateJob'
import { formatMegabytes, useUpdateJob } from '../features/updates/useUpdateJob'

export type DesktopUpdate = {
  currentVersion: string
  latestVersion: string
}

const DISMISSED_KEY = 'orin.update.dismissed-attempt'

function readDismissed(): string | null {
  try { return window.localStorage.getItem(DISMISSED_KEY) } catch { return null }
}
function rememberDismissed(value: string): void {
  try { window.localStorage.setItem(DISMISSED_KEY, value) } catch { /* a private window just shows it again */ }
}

/** What the last activation did, worth telling once: it undid itself, or it worked. */
function attemptNotice(status: UpdateJobStatus | null, dismissed: string | null): { key: string; attempt: UpdateAttempt } | null {
  const attempt = status?.last_attempt
  if (!attempt || !attempt.at || attempt.at === dismissed) return null
  if (attempt.status === 'rolled_back') return { key: attempt.at, attempt }
  if (attempt.status === 'updated' && attempt.version === status?.current_version) return { key: attempt.at, attempt }
  return null
}

/**
 * The desktop update flow: learn that a release exists, download it in the
 * background (Orin keeps working), then offer one button to restart onto it.
 * Everything risky (verification, the switch, the automatic rollback) happens
 * in the backend and the CLI; this only reports it.
 */
export function UpdateBanner() {
  const desktop = typeof window !== 'undefined' ? window.orinDesktop : undefined
  const [client] = useState(() => createBrowserApiClient())
  const [announced, setAnnounced] = useState<{ currentVersion: string; latestVersion: string } | null>(null)
  const [restarting, setRestarting] = useState<'idle' | 'restarting' | 'failed'>('idle')
  const [dismissed, setDismissed] = useState<string | null>(readDismissed)
  const { status, start, requestFailed } = useUpdateJob(client, desktop !== undefined)

  useEffect(() => {
    if (!desktop) return undefined
    return desktop.onUpdateAvailable((candidate) => {
      if (!candidate || typeof candidate.currentVersion !== 'string' || typeof candidate.latestVersion !== 'string') return
      setAnnounced(candidate)
    })
  }, [desktop])

  if (!desktop) return null

  async function restart() {
    if (restarting === 'restarting') return
    setRestarting('restarting')
    const started = await window.orinDesktop?.applyUpdate?.()
    if (!started) setRestarting('failed')
  }

  const state = status?.state ?? 'idle'
  const version = status?.version ?? announced?.latestVersion ?? ''
  const notice = attemptNotice(status, dismissed)

  if (restarting === 'restarting') {
    return <Banner eyebrow="Atualizando" title={`Instalando a versão ${version}`} hint="O Orin vai fechar e abrir de novo sozinho em alguns segundos." />
  }
  if (state === 'ready') {
    return (
      <Banner eyebrow="Atualização pronta" title={`Versão ${version} baixada e verificada`}
        hint={restarting === 'failed' ? 'Não foi possível reiniciar. Feche o Orin e rode "orin update --apply" no terminal.' : 'Salve o que estiver fazendo: o Orin reinicia sozinho para concluir.'}
        notes={status?.notes ?? null}
        action={<button className="button button--primary" type="button" onClick={() => void restart()}>Reiniciar para atualizar</button>} />
    )
  }
  if (state === 'checking' || state === 'downloading' || state === 'verifying' || state === 'extracting' || state === 'validating') {
    const percent = status?.progress != null ? Math.round(status.progress * 100) : null
    const bytes = status?.bytes_total ? ` · ${formatMegabytes(status.bytes_done)} de ${formatMegabytes(status.bytes_total)}` : ''
    return (
      <Banner eyebrow="Baixando atualização" title={`${status?.label ?? 'Preparando'}${percent !== null ? ` — ${percent}%` : ''}`}
        hint={`Você pode continuar usando o Orin${bytes}.`}
        progress={percent} />
    )
  }
  if (state === 'failed') {
    return (
      <Banner eyebrow="Atualização" title={status?.error?.message ?? 'Não foi possível baixar a atualização.'} hint={status?.error?.hint ?? 'Tente de novo.'}
        action={<button className="button button--primary" type="button" onClick={() => void start()}>Tentar novamente</button>} />
    )
  }
  if (notice) {
    const { attempt } = notice
    const rolledBack = attempt.status === 'rolled_back'
    return (
      <Banner eyebrow={rolledBack ? 'Atualização desfeita' : 'Orin atualizado'}
        title={rolledBack ? `A versão ${attempt.version} não funcionou e o Orin voltou para a ${attempt.restored ?? status?.current_version}.` : `Agora você está na versão ${attempt.version}.`}
        hint={rolledBack ? 'Nada foi perdido. ' + (attempt.message ?? '') : 'As correções e melhorias já estão ativas.'}
        action={<button className="button button--quiet" type="button" onClick={() => { rememberDismissed(notice.key); setDismissed(notice.key) }}>Entendi</button>} />
    )
  }
  if (announced && (state === 'idle' || state === 'up_to_date')) {
    return (
      <Banner eyebrow="Atualização disponível" title={`Versão atual ${announced.currentVersion} - Versão mais recente ${announced.latestVersion}`}
        hint={requestFailed ? 'Não foi possível iniciar o download. Tente novamente.' : 'O download acontece em segundo plano, sem interromper seu trabalho.'}
        action={<button className="button button--primary" type="button" onClick={() => void start()}>Baixar atualização</button>} />
    )
  }
  return null
}

function Banner({ eyebrow, title, hint, notes, progress, action }: {
  eyebrow: string
  title: string
  hint: string
  notes?: string | null
  progress?: number | null
  action?: ReactNode
}) {
  return (
    <aside className="update-banner" role="status" aria-label={eyebrow}>
      <div className="update-banner__copy">
        <span className="update-banner__eyebrow">{eyebrow}</span>
        <strong>{title}</strong>
        {progress !== undefined && <progress className="update-banner__progress" max={100} value={progress ?? undefined} aria-label="Progresso do download da atualização" />}
        <span className="update-banner__hint">{hint}</span>
        {notes && <span className="update-banner__notes">{notes}</span>}
      </div>
      {action}
    </aside>
  )
}

/**
 * Shared pieces of the Orin Code settings editors (W7 Batch E): the folder they edit, loading, and errors.
 *
 * The editors work on the person's own files. The folder's layers (project and local) need a folder: the chat's
 * commands open an editor with `?conversation=<id>`, and the server resolves that conversation's folder; without it,
 * only the user's layer (and the managed policy, read-only) is shown.
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import type { SettingsLayerName } from '../../api/settings'

export const LAYER_LABEL: Record<SettingsLayerName, string> = {
  user: 'Usuário',
  project: 'Projeto (compartilhado no repositório)',
  local: 'Local (só você, nesta pasta)',
  cli: 'Linha de comando',
  managed: 'Política gerenciada',
}

export const LAYER_SHORT: Record<SettingsLayerName, string> = {
  user: 'usuário', project: 'projeto', local: 'local', cli: 'linha de comando', managed: 'gerenciado',
}

/** The conversation whose folder the editor works on (`?conversation=`), if any. */
export function useFolderConversation(): string | null {
  const [params] = useSearchParams()
  const value = params.get('conversation')
  return value && /^[A-Za-z0-9_.:-]{1,128}$/.test(value) ? value : null
}

export function useSettingsClient(): ApiClient {
  return useMemo(() => createBrowserApiClient(), [])
}

/** What went wrong, in the person's words; a 422's problems are listed as the server gave them. */
export function describeError(error: unknown, action: string): { message: string; problems: readonly string[]; localOnly: boolean } {
  if (error instanceof ApiError) {
    if (error.status === 404) return { message: 'Este editor existe só no Orin local (num Orin compartilhado as configurações são do servidor).', problems: [], localOnly: true }
    if (error.status === 403) return { message: `Você não tem permissão para ${action}.`, problems: [], localOnly: false }
    if (error.status === 422 || error.status === 400) return { message: `Não foi possível ${action}:`, problems: error.problems, localOnly: false }
    if (error.status === 409) return { message: `O arquivo mudou enquanto você editava; recarregue e tente de novo.`, problems: [], localOnly: false }
  }
  return { message: `Não foi possível ${action}.`, problems: [], localOnly: false }
}

export type Loaded<T> = { data: T | null; error: ReturnType<typeof describeError> | null; loading: boolean; reload: () => void }

/** Loads once and on `reload()`; a failure keeps no stale data (fail closed). */
export function useLoaded<T>(load: () => Promise<T>, action: string): Loaded<T> {
  const [generation, setGeneration] = useState(0)
  const [state, setState] = useState<{ data: T | null; error: ReturnType<typeof describeError> | null; loading: boolean }>({ data: null, error: null, loading: true })
  useEffect(() => {
    let cancelled = false
    load()
      .then((data) => { if (!cancelled) setState({ data, error: null, loading: false }) })
      .catch((error: unknown) => { if (!cancelled) setState({ data: null, error: describeError(error, action), loading: false }) })
    return () => { cancelled = true }
  }, [load, action, generation])
  const reload = useCallback(() => setGeneration((value) => value + 1), [])
  return { ...state, reload }
}

export function ErrorNote({ error }: { error: ReturnType<typeof describeError> | null }) {
  if (!error) return null
  return (
    <div className="code-settings__error" role="alert">
      <p>{error.message}</p>
      {error.problems.length > 0 && <ul>{error.problems.map((problem, index) => <li key={index}>{problem}</li>)}</ul>}
    </div>
  )
}

/** Which folder the editor is working on, and how to reach a folder's settings when there is none. */
export function FolderBanner({ projectRoot, trusted, conversationId }: { projectRoot: string | null; trusted?: boolean; conversationId: string | null }) {
  if (conversationId && !projectRoot) {
    return <p className="code-settings__folder">Configurações da pasta da conversa aberta (cada camada mostra o seu arquivo).</p>
  }
  if (!conversationId || !projectRoot) {
    return (
      <p className="code-settings__folder">
        Mostrando as suas configurações de usuário e a política gerenciada. Para editar as de uma pasta, use o comando
        correspondente (por exemplo <kbd>/permissions</kbd>) numa conversa daquela pasta.
      </p>
    )
  }
  return (
    <p className="code-settings__folder">
      Pasta: <code>{projectRoot}</code>
      {trusted === false && <span className="code-settings__warning"> · pasta não confiável: as configurações do projeto são ignoradas até você confiar nela.</span>}
    </p>
  )
}

export function Card({ title, id, children, actions }: { title: string; id: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="settings-card code-settings__card" aria-labelledby={id}>
      <div className="code-settings__card-head">
        <h2 id={id}>{title}</h2>
        {actions}
      </div>
      {children}
    </section>
  )
}

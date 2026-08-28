import { useEffect, useRef, useState, type FormEvent } from 'react'
import type { ApiClient } from '../../api/client'
import { inferMcpLaunch, type McpLaunchGuess } from '../../api/plugins'
import { approveMcpServer, createMcpServer, deleteMcpServer, type McpServerDetail, type McpTransport } from '../../api/mcp'
import { McpApprovalCard } from '../conversations/McpApprovalCard'

export function McpFromRepoDialog({ client, sourceUrl, onClose, onAdded }: { client: ApiClient; sourceUrl: string; onClose: () => void; onAdded: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [guess, setGuess] = useState<McpLaunchGuess | null>(null)
  const [displayName, setDisplayName] = useState('')
  const [transport, setTransport] = useState<McpTransport>('stdio')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState('')
  const [url, setUrl] = useState('')
  const [secretNames, setSecretNames] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [created, setCreated] = useState<McpServerDetail | null>(null)

  useEffect(() => {
    dialogRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  // `loading` already starts true, so the mount pass needs no synchronous
  // setState inside the effect. Only a change of source has to reset it, and
  // doing that during render avoids painting one frame of the previous
  // repository's guess.
  const [seenSource, setSeenSource] = useState(sourceUrl)
  if (sourceUrl !== seenSource) {
    setSeenSource(sourceUrl)
    setLoading(true)
  }

  useEffect(() => {
    let active = true
    inferMcpLaunch(client, sourceUrl)
      .then((result) => {
        if (!active) return
        setGuess(result)
        setDisplayName(result.display_name)
        setTransport(result.transport ?? 'stdio')
        setCommand(result.command ?? '')
        setArgs(result.args.join(' '))
        setUrl(result.url ?? '')
        setSecretNames(result.secret_names.join(', '))
      })
      .catch(() => { if (active) setLoadError('Não foi possível analisar este repositório automaticamente.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [client, sourceUrl])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setSubmitError(null)
    try {
      const detail = await createMcpServer(client, {
        display_name: displayName,
        transport,
        command: transport === 'stdio' ? command.trim() || undefined : undefined,
        args: transport === 'stdio' ? args.split(/\s+/).filter(Boolean) : undefined,
        url: transport === 'http' ? url.trim() || undefined : undefined,
        secret_names: secretNames.split(',').map((item) => item.trim()).filter(Boolean),
      })
      setCreated(detail)
    } catch {
      setSubmitError('Não foi possível adicionar o servidor.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="plugin-dialog__backdrop">
      <div className="plugin-dialog" role="dialog" aria-modal="true" aria-labelledby="mcp-from-repo-title" tabIndex={-1} ref={dialogRef}>
        <header className="plugin-dialog__head">
          <div><span className="plugin-dialog__eyebrow">SERVIDOR MCP</span><h2 id="mcp-from-repo-title">Adicionar como servidor MCP</h2></div>
          <button type="button" className="button--quiet" onClick={onClose}>Fechar</button>
        </header>
        <p className="plugin-dialog__lede">Este repositório não tem um manifesto de plugin — vamos tentar detectar como executá-lo como um servidor MCP comum.</p>

        {created ? (
          <McpApprovalCard
            server={{ server_id: created.server_id, display_name: created.display_name, transport: created.transport, secret_names: created.secret_names, catalog_id: created.catalog_id }}
            active
            onApprove={async (secrets) => { await approveMcpServer(client, created.server_id, secrets); onAdded() }}
            onDecline={async () => { await deleteMcpServer(client, created.server_id); onAdded() }}
          />
        ) : loading ? (
          <p>Analisando repositório…</p>
        ) : (
          <form className="mcp-server-form__manual" onSubmit={(event) => void submit(event)}>
            {loadError && <p role="alert">{loadError}</p>}
            {!loadError && guess?.confidence === 'none' && <p className="plugin-library__note">Não foi possível detectar automaticamente o comando de execução — preencha manualmente.</p>}
            <label>Nome<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label>
            <label>
              Transporte
              <select value={transport} onChange={(event) => setTransport(event.target.value as McpTransport)}>
                <option value="stdio">stdio</option>
                <option value="http">http</option>
              </select>
            </label>
            {transport === 'stdio' ? (
              <>
                <label>Comando<input value={command} onChange={(event) => setCommand(event.target.value)} placeholder="npx" /></label>
                <label>Argumentos<input value={args} onChange={(event) => setArgs(event.target.value)} placeholder="separados por espaço" /></label>
              </>
            ) : (
              <label>URL<input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://" /></label>
            )}
            <label>Credenciais necessárias<input value={secretNames} onChange={(event) => setSecretNames(event.target.value)} placeholder="separadas por vírgula" /></label>
            <button type="submit" disabled={submitting}>{submitting ? 'Adicionando…' : 'Adicionar'}</button>
            {submitError && <p role="alert">{submitError}</p>}
          </form>
        )}
      </div>
    </div>
  )
}

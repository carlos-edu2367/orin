import { useEffect, useState, type FormEvent } from 'react'
import type { ApiClient } from '../../api/client'
import { createMcpServer, listMcpCatalog, type McpCatalogEntry, type McpTransport } from '../../api/mcp'

type Mode = 'catalog' | 'manual'

type McpServerFormProps = {
  client: ApiClient
  onCreated: () => void
  onClose: () => void
}

/**
 * Proposing a server only ever needs names and shape, never a credential
 * value — the value is typed later, at approval, on the server's own card.
 */
export function McpServerForm({ client, onCreated, onClose }: McpServerFormProps) {
  const [mode, setMode] = useState<Mode>('catalog')
  const [query, setQuery] = useState('')
  const [entries, setEntries] = useState<McpCatalogEntry[]>([])
  const [selected, setSelected] = useState<McpCatalogEntry | null>(null)
  const [loadingCatalog, setLoadingCatalog] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [displayName, setDisplayName] = useState('')
  const [transport, setTransport] = useState<McpTransport>('stdio')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState('')
  const [url, setUrl] = useState('')
  const [secretNames, setSecretNames] = useState('')

  useEffect(() => {
    if (mode !== 'catalog') return
    const controller = new AbortController()
    listMcpCatalog(client, query, controller.signal)
      .then((value) => { setEntries(value); setError(null) })
      .catch(() => setError('Não foi possível carregar o catálogo.'))
      .finally(() => setLoadingCatalog(false))
    return () => controller.abort()
  }, [client, mode, query])

  async function proposeFromCatalog(entry: McpCatalogEntry) {
    setSubmitting(true)
    setError(null)
    try {
      await createMcpServer(client, { catalog_id: entry.catalog_id, display_name: entry.display_name })
      onCreated()
    } catch {
      setError(`Não foi possível adicionar ${entry.display_name}.`)
      setSubmitting(false)
    }
  }

  async function submitManual(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await createMcpServer(client, {
        display_name: displayName,
        transport,
        command: transport === 'stdio' ? command.trim() || undefined : undefined,
        args: transport === 'stdio' ? args.split(/\s+/).filter(Boolean) : undefined,
        url: transport === 'http' ? url.trim() || undefined : undefined,
        secret_names: secretNames.split(',').map((item) => item.trim()).filter(Boolean),
      })
      onCreated()
    } catch {
      setError('Não foi possível adicionar o servidor.')
      setSubmitting(false)
    }
  }

  return (
    <div className="mcp-server-form" role="dialog" aria-label="Adicionar servidor MCP">
      <header className="mcp-server-form__head">
        <h2>Adicionar servidor</h2>
        <button type="button" className="button--quiet" onClick={onClose}>Fechar</button>
      </header>

      <div className="mcp-server-form__tabs">
        <button type="button" aria-pressed={mode === 'catalog'} onClick={() => setMode('catalog')}>Catálogo</button>
        <button type="button" aria-pressed={mode === 'manual'} onClick={() => setMode('manual')}>Configurar manualmente</button>
      </div>

      {mode === 'catalog' ? (
        <div className="mcp-server-form__catalog-panel">
          <label className="mcp-server-form__search">
            <span className="visually-hidden">Buscar no catálogo</span>
            <input type="search" aria-label="Buscar no catálogo" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar…" />
          </label>
          {loadingCatalog ? <p>Carregando catálogo…</p> : (
            <ul className="mcp-server-form__catalog">
              {entries.map((entry) => (
                <li key={entry.catalog_id}>
                  <button type="button" onClick={() => setSelected(entry)} aria-pressed={selected?.catalog_id === entry.catalog_id}>
                    <strong>{entry.display_name}</strong>
                    <p>{entry.summary}</p>
                  </button>
                </li>
              ))}
              {entries.length === 0 && <li className="mcp-server-form__catalog-empty">Nenhum resultado para esta busca.</li>}
            </ul>
          )}
          {selected && (
            <div className="mcp-server-form__preview">
              <p><code>{selected.transport}</code> · {selected.setup_instructions}</p>
              {selected.secrets.length > 0 && <p>Vai precisar de: {selected.secrets.map((item) => item.name).join(', ')}</p>}
              <button type="button" disabled={submitting} onClick={() => void proposeFromCatalog(selected)}>
                {submitting ? 'Adicionando…' : `Adicionar ${selected.display_name}`}
              </button>
            </div>
          )}
        </div>
      ) : (
        <form className="mcp-server-form__manual" onSubmit={(event) => void submitManual(event)}>
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
        </form>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  )
}

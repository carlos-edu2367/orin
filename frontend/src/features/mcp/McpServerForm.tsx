import { useEffect, useState, type FormEvent } from 'react'
import type { ApiClient } from '../../api/client'
import { useSession } from '../../app/useSession'
import {
  approveMcpServer,
  createMcpServer,
  isMcpAuthorizationRequired,
  listMcpCatalog,
  type McpCatalogEntry,
  type McpTransport,
} from '../../api/mcp'
import {
  parseClaudeMcpAddCommand,
  validateMcpHeaderName,
  type McpHeaderInput,
} from './claudeCommandParser'

type Mode = 'catalog' | 'manual'

type McpServerFormProps = {
  client: ApiClient
  /** keepOpen: the server was created but connecting failed; the form stays open to show why. */
  onCreated: (options?: { keepOpen?: boolean }) => void
  onClose: () => void
}

/**
 * Proposing a server only ever needs names and shape, never a credential
 * value — the value is typed later, at approval, on the server's own card
 * (or sent immediately via approveMcpServer when HTTP header values are supplied).
 */
export function McpServerForm({ client, onCreated, onClose }: McpServerFormProps) {
  const { capabilities } = useSession()
  const allowStdio = capabilities.mcp_stdio
  const [mode, setMode] = useState<Mode>('catalog')
  const [query, setQuery] = useState('')
  const [entries, setEntries] = useState<McpCatalogEntry[]>([])
  const [selected, setSelected] = useState<McpCatalogEntry | null>(null)
  const [loadingCatalog, setLoadingCatalog] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [commandInput, setCommandInput] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [transport, setTransport] = useState<McpTransport>(allowStdio ? 'stdio' : 'http')
  const [command, setCommand] = useState('')
  const [args, setArgs] = useState('')
  const [url, setUrl] = useState('')
  const [secretNames, setSecretNames] = useState('')
  const [headers, setHeaders] = useState<McpHeaderInput[]>([])

  useEffect(() => {
    if (mode !== 'catalog') return
    const controller = new AbortController()
    listMcpCatalog(client, query, controller.signal)
      // A server instance cannot spawn stdio servers, so it never offers them.
      .then((value) => { setEntries(allowStdio ? value : value.filter((entry) => entry.transport !== 'stdio')); setError(null) })
      .catch(() => setError('Não foi possível carregar o catálogo.'))
      .finally(() => setLoadingCatalog(false))
    return () => controller.abort()
  }, [client, mode, query, allowStdio])

  function handleImportCommand() {
    const parsed = parseClaudeMcpAddCommand(commandInput)
    if (!parsed.ok) {
      setError(parsed.error)
      return
    }
    setError(null)
    setDisplayName(parsed.displayName)
    setTransport(parsed.transport)
    if (parsed.transport === 'http') {
      setUrl(parsed.url ?? '')
      setHeaders(parsed.headers.map((item) => ({ name: item.name, value: item.value })))
    } else {
      setCommand(parsed.command ?? '')
      setArgs((parsed.args ?? []).join(' '))
      setHeaders([])
    }
    setSecretNames(parsed.secretNames.join(', '))
  }

  function addHeaderRow() {
    setHeaders((current) => [...current, { name: '', value: '' }])
  }

  function updateHeaderRow(index: number, patch: Partial<McpHeaderInput>) {
    setHeaders((current) =>
      current.map((item, idx) => (idx === index ? { ...item, ...patch } : item)),
    )
  }

  function removeHeaderRow(index: number) {
    setHeaders((current) => current.filter((_, idx) => idx !== index))
  }

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

    const activeHeaders =
      transport === 'http'
        ? headers.filter((row) => row.name.trim() !== '' || row.value.trim() !== '')
        : []

    for (const row of activeHeaders) {
      const trimmedName = row.name.trim()
      if (!trimmedName) {
        setError('Informe o nome do cabeçalho HTTP.')
        return
      }
      const validationError = validateMcpHeaderName(trimmedName)
      if (validationError) {
        setError(validationError)
        return
      }
      if (/[\r\n]/.test(row.value)) {
        setError(`O valor do cabeçalho "${trimmedName}" não pode conter quebras de linha.`)
        return
      }
    }

    const combinedSecretNames = secretNames
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)

    const headerSecrets: Record<string, string> = {}
    for (const row of activeHeaders) {
      const headerSecretName = `header:${row.name.trim()}`
      if (!combinedSecretNames.includes(headerSecretName)) {
        combinedSecretNames.push(headerSecretName)
      }
      const trimmedValue = row.value.trim()
      if (trimmedValue) {
        headerSecrets[headerSecretName] = trimmedValue
      }
    }

    setSubmitting(true)
    setError(null)
    let createdServerId: string
    try {
      const created = await createMcpServer(client, {
        display_name: displayName,
        transport,
        command: transport === 'stdio' ? command.trim() || undefined : undefined,
        args: transport === 'stdio' ? args.split(/\s+/).filter(Boolean) : undefined,
        url: transport === 'http' ? url.trim() || undefined : undefined,
        secret_names: combinedSecretNames,
      })
      createdServerId = created.server_id
    } catch {
      setError('Não foi possível adicionar o servidor.')
      setSubmitting(false)
      return
    }

    setHeaders([])
    setCommandInput('')

    if (Object.keys(headerSecrets).length > 0) {
      try {
        await approveMcpServer(client, createdServerId, headerSecrets)
      } catch (approveFailure) {
        if (isMcpAuthorizationRequired(approveFailure)) {
          setError('Servidor adicionado, mas o servidor pediu login (OAuth). Autorize o acesso no card do servidor.')
        } else {
          setError('Servidor adicionado, mas o servidor recusou a credencial. Confira os valores no card do servidor.')
        }
        setSubmitting(false)
        onCreated({ keepOpen: true })
        return
      }
    }

    setSubmitting(false)
    onCreated()
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
          <div className="mcp-server-form__import">
            <label>
              Colar comando `claude mcp add`
              <textarea
                rows={2}
                value={commandInput}
                onChange={(event) => setCommandInput(event.target.value)}
                placeholder='claude mcp add --transport http clickup https://... --header "Authorization: Bearer ..."'
              />
            </label>
            <button type="button" onClick={handleImportCommand}>Importar</button>
          </div>
          <label>Nome<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} required /></label>
          <label>
            Transporte
            <select value={transport} onChange={(event) => setTransport(event.target.value as McpTransport)}>
              {allowStdio && <option value="stdio">stdio</option>}
              <option value="http">http</option>
            </select>
          </label>
          {transport === 'stdio' ? (
            <>
              <label>Comando<input value={command} onChange={(event) => setCommand(event.target.value)} placeholder="npx" /></label>
              <label>Argumentos<input value={args} onChange={(event) => setArgs(event.target.value)} placeholder="separados por espaço" /></label>
            </>
          ) : (
            <>
              <label>URL<input value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://" /></label>
              <div className="mcp-server-form__headers" role="group" aria-label="Cabeçalhos HTTP">
                <div className="mcp-server-form__headers-head">
                  <span>Cabeçalhos HTTP</span>
                  <button type="button" onClick={addHeaderRow}>Adicionar cabeçalho</button>
                </div>
                {headers.map((header, index) => (
                  <div key={index} className="mcp-server-form__header-row">
                    <label>
                      Nome do cabeçalho
                      <input
                        value={header.name}
                        onChange={(event) => updateHeaderRow(index, { name: event.target.value })}
                        placeholder="Authorization"
                      />
                    </label>
                    <label>
                      Valor do cabeçalho
                      <input
                        type="password"
                        autoComplete="off"
                        value={header.value}
                        onChange={(event) => updateHeaderRow(index, { value: event.target.value })}
                        placeholder="Bearer ..."
                      />
                    </label>
                    <button type="button" onClick={() => removeHeaderRow(index)}>Remover</button>
                  </div>
                ))}
              </div>
            </>
          )}
          <label>Credenciais necessárias<input value={secretNames} onChange={(event) => setSecretNames(event.target.value)} placeholder="separadas por vírgula" /></label>
          <button type="submit" disabled={submitting}>{submitting ? 'Adicionando…' : 'Adicionar'}</button>
        </form>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  )
}

import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import { listMcpServers, type McpServerSummary } from '../../api/mcp'
import { SettingsSection } from '../settings/SettingsSection'
import { McpServerCard } from './McpServerCard'
import { McpServerForm } from './McpServerForm'

/**
 * MCP server management. Renders inside the existing Settings shell — once
 * the settings shell refactor lands, this becomes the content of the /settings/mcp
 * section rather than a page of its own.
 */
export function McpSection({ client }: { client?: ApiClient }) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const [servers, setServers] = useState<McpServerSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)

  const refresh = useCallback(() => (
    listMcpServers(apiClient)
      .then((value) => { setServers(value); setError(null) })
      .catch(() => setError('Não foi possível carregar os servidores MCP.'))
      .finally(() => setLoading(false))
  ), [apiClient])

  useEffect(() => { void refresh() }, [refresh])

  return (
    <SettingsSection eyebrow="EXTENSÕES / MCP" title="MCP" lede="Servidores conectados e as tools que cada um publica. O agente também pode propor uma conexão durante a conversa; ela aparece aqui aguardando aprovação.">
      <p className="mcp-section__description">
        Servidores conectados e as tools que cada um publica. O agente também pode propor uma conexão durante a
        conversa; ela aparece aqui aguardando aprovação.
      </p>

      <div className="mcp-section__actions">
        <button type="button" className="button button--primary" onClick={() => setFormOpen(true)}>Adicionar servidor</button>
      </div>

      {loading && servers.length === 0 && <p>Carregando…</p>}
      {error && <p role="alert">{error}</p>}

      <div className="mcp-section__list">
        {servers.map((server) => (
          <McpServerCard key={server.server_id} server={server} client={apiClient} onChanged={() => void refresh()} />
        ))}
        {!loading && !error && servers.length === 0 && <p className="mcp-section__empty">Nenhum servidor MCP configurado ainda.</p>}
      </div>

      {formOpen && (
        <McpServerForm
          client={apiClient}
          onClose={() => setFormOpen(false)}
          onCreated={() => { setFormOpen(false); void refresh() }}
        />
      )}
    </SettingsSection>
  )
}

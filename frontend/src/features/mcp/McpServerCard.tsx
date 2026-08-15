import { useState } from 'react'
import type { ApiClient } from '../../api/client'
import {
  approveMcpServer, deleteMcpServer, getMcpServer, setMcpServerEnabled, setMcpToolEnabled, testMcpServer,
  type McpServerSummary, type McpToolSummary,
} from '../../api/mcp'
import { McpApprovalCard } from '../conversations/McpApprovalCard'

const STATE_LABEL: Record<string, string> = {
  pending_approval: 'Aguardando aprovação', active: 'Ativo', disabled: 'Desativado', error: 'Erro',
}

type McpServerCardProps = {
  server: McpServerSummary
  client: ApiClient
  onChanged: () => void
}

/**
 * A pending server reuses the same approval card the chat shows: the two
 * surfaces should feel like the same decision, not two different flows.
 */
export function McpServerCard({ server, client, onChanged }: McpServerCardProps) {
  const [tools, setTools] = useState<McpToolSummary[] | null>(null)
  const [toolsOpen, setToolsOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmingRemove, setConfirmingRemove] = useState(false)
  const [testResult, setTestResult] = useState<string | null>(null)

  async function toggleToolsOpen() {
    if (toolsOpen) {
      setToolsOpen(false)
      return
    }
    setBusy(true)
    setError(null)
    try {
      const detail = await getMcpServer(client, server.server_id)
      setTools(detail.tools)
      setToolsOpen(true)
    } catch {
      setError('Não foi possível carregar as tools deste servidor.')
    } finally {
      setBusy(false)
    }
  }

  async function toggleTool(tool: McpToolSummary) {
    setBusy(true)
    setError(null)
    try {
      const detail = await setMcpToolEnabled(client, server.server_id, tool.name, !tool.enabled)
      setTools(detail.tools)
    } catch {
      setError(`Não foi possível atualizar a tool ${tool.name}.`)
    } finally {
      setBusy(false)
    }
  }

  async function toggleServer() {
    setBusy(true)
    setError(null)
    try {
      await setMcpServerEnabled(client, server.server_id, server.state !== 'active')
      onChanged()
    } catch {
      setError('Não foi possível atualizar o estado do servidor.')
    } finally {
      setBusy(false)
    }
  }

  async function runTest() {
    setBusy(true)
    setError(null)
    setTestResult(null)
    try {
      const result = await testMcpServer(client, server.server_id)
      setTestResult(result.connected ? `Conectado · ${result.tools.length} tool${result.tools.length === 1 ? '' : 's'}` : `Falhou: ${result.error ?? 'sem detalhes'}`)
    } catch {
      setError('Não foi possível testar a conexão.')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    setError(null)
    try {
      await deleteMcpServer(client, server.server_id)
      onChanged()
    } catch {
      setError('Não foi possível remover o servidor.')
      setBusy(false)
    }
  }

  return (
    <article className="mcp-server-card" aria-label={server.display_name}>
      <header className="mcp-server-card__head">
        <div>
          <strong>{server.display_name}</strong>
          <span className="mcp-server-card__meta"><code>{server.transport}</code> · {server.tool_count} tool{server.tool_count === 1 ? '' : 's'}</span>
        </div>
        <span className={`mcp-server-card__state is-${server.state}`}>{STATE_LABEL[server.state] ?? server.state}</span>
      </header>

      {server.state === 'pending_approval' ? (
        <McpApprovalCard
          server={{ server_id: server.server_id, display_name: server.display_name, transport: server.transport, secret_names: server.secret_names, catalog_id: server.catalog_id }}
          active
          onApprove={async (secrets) => { await approveMcpServer(client, server.server_id, secrets); onChanged() }}
          onDecline={async () => { await deleteMcpServer(client, server.server_id); onChanged() }}
        />
      ) : (
        <div className="mcp-server-card__body">
          <div className="mcp-server-card__actions">
            <button type="button" onClick={() => void toggleServer()} disabled={busy}>{server.state === 'active' ? 'Desativar' : 'Ativar'}</button>
            <button type="button" onClick={() => void runTest()} disabled={busy}>Testar conexão</button>
            <button type="button" onClick={() => void toggleToolsOpen()} disabled={busy}>{toolsOpen ? 'Ocultar tools' : 'Ver tools'}</button>
            {!confirmingRemove
              ? <button type="button" className="mcp-server-card__remove" onClick={() => setConfirmingRemove(true)} disabled={busy}>Remover</button>
              : <button type="button" className="mcp-server-card__remove" onClick={() => void remove()} disabled={busy}>Confirmar remoção</button>}
          </div>
          {testResult && <p className="mcp-server-card__test-result">{testResult}</p>}
          {toolsOpen && tools && (
            <ul className="mcp-server-card__tools">
              {tools.map((tool) => (
                <li key={tool.name}>
                  <label>
                    <input type="checkbox" checked={tool.enabled} onChange={() => void toggleTool(tool)} disabled={busy} />
                    <span>{tool.name}</span>
                  </label>
                  {tool.description && <p>{tool.description}</p>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {error && <p role="alert" className="mcp-server-card__error">{error}</p>}
    </article>
  )
}

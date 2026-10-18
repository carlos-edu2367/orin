import { useCallback, useEffect, useState } from 'react'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import {
  approveMcpServer,
  createMcpServer,
  deleteMcpServer,
  listMcpServers,
  setMcpServerEnabled,
  testMcpServer,
  type McpServerSummary,
  type McpTestResult,
} from '../../api/mcp'
import { SettingsSection } from './SettingsSection'

const PLAYWRIGHT_COMMAND = 'npx -y @playwright/mcp@latest'

function findPlaywrightServer(servers: McpServerSummary[]): McpServerSummary | null {
  return (
    servers.find((item) => item.catalog_id === 'playwright' || item.slug === 'playwright') ?? null
  )
}

function stateBadgeLabel(state: McpServerSummary['state']): string {
  switch (state) {
    case 'active':
      return 'Ativo'
    case 'disabled':
      return 'Desativado'
    case 'pending_approval':
      return 'Pendente de aprovação'
    case 'error':
      return 'Erro'
  }
}

/**
 * Browser automation is provided exclusively through the official Playwright MCP
 * server (`npx -y @playwright/mcp@latest`), avoiding duplicate internal browser
 * engines while giving agents full Playwright MCP capabilities in 1 click.
 */
export function BrowserSection({ client: providedClient }: { client?: ApiClient }) {
  const [client] = useState(() => providedClient ?? createBrowserApiClient())
  const [server, setServer] = useState<McpServerSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [busyAction, setBusyAction] = useState<'install' | 'test' | 'toggle' | 'remove' | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<McpTestResult | null>(null)

  const refresh = useCallback(async () => {
    setErrorMessage(null)
    try {
      const list = await listMcpServers(client)
      setServer(findPlaywrightServer(list))
    } catch {
      setErrorMessage('Não foi possível consultar os servidores MCP.')
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => {
    // Inline fetch: setState only happens after the awaited request, and a stale response is dropped.
    let cancelled = false
    listMcpServers(client)
      .then((list) => {
        if (cancelled) return
        setErrorMessage(null)
        setServer(findPlaywrightServer(list))
      })
      .catch(() => {
        if (!cancelled) setErrorMessage('Não foi possível consultar os servidores MCP.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [client])

  async function handleInstallAndActivate() {
    if (busyAction) return
    setBusyAction('install')
    setErrorMessage(null)
    setTestResult(null)
    try {
      let current = server
      if (!current) {
        current = await createMcpServer(client, {
          display_name: 'Playwright',
          catalog_id: 'playwright',
          slug: 'playwright',
          transport: 'stdio',
          command: 'npx',
          args: ['-y', '@playwright/mcp@latest'],
          secret_names: [],
        })
        setServer(current)
      }
      const approved = await approveMcpServer(client, current.server_id, {})
      setServer(approved)
      try {
        const tested = await testMcpServer(client, approved.server_id)
        setTestResult(tested)
      } catch {
        // Approval already succeeded; connection test is advisory on first install.
      }
    } catch {
      setErrorMessage(
        'Não foi possível instalar ou ativar o Playwright MCP. Verifique se o Node.js (npx) está disponível no PATH.',
      )
    } finally {
      setBusyAction(null)
    }
  }

  async function handleTestConnection() {
    if (!server || busyAction) return
    setBusyAction('test')
    setErrorMessage(null)
    try {
      const result = await testMcpServer(client, server.server_id)
      setTestResult(result)
      if (!result.connected) {
        setErrorMessage(result.error ?? 'Não foi possível conectar ao servidor Playwright MCP.')
      }
      await refresh()
    } catch {
      setErrorMessage('Falha ao testar a conexão com o Playwright MCP.')
    } finally {
      setBusyAction(null)
    }
  }

  async function handleToggleEnabled(nextEnabled: boolean) {
    if (!server || busyAction) return
    setBusyAction('toggle')
    setErrorMessage(null)
    if (!nextEnabled) setTestResult(null)
    try {
      const updated = await setMcpServerEnabled(client, server.server_id, nextEnabled)
      setServer(updated)
    } catch {
      setErrorMessage('Não foi possível alterar o estado do servidor Playwright MCP.')
    } finally {
      setBusyAction(null)
    }
  }

  async function handleRemove() {
    if (!server || busyAction) return
    setBusyAction('remove')
    setErrorMessage(null)
    setTestResult(null)
    try {
      await deleteMcpServer(client, server.server_id)
      setServer(null)
    } catch {
      setErrorMessage('Não foi possível remover o servidor Playwright MCP.')
    } finally {
      setBusyAction(null)
    }
  }

  return (
    <SettingsSection eyebrow="SISTEMA / NAVEGADOR" title="Navegador">
      <section className="installation-status browser-engine" aria-labelledby="browser-engine-title">
        <div className="installation-status__heading">
          <div>
            <p className="eyebrow">NAVEGADOR VIA MCP</p>
            <h2 id="browser-engine-title">Playwright MCP</h2>
          </div>
          {server && (
            <span
              className={`settings-status-pill settings-status-pill--${server.state}`}
              data-testid="playwright-mcp-status"
            >
              {stateBadgeLabel(server.state)}
            </span>
          )}
        </div>

        <p className="browser-engine__lede">
          Permite que o agente abra páginas reais, clique, preencha formulários, inspecione o DOM e capture screenshots pelo servidor oficial do Playwright MCP.
        </p>

        <p className="browser-engine__hint">
          Comando oficial: <code>{PLAYWRIGHT_COMMAND}</code>
        </p>

        {errorMessage && <p role="alert">{errorMessage}</p>}

        {!loading && !server && (
          <div className="installation-status__update">
            <button
              type="button"
              className="button button--primary"
              disabled={busyAction !== null}
              onClick={() => void handleInstallAndActivate()}
            >
              {busyAction === 'install' ? 'Instalando e ativando…' : 'Instalar e ativar Playwright MCP'}
            </button>
          </div>
        )}

        {server && server.state === 'active' && (
          <p role="status" className="browser-engine__ok">
            O agente já pode usar o Playwright MCP nas conversas e no Modo Code
            {server.tool_count > 0 ? ` (${server.tool_count} ferramentas disponíveis)` : ''}.
          </p>
        )}

        {server && server.state === 'disabled' && (
          <p role="status">O servidor Playwright MCP está instalado, mas desativado no momento.</p>
        )}

        {server && server.state === 'pending_approval' && (
          <p role="status">O servidor Playwright MCP foi registrado e aguarda ativação.</p>
        )}

        {testResult?.connected && (
          <p role="status" className="browser-engine__ok">
            Conexão verificada ({testResult.tools.length} ferramentas prontas).
          </p>
        )}

        {server && (
          <div className="installation-status__update" style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
            {server.state === 'pending_approval' ? (
              <button
                type="button"
                className="button button--primary"
                disabled={busyAction !== null}
                onClick={() => void handleInstallAndActivate()}
              >
                {busyAction === 'install' ? 'Ativando…' : 'Ativar Playwright MCP'}
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className="button button--ghost"
                  disabled={busyAction !== null}
                  onClick={() => void handleTestConnection()}
                >
                  {busyAction === 'test' ? 'Testando…' : 'Testar conexão'}
                </button>
                {server.state === 'active' ? (
                  <button
                    type="button"
                    className="button button--ghost"
                    disabled={busyAction !== null}
                    onClick={() => void handleToggleEnabled(false)}
                  >
                    Desativar
                  </button>
                ) : (
                  <button
                    type="button"
                    className="button button--primary"
                    disabled={busyAction !== null}
                    onClick={() => void handleToggleEnabled(true)}
                  >
                    Ativar
                  </button>
                )}
              </>
            )}
            <button
              type="button"
              className="button button--ghost"
              disabled={busyAction !== null}
              onClick={() => void handleRemove()}
            >
              Remover
            </button>
          </div>
        )}
      </section>
    </SettingsSection>
  )
}

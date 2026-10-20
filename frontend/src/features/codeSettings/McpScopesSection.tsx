import { useCallback, useState } from 'react'
import { decideMcpServer, getMcpConfig, type McpConfigServer } from '../../api/settings'
import { SettingsSection } from '../settings/SettingsSection'
import { Card, describeError, ErrorNote, FolderBanner, useFolderConversation, useLoaded, useSettingsClient } from './shared'

const SCOPE: Record<string, string> = {
  managed: 'Política gerenciada', cli: 'Linha de comando', local: 'Local (só você, nesta pasta)', project: 'Projeto (.mcp.json)', user: 'Usuário',
}
const ORDER = ['managed', 'cli', 'local', 'project', 'user']
const STATE: Record<McpConfigServer['state'], string> = { on: 'ligado', pending: 'aguardando aprovação', off: 'desligado' }

/**
 * MCP servers from configuration files, per scope (W7): Claude Code's and Orin's files for the person, the folder and
 * the managed policy. A project's servers (its `.mcp.json`) run only after the person approves them here; the choice is
 * saved outside the repository, so a repository can never approve its own servers. Servers added through Orin's
 * catalog are on the MCP page.
 */
export function McpScopesSection() {
  const client = useSettingsClient()
  const conversationId = useFolderConversation()
  const load = useCallback(() => getMcpConfig(client, conversationId), [client, conversationId])
  const report = useLoaded(load, 'ler os servidores MCP')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ReturnType<typeof describeError> | null>(null)

  async function decide(server: string, decision: 'approve' | 'deny' | 'reset') {
    if (!conversationId) return
    setBusy(true)
    setError(null)
    try {
      await decideMcpServer(client, conversationId, server, decision)
      report.reload()
    } catch (caught) {
      setError(describeError(caught, decision === 'approve' ? 'aprovar o servidor' : decision === 'deny' ? 'negar o servidor' : 'esquecer a escolha'))
    } finally {
      setBusy(false)
    }
  }

  const data = report.data
  const chosen = new Set([...(data?.choices.approved ?? []), ...(data?.choices.denied ?? [])])
  const byScope = ORDER.map((scope) => ({ scope, servers: (data?.servers ?? []).filter((server) => server.scope === scope) }))
    .filter((group) => group.servers.length > 0)
  return (
    <SettingsSection eyebrow="ORIN CODE / MCP POR ESCOPO">
      {!report.error?.localOnly && <FolderBanner projectRoot={data?.folder ?? null} trusted={data?.trusted} conversationId={conversationId} />}
      <ErrorNote error={report.error} />
      {report.loading && !data && <p className="code-settings__muted">Carregando…</p>}
      {data?.managedOnly && <p className="code-settings__warning">A política gerenciada define os servidores MCP: os dos seus arquivos e os da pasta ficam desligados.</p>}
      {data?.awaitingTrust && <p className="code-settings__warning">Esta pasta tem servidores MCP no projeto, mas ainda não é confiável: eles ficam desligados até você confiar nela.</p>}
      {data && data.errors.length > 0 && (
        <div className="code-settings__error" role="alert">
          <p>Problemas nos arquivos de MCP:</p>
          <ul>{data.errors.map((item, index) => <li key={index}>{item.where}: {item.message}</li>)}</ul>
        </div>
      )}
      {data && data.pending.length > 0 && (
        <Card title="Servidores do projeto aguardando a sua aprovação" id="mcp-pending">
          <p className="code-settings__muted">Um servidor MCP roda um programa no seu computador. Aprove só o que você conhece; a escolha vale para esta pasta e fica fora do repositório.</p>
          <ul className="code-settings__rules">
            {data.pending.map((server) => (
              <li key={server.name} className="code-settings__rule code-settings__mcp-pending">
                <div className="code-settings__rule-text">
                  <strong>{server.name}</strong> <span className="code-settings__muted">{server.transport}</span>
                  <div><code>{server.shown || '—'}</code></div>
                  <span className="code-settings__file">{server.source}</span>
                </div>
                <button type="button" className="button button--primary" disabled={busy || !conversationId} onClick={() => void decide(server.name, 'approve')}>Aprovar</button>
                <button type="button" className="ghost-button" disabled={busy || !conversationId} onClick={() => void decide(server.name, 'deny')}>Negar</button>
              </li>
            ))}
          </ul>
          <ErrorNote error={error} />
        </Card>
      )}
      {data && (
        <Card title="Servidores por escopo" id="mcp-scopes">
          {byScope.length === 0 ? <p className="code-settings__empty">Nenhum servidor MCP em arquivos de configuração.</p> : byScope.map((group) => (
            <div key={group.scope} className="code-settings__scope-group">
              <h3>{SCOPE[group.scope] ?? group.scope}</h3>
              <ul className="code-settings__rules">
                {group.servers.map((server) => (
                  <li key={`${group.scope}:${server.name}`} className="code-settings__rule">
                    <strong className="code-settings__rule-text">{server.name} <code>{server.shown || '—'}</code></strong>
                    <span className={`code-settings__layer code-settings__state--${server.state}`}>{STATE[server.state]}</span>
                    {server.scope === 'project' && chosen.has(server.name) && conversationId && (
                      <button type="button" className="ghost-button" disabled={busy} onClick={() => void decide(server.name, 'reset')}
                        aria-label={`Esquecer a escolha sobre ${server.name}`}>Esquecer a escolha</button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {data.pending.length === 0 && <ErrorNote error={error} />}
        </Card>
      )}
    </SettingsSection>
  )
}

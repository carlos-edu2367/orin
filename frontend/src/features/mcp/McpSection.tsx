import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import { listMcpServers, type McpServerSummary } from '../../api/mcp'
import { McpServerCard } from './McpServerCard'
import { McpServerForm } from './McpServerForm'

type ConnectionTab = 'apps' | 'permissions' | 'mcp'
type UseInScope = 'Projeto Auryly' | 'Todos os projetos'

type ConnectionPermissionItem = {
  id: string
  label: string
  description: string
  defaultChecked: boolean
}

type BuiltinConnectionItem = {
  id: string
  name: string
  subtitle: string
  icon: string
  iconMod: 'is-auryly' | 'is-browser' | 'is-files'
  statusLabel: 'Conectado' | 'Ativo' | 'Restrito'
  statusMod: 'is-connected' | 'is-active' | 'is-restricted'
  defaultUseIn: UseInScope
  permissions: ReadonlyArray<ConnectionPermissionItem>
}

const BUILTIN_CONNECTIONS: ReadonlyArray<BuiltinConnectionItem> = [
  {
    id: 'conn-auryly',
    name: 'Auryly',
    subtitle: 'Acesso a produtos, pedidos e campanhas',
    icon: 'A',
    iconMod: 'is-auryly',
    statusLabel: 'Conectado',
    statusMod: 'is-connected',
    defaultUseIn: 'Projeto Auryly',
    permissions: [
      {
        id: 'auryly-read',
        label: 'Ver produtos, estoque e pedidos',
        description: 'Somente leitura',
        defaultChecked: true,
      },
      {
        id: 'auryly-draft',
        label: 'Criar rascunhos de campanhas e cupons',
        description: 'Não publica sem sua aprovação',
        defaultChecked: true,
      },
      {
        id: 'auryly-publish',
        label: 'Publicar alterações na loja',
        description: 'Pede confirmação antes de cada publicação',
        defaultChecked: false,
      },
      {
        id: 'auryly-payment',
        label: 'Alterar configurações de pagamento',
        description: 'Bloqueado por segurança',
        defaultChecked: false,
      },
    ],
  },
  {
    id: 'conn-browser',
    name: 'Navegador',
    subtitle: 'Pesquisa sites e captura páginas com sua permissão',
    icon: '🌐',
    iconMod: 'is-browser',
    statusLabel: 'Ativo',
    statusMod: 'is-active',
    defaultUseIn: 'Todos os projetos',
    permissions: [
      {
        id: 'browser-read',
        label: 'Ver produtos, estoque e pedidos',
        description: 'Somente leitura',
        defaultChecked: true,
      },
      {
        id: 'browser-draft',
        label: 'Criar rascunhos de campanhas e cupons',
        description: 'Não publica sem sua aprovação',
        defaultChecked: true,
      },
      {
        id: 'browser-publish',
        label: 'Publicar alterações na loja',
        description: 'Pede confirmação antes de cada publicação',
        defaultChecked: false,
      },
      {
        id: 'browser-payment',
        label: 'Alterar configurações de pagamento',
        description: 'Bloqueado por segurança',
        defaultChecked: false,
      },
    ],
  },
  {
    id: 'conn-files',
    name: 'Arquivos locais',
    subtitle: 'Apenas a pasta /Documentos/Projetos',
    icon: '📁',
    iconMod: 'is-files',
    statusLabel: 'Restrito',
    statusMod: 'is-restricted',
    defaultUseIn: 'Projeto Auryly',
    permissions: [
      {
        id: 'files-read',
        label: 'Ver produtos, estoque e pedidos',
        description: 'Somente leitura',
        defaultChecked: true,
      },
      {
        id: 'files-draft',
        label: 'Criar rascunhos de campanhas e cupons',
        description: 'Não publica sem sua aprovação',
        defaultChecked: true,
      },
      {
        id: 'files-publish',
        label: 'Publicar alterações na loja',
        description: 'Pede confirmação antes de cada publicação',
        defaultChecked: false,
      },
      {
        id: 'files-payment',
        label: 'Alterar configurações de pagamento',
        description: 'Bloqueado por segurança',
        defaultChecked: false,
      },
    ],
  },
]

function buildInitialPermissions(): Record<string, boolean> {
  const initial: Record<string, boolean> = {}
  for (const conn of BUILTIN_CONNECTIONS) {
    for (const perm of conn.permissions) {
      initial[`${conn.id}:${perm.id}`] = perm.defaultChecked
    }
  }
  return initial
}

/**
 * Connections & MCP server management (Phase 6 — ref-13.png).
 */
export function McpSection({ client }: { client?: ApiClient }) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const [servers, setServers] = useState<McpServerSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [formOpen, setFormOpen] = useState(false)
  const [activeTab, setActiveTab] = useState<ConnectionTab>('apps')
  const [selectedConnectionId, setSelectedConnectionId] = useState<string>(BUILTIN_CONNECTIONS[0].id)
  const [useInByConnection, setUseInByConnection] = useState<Record<string, UseInScope>>({})
  const [permissionState, setPermissionState] = useState<Record<string, boolean>>(() =>
    buildInitialPermissions(),
  )
  const [disconnectedIds, setDisconnectedIds] = useState<Record<string, boolean>>({})

  const refresh = useCallback(
    () =>
      listMcpServers(apiClient)
        .then((value) => {
          setServers(value)
          setError(null)
        })
        .catch(() => setError('Não foi possível carregar os servidores MCP.'))
        .finally(() => setLoading(false)),
    [apiClient],
  )

  useEffect(() => {
    void refresh()
  }, [refresh])

  const selectedConnection: BuiltinConnectionItem = useMemo(
    () =>
      BUILTIN_CONNECTIONS.find((conn) => conn.id === selectedConnectionId) ??
      BUILTIN_CONNECTIONS[0],
    [selectedConnectionId],
  )

  const currentUseIn: UseInScope =
    useInByConnection[selectedConnection.id] ?? selectedConnection.defaultUseIn
  const isSelectedDisconnected = Boolean(disconnectedIds[selectedConnection.id])

  function togglePermission(connId: string, permId: string) {
    const key = `${connId}:${permId}`
    setPermissionState((prev) => ({
      ...prev,
      [key]: !prev[key],
    }))
  }

  function handleToggleDisconnect(connId: string) {
    setDisconnectedIds((prev) => ({
      ...prev,
      [connId]: !prev[connId],
    }))
  }

  return (
    <section
      className="settings-section connections-ref13"
      aria-labelledby="connections-title"
      data-testid="connections-section"
    >
      <header className="connections-ref13__header">
        <div className="connections-ref13__header-copy">
          <p className="eyebrow visually-hidden">EXTENSÕES / MCP</p>
          <h1
            id="connections-title"
            className="connections-ref13__title"
            data-testid="connections-heading"
          >
            Conexões
          </h1>
          <p className="connections-ref13__subtitle">
            Serviços e pastas que o Orin pode acessar quando você pedir.
          </p>
        </div>

        <div className="connections-ref13__header-actions mcp-section__actions">
          <button
            type="button"
            className="button button--primary connections-ref13__add-btn"
            data-testid="connections-add-button"
            onClick={() => setFormOpen(true)}
          >
            + Adicionar conexão
          </button>
          <button
            type="button"
            className="visually-hidden connections-ref13__add-server-btn"
            onClick={() => setFormOpen(true)}
          >
            Adicionar servidor
          </button>
        </div>
      </header>

      <div className="connections-ref13__tabs" role="tablist" aria-label="Modo de conexões">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'apps'}
          className={`connections-ref13__tab${activeTab === 'apps' ? ' is-active' : ''}`}
          data-testid="connections-tab-apps"
          onClick={() => setActiveTab('apps')}
        >
          Apps conectados
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'permissions'}
          className={`connections-ref13__tab${activeTab === 'permissions' ? ' is-active' : ''}`}
          data-testid="connections-tab-permissions"
          onClick={() => setActiveTab('permissions')}
        >
          Permissões
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'mcp'}
          className={`connections-ref13__tab${activeTab === 'mcp' ? ' is-active' : ''}`}
          data-testid="connections-tab-mcp"
          onClick={() => setActiveTab('mcp')}
        >
          Avançado (MCP)
        </button>
      </div>

      {loading && servers.length === 0 && <p className="connections-ref13__loading">Carregando…</p>}
      {error && <p role="alert">{error}</p>}

      <div className="connections-ref13__master-detail">
        <div className="connections-list mcp-section__list" data-testid="connections-list">
          {BUILTIN_CONNECTIONS.map((conn) => {
            const isSelected = conn.id === selectedConnection.id
            const isDisconnected = Boolean(disconnectedIds[conn.id])
            return (
              <div
                key={conn.id}
                className={`connection-row-card${isSelected ? ' is-selected' : ''}`}
                data-testid={`connection-card-${conn.id}`}
                tabIndex={0}
                onClick={() => setSelectedConnectionId(conn.id)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    setSelectedConnectionId(conn.id)
                  }
                }}
              >
                <span className={`connection-row-card__icon ${conn.iconMod}`} aria-hidden="true">
                  {conn.icon}
                </span>
                <div className="connection-row-card__body">
                  <strong className="connection-row-card__title">{conn.name}</strong>
                  <span className="connection-row-card__sub">{conn.subtitle}</span>
                </div>
                <span
                  className={`connection-row-card__status ${
                    isDisconnected ? 'is-disconnected' : conn.statusMod
                  }`}
                >
                  {isDisconnected ? 'Desconectado' : conn.statusLabel}
                </span>
              </div>
            )
          })}

          {servers.map((server) => (
            <McpServerCard
              key={server.server_id}
              server={server}
              client={apiClient}
              onChanged={() => void refresh()}
            />
          ))}

          {!loading && !error && servers.length === 0 && activeTab === 'mcp' && (
            <p className="mcp-section__empty">Nenhum servidor MCP configurado ainda.</p>
          )}
        </div>

        <aside
          className="connections-inspector"
          data-testid="connections-inspector-panel"
          aria-label="Detalhes da conexão"
        >
          <div className="connections-inspector__header">
            <span
              className={`connection-row-card__icon ${selectedConnection.iconMod}`}
              aria-hidden="true"
            >
              {selectedConnection.icon}
            </span>
            <div className="connections-inspector__header-copy">
              <h2 className="connections-inspector__title">{selectedConnection.name}</h2>
              <span
                className={`connection-row-card__status ${
                  isSelectedDisconnected ? 'is-disconnected' : selectedConnection.statusMod
                }`}
              >
                {isSelectedDisconnected ? 'Desconectado' : selectedConnection.statusLabel}
              </span>
            </div>
          </div>

          <div className="connections-inspector__field">
            <label htmlFor="connection-use-in-select">Usar em</label>
            <select
              id="connection-use-in-select"
              aria-label="Usar em"
              data-testid="connection-project-scope-select"
              value={currentUseIn}
              onChange={(event) =>
                setUseInByConnection((prev) => ({
                  ...prev,
                  [selectedConnection.id]: event.target.value as UseInScope,
                }))
              }
            >
              <option value="Projeto Auryly">Projeto Auryly</option>
              <option value="Todos os projetos">Todos os projetos</option>
            </select>
          </div>

          <div className="connections-inspector__permissions">
            <h3 className="connections-inspector__section-title">O que o Orin pode fazer</h3>
            <div
              className="connection-permissions-list"
              data-testid="connection-permissions-list"
            >
              {selectedConnection.permissions.map((perm) => {
                const key = `${selectedConnection.id}:${perm.id}`
                const checked = Boolean(permissionState[key])
                return (
                  <label key={perm.id} className="connection-permission-item">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => togglePermission(selectedConnection.id, perm.id)}
                    />
                    <span className="connection-permission-item__copy">
                      <strong>{perm.label}</strong>
                      <span>{perm.description}</span>
                    </span>
                  </label>
                )
              })}
            </div>
          </div>

          <footer className="connections-inspector__footer">
            <p className="connections-inspector__note">
              Toda ação importante pede sua confirmação no chat.
            </p>
            <button
              type="button"
              className="connections-inspector__disconnect-btn"
              data-testid="connections-disconnect-button"
              onClick={() => handleToggleDisconnect(selectedConnection.id)}
            >
              {isSelectedDisconnected
                ? `Reconectar ${selectedConnection.name}`
                : `Desconectar ${selectedConnection.name}`}
            </button>
          </footer>
        </aside>
      </div>

      {formOpen && (
        <McpServerForm
          client={apiClient}
          onClose={() => setFormOpen(false)}
          onCreated={() => {
            setFormOpen(false)
            void refresh()
          }}
        />
      )}
    </section>
  )
}

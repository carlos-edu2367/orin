import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import {
  DEFAULT_DISCORD_INTEGRATION_STATE,
  deleteDiscordIntegration,
  extractDiscordErrorMessage,
  findInvalidSnowflakes,
  getDiscordIntegration,
  parseSnowflakeInputList,
  testDiscordIntegration,
  updateDiscordIntegration,
  type DiscordIntegrationState,
} from '../../api/discord'
import { listMcpServers, type McpServerSummary } from '../../api/mcp'
import { McpServerCard } from './McpServerCard'
import { McpServerForm } from './McpServerForm'
import './discordInspector.css'

type ConnectionTab = 'apps' | 'permissions' | 'mcp'
type UseInScope = 'Todos os projetos' | 'Apenas no projeto atual'

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
  iconMod: 'is-browser' | 'is-files' | 'is-discord'
  statusLabel: 'Ativo' | 'Restrito'
  statusMod: 'is-active' | 'is-restricted'
  defaultUseIn: UseInScope
  permissions: ReadonlyArray<ConnectionPermissionItem>
}

const DISCORD_CONNECTION_ID = 'conn-discord'

const BUILTIN_CONNECTIONS: ReadonlyArray<BuiltinConnectionItem> = [
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
        id: 'browser-navigate',
        label: 'Navegar em páginas públicas quando solicitado',
        description: 'Somente leitura',
        defaultChecked: true,
      },
      {
        id: 'browser-capture',
        label: 'Capturar leitura de página com confirmação',
        description: 'Pede confirmação antes de interagir com formulários',
        defaultChecked: true,
      },
    ],
  },
  {
    id: 'conn-files',
    name: 'Arquivos locais',
    subtitle: 'Acesso restrito às pastas autorizadas em Segurança',
    icon: '📁',
    iconMod: 'is-files',
    statusLabel: 'Restrito',
    statusMod: 'is-restricted',
    defaultUseIn: 'Apenas no projeto atual',
    permissions: [
      {
        id: 'files-read',
        label: 'Ler arquivos nas pastas autorizadas',
        description: 'Somente leitura nas pastas permitidas',
        defaultChecked: true,
      },
      {
        id: 'files-write',
        label: 'Pedir confirmação antes de editar arquivos',
        description: 'Nunca altera arquivos fora das pastas autorizadas',
        defaultChecked: true,
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

function formatDiscordStatusBadge(state: DiscordIntegrationState): {
  label: string
  statusMod: 'is-connected' | 'is-restricted' | 'is-disconnected'
} {
  const status = state.runtime.status
  const rawUsername = (state.runtime.bot_username ?? state.bot_username ?? '').trim()
  const handle = rawUsername ? `@${rawUsername.replace(/^@/, '')}` : null

  if (status === 'connected') {
    return {
      label: handle ? `Conectado · ${handle}` : 'Conectado',
      statusMod: 'is-connected',
    }
  }
  if (status === 'connecting' || status === 'reconnecting') {
    return {
      label: 'Conectando...',
      statusMod: 'is-restricted',
    }
  }
  if (status === 'error') {
    return {
      label: 'Erro',
      statusMod: 'is-disconnected',
    }
  }
  return {
    label: 'Desconectado',
    statusMod: 'is-disconnected',
  }
}

/**
 * Connections & MCP server management (Phase 6 — ref-13.png) + Discord (Orin Friend).
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

  // Discord (Orin Friend) state & form fields
  const [discordState, setDiscordState] = useState<DiscordIntegrationState>(
    DEFAULT_DISCORD_INTEGRATION_STATE,
  )
  const [discordEnabled, setDiscordEnabled] = useState(false)
  const [discordBotToken, setDiscordBotToken] = useState('')
  const [discordAllowedUsersText, setDiscordAllowedUsersText] = useState('')
  const [discordAllowedChannelsText, setDiscordAllowedChannelsText] = useState('')
  const discordFormDirtyRef = useRef(false)
  const [discordBusy, setDiscordBusy] = useState<'save' | 'test' | 'delete' | null>(null)
  const [discordFeedback, setDiscordFeedback] = useState<{
    tone: 'success' | 'warning' | 'error'
    text: string
  } | null>(null)

  const [prevApiClient, setPrevApiClient] = useState(apiClient)
  if (prevApiClient !== apiClient) {
    setPrevApiClient(apiClient)
    setDiscordBotToken('')
    setDiscordFeedback(null)
    setDiscordState(DEFAULT_DISCORD_INTEGRATION_STATE)
    setDiscordEnabled(false)
    setDiscordAllowedUsersText('')
    setDiscordAllowedChannelsText('')
  }

  const syncDiscordFormFromState = useCallback(
    (next: DiscordIntegrationState, force = false) => {
      setDiscordState(next)
      if (force || !discordFormDirtyRef.current) {
        setDiscordEnabled(next.enabled)
        setDiscordAllowedUsersText(next.allowed_discord_user_ids.join(', '))
        setDiscordAllowedChannelsText(next.allowed_channel_ids.join(', '))
      }
    },
    [],
  )

  const refreshDiscord = useCallback(
    (forceFormSync = false, signal?: AbortSignal) =>
      getDiscordIntegration(apiClient, signal)
        .then((value) => {
          syncDiscordFormFromState(value, forceFormSync)
        })
        .catch(() => {
          // Silent fallback when endpoint is unmocked in isolated tests or offline
        }),
    [apiClient, syncDiscordFormFromState],
  )

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
    discordFormDirtyRef.current = false
    void refresh()
    void refreshDiscord(true)
  }, [apiClient, refresh, refreshDiscord])

  const isDiscordSelected = selectedConnectionId === DISCORD_CONNECTION_ID

  // Light polling while the Discord inspector is open or connecting
  useEffect(() => {
    if (
      !isDiscordSelected &&
      discordState.runtime.status !== 'connecting' &&
      discordState.runtime.status !== 'reconnecting'
    ) {
      return
    }
    const controller = new AbortController()
    const timer = window.setInterval(() => {
      void refreshDiscord(false, controller.signal)
    }, 5000)
    return () => {
      controller.abort()
      window.clearInterval(timer)
    }
  }, [isDiscordSelected, discordState.runtime.status, refreshDiscord])

  const selectedConnection: BuiltinConnectionItem = useMemo(
    () =>
      BUILTIN_CONNECTIONS.find((conn) => conn.id === selectedConnectionId) ??
      BUILTIN_CONNECTIONS[0],
    [selectedConnectionId],
  )

  const currentUseIn: UseInScope =
    useInByConnection[selectedConnection.id] ?? selectedConnection.defaultUseIn
  const isSelectedDisconnected = Boolean(disconnectedIds[selectedConnection.id])
  const discordBadge = useMemo(() => formatDiscordStatusBadge(discordState), [discordState])

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

  async function handleSaveDiscord() {
    setDiscordFeedback(null)
    const userIds = parseSnowflakeInputList(discordAllowedUsersText)
    const channelIds = parseSnowflakeInputList(discordAllowedChannelsText)

    const invalidUsers = findInvalidSnowflakes(userIds)
    if (invalidUsers.length > 0) {
      setDiscordFeedback({
        tone: 'error',
        text: `Discord User ID inválido (${invalidUsers[0]}). Informe apenas IDs numéricos Snowflake de 17 a 20 dígitos.`,
      })
      return
    }

    const invalidChannels = findInvalidSnowflakes(channelIds)
    if (invalidChannels.length > 0) {
      setDiscordFeedback({
        tone: 'error',
        text: `ID de canal inválido (${invalidChannels[0]}). Informe apenas IDs numéricos Snowflake de 17 a 20 dígitos.`,
      })
      return
    }

    const cleanToken = discordBotToken.trim()
    if (discordEnabled) {
      if (!cleanToken && !discordState.has_bot_token) {
        setDiscordFeedback({
          tone: 'error',
          text: 'Para ativar o bot do Discord, informe um Bot Token válido (modo fail-closed).',
        })
        return
      }
      if (userIds.length === 0) {
        setDiscordFeedback({
          tone: 'error',
          text: 'Para ativar o bot do Discord, informe ao menos um Discord User ID autorizado na allowlist (modo fail-closed).',
        })
        return
      }
    }

    setDiscordBusy('save')
    try {
      const updated = await updateDiscordIntegration(apiClient, {
        enabled: discordEnabled,
        bot_token: cleanToken ? cleanToken : null,
        allowed_discord_user_ids: userIds,
        allowed_channel_ids: channelIds,
      })
      setDiscordBotToken('')
      discordFormDirtyRef.current = false
      syncDiscordFormFromState(updated, true)
      setDiscordFeedback({
        tone: 'success',
        text: updated.enabled
          ? 'Configuração salva e conexão com o Discord iniciada.'
          : 'Configuração do Discord salva (bot desativado).',
      })
    } catch (err) {
      setDiscordFeedback({
        tone: 'error',
        text: extractDiscordErrorMessage(
          err,
          'Não foi possível salvar a configuração do Discord.',
        ),
      })
    } finally {
      setDiscordBusy(null)
    }
  }

  async function handleTestDiscordDm() {
    setDiscordFeedback(null)
    const userIds = parseSnowflakeInputList(discordAllowedUsersText)
    const invalidUsers = findInvalidSnowflakes(userIds)
    if (invalidUsers.length > 0) {
      setDiscordFeedback({
        tone: 'error',
        text: `Discord User ID inválido para teste (${invalidUsers[0]}). Informe um Snowflake numérico de 17 a 20 dígitos.`,
      })
      return
    }

    const cleanToken = discordBotToken.trim()
    if (!cleanToken && !discordState.has_bot_token) {
      setDiscordFeedback({
        tone: 'error',
        text: 'Nenhum Bot Token configurado para testar. Digite o token acima primeiro.',
      })
      return
    }

    setDiscordBusy('test')
    try {
      const result = await testDiscordIntegration(apiClient, {
        bot_token: cleanToken ? cleanToken : null,
        discord_user_id: userIds[0] ?? null,
        send_dm: true,
      })
      syncDiscordFormFromState(result, false)
      if (result.warning) {
        setDiscordFeedback({
          tone: 'warning',
          text: result.warning,
        })
      } else {
        setDiscordFeedback({
          tone: 'success',
          text:
            result.message ||
            'Orin conectado a este PC e pronto para comandos via Orin Friend.',
        })
      }
    } catch (err) {
      setDiscordFeedback({
        tone: 'error',
        text: extractDiscordErrorMessage(
          err,
          'Falha ao testar a conexão com o Discord.',
        ),
      })
    } finally {
      setDiscordBusy(null)
    }
  }

  async function handleDeleteDiscord() {
    setDiscordFeedback(null)
    setDiscordBusy('delete')
    try {
      const resetState = await deleteDiscordIntegration(apiClient)
      setDiscordBotToken('')
      discordFormDirtyRef.current = false
      syncDiscordFormFromState(resetState, true)
      setDiscordFeedback({
        tone: 'success',
        text: 'Bot desconectado e Bot Token removido com segurança.',
      })
    } catch (err) {
      setDiscordFeedback({
        tone: 'error',
        text: extractDiscordErrorMessage(
          err,
          'Não foi possível desconectar e remover o token do Discord.',
        ),
      })
    } finally {
      setDiscordBusy(null)
    }
  }

  const activeBotHandle = useMemo(() => {
    const raw = (discordState.runtime.bot_username ?? discordState.bot_username ?? '').trim()
    return raw ? `@${raw.replace(/^@/, '')}` : null
  }, [discordState.bot_username, discordState.runtime.bot_username])

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
            const isSelected = conn.id === selectedConnectionId
            const isDisconnected = Boolean(disconnectedIds[conn.id])
            return (
              <div
                key={conn.id}
                role="button"
                aria-selected={isSelected}
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

          <div
            role="button"
            aria-selected={isDiscordSelected}
            className={`connection-row-card${isDiscordSelected ? ' is-selected' : ''}`}
            data-testid="connection-card-conn-discord"
            tabIndex={0}
            onClick={() => setSelectedConnectionId(DISCORD_CONNECTION_ID)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                setSelectedConnectionId(DISCORD_CONNECTION_ID)
              }
            }}
          >
            <span className="connection-row-card__icon is-discord" aria-hidden="true">
              💬
            </span>
            <div className="connection-row-card__body">
              <strong className="connection-row-card__title">Discord (Orin Friend)</strong>
              <span className="connection-row-card__sub">
                Controle projetos e sessões Chat/Code pelo celular via Orin Friend
              </span>
            </div>
            <span
              className={`connection-row-card__status ${discordBadge.statusMod}`}
              data-testid="discord-card-status"
            >
              {discordBadge.label}
            </span>
          </div>

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
          {isDiscordSelected ? (
            <>
              <div className="connections-inspector__header">
                <span className="connection-row-card__icon is-discord" aria-hidden="true">
                  💬
                </span>
                <div className="connections-inspector__header-copy">
                  <h2 className="connections-inspector__title">Discord (Orin Friend)</h2>
                  <div className="discord-inspector__status-row">
                    <span
                      className={`connection-row-card__status ${discordBadge.statusMod}`}
                      data-testid="discord-inspector-status"
                    >
                      {discordBadge.label}
                    </span>
                    {discordState.runtime.status === 'connected' &&
                      discordState.runtime.latency_ms !== null && (
                        <span
                          className="discord-inspector__latency"
                          data-testid="discord-inspector-latency"
                        >
                          {Math.round(discordState.runtime.latency_ms)} ms
                        </span>
                      )}
                  </div>
                </div>
              </div>

              {discordState.runtime.last_error && (
                <div
                  className="discord-inspector__error-banner"
                  role="alert"
                  data-testid="discord-runtime-error"
                >
                  <strong>Erro na conexão com o Discord Gateway:</strong>
                  <span>{discordState.runtime.last_error}</span>
                </div>
              )}

              {discordFeedback && (
                <div
                  className={`discord-inspector__feedback is-${discordFeedback.tone}`}
                  role={discordFeedback.tone === 'error' ? 'alert' : 'status'}
                  data-testid="discord-feedback"
                >
                  {discordFeedback.text}
                </div>
              )}

              <label
                className="connection-permission-item discord-inspector__toggle"
                htmlFor="discord-enabled-toggle"
              >
                <input
                  id="discord-enabled-toggle"
                  type="checkbox"
                  data-testid="discord-enabled-toggle"
                  checked={discordEnabled}
                  onChange={(event) => {
                    discordFormDirtyRef.current = true
                    setDiscordEnabled(event.target.checked)
                  }}
                />
                <span className="connection-permission-item__copy">
                  <strong>Ativar bot do Discord</strong>
                  <span>
                    Conecta ao Discord Gateway v10 em segundo plano (100% outbound, sem abrir portas
                    no PC)
                    {activeBotHandle ? ` como ${activeBotHandle}` : ''}.
                  </span>
                </span>
              </label>

              <div className="connections-inspector__field">
                <label htmlFor="discord-bot-token-input">Bot Token</label>
                <input
                  id="discord-bot-token-input"
                  type="password"
                  className="discord-inspector__input"
                  data-testid="discord-bot-token-input"
                  autoComplete="new-password"
                  value={discordBotToken}
                  placeholder={
                    discordState.has_bot_token
                      ? `Token salvo (${discordState.masked_token_hint ?? '...'}) — deixe em branco para manter`
                      : 'Cole o Bot Token do Discord Developer Portal'
                  }
                  onChange={(event) => {
                    discordFormDirtyRef.current = true
                    setDiscordBotToken(event.target.value)
                  }}
                />
                <span className="discord-inspector__hint">
                  {discordState.has_bot_token
                    ? `Token criptografado salvo (${discordState.masked_token_hint ?? '...'}). Preencha apenas se quiser substituir.`
                    : 'Criptografado em repouso (enc:v1) e nunca reexibido após salvar.'}
                </span>
              </div>

              <div className="connections-inspector__field">
                <label htmlFor="discord-allowed-users-input">
                  Seu Discord User ID (Allowlist obrigatória)
                </label>
                <input
                  id="discord-allowed-users-input"
                  type="text"
                  className="discord-inspector__input"
                  data-testid="discord-allowed-users-input"
                  value={discordAllowedUsersText}
                  placeholder="Ex.: 123456789012345678, 987654321098765432"
                  onChange={(event) => {
                    discordFormDirtyRef.current = true
                    setDiscordAllowedUsersText(event.target.value)
                  }}
                />
                <span className="discord-inspector__hint">
                  Segurança fail-closed: apenas estes IDs numéricos (Snowflake de 17 a 20 dígitos)
                  podem enviar comandos e aprovar ações no Orin.
                </span>
              </div>

              <div className="connections-inspector__field">
                <label htmlFor="discord-allowed-channels-input">
                  IDs de Canais Permitidos (opcional)
                </label>
                <input
                  id="discord-allowed-channels-input"
                  type="text"
                  className="discord-inspector__input"
                  data-testid="discord-allowed-channels-input"
                  value={discordAllowedChannelsText}
                  placeholder="Ex.: 112233445566778899 (opcional)"
                  onChange={(event) => {
                    discordFormDirtyRef.current = true
                    setDiscordAllowedChannelsText(event.target.value)
                  }}
                />
                <span className="discord-inspector__hint">
                  DMs diretas com o bot já funcionam para os usuários autorizados. Use este campo
                  para liberar canais privados específicos em servidores.
                </span>
              </div>

              <details className="discord-inspector__guide" data-testid="discord-setup-guide">
                <summary className="discord-inspector__guide-summary">
                  Como configurar no Discord Developer Portal (1 minuto)
                </summary>
                <ol className="discord-inspector__guide-steps">
                  <li>
                    Acesse <code>discord.com/developers/applications</code> →{' '}
                    <strong>New Application</strong> → aba <strong>Bot</strong>.
                  </li>
                  <li>
                    Ative <strong>Message Content Intent</strong> em{' '}
                    <em>Privileged Gateway Intents</em> e salve.
                  </li>
                  <li>
                    Clique em <strong>Reset Token</strong>, copie o token e cole acima.
                  </li>
                  <li>
                    Em <strong>OAuth2 → URL Generator</strong>, marque <code>bot</code> (
                    <code>Send Messages</code>, <code>Read Message History</code>,{' '}
                    <code>Attach Files</code>, <code>Embed Links</code>), adicione o bot a um
                    servidor privado seu para habilitar DM com você, e copie seu{' '}
                    <strong>User ID</strong> no Discord (
                    <em>
                      Configurações → Avançado → Modo Desenvolvedor → botão direito no seu perfil →
                      Copiar ID do usuário
                    </em>
                    ).
                  </li>
                </ol>
              </details>

              <footer className="connections-inspector__footer discord-inspector__actions">
                <div className="discord-inspector__primary-actions">
                  <button
                    type="button"
                    className="button button--primary"
                    data-testid="discord-save-button"
                    disabled={discordBusy !== null}
                    onClick={() => void handleSaveDiscord()}
                  >
                    {discordBusy === 'save' ? 'Salvando...' : 'Salvar e conectar'}
                  </button>
                  <button
                    type="button"
                    className="button button--secondary"
                    data-testid="discord-test-button"
                    disabled={discordBusy !== null}
                    onClick={() => void handleTestDiscordDm()}
                  >
                    {discordBusy === 'test' ? 'Testando DM...' : 'Testar envio na DM'}
                  </button>
                </div>
                <button
                  type="button"
                  className="connections-inspector__disconnect-btn"
                  data-testid="discord-delete-button"
                  disabled={discordBusy !== null}
                  onClick={() => void handleDeleteDiscord()}
                >
                  {discordBusy === 'delete' ? 'Removendo...' : 'Desconectar / Remover token'}
                </button>
              </footer>
            </>
          ) : (
            <>
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
                  <option value="Todos os projetos">Todos os projetos</option>
                  <option value="Apenas no projeto atual">Apenas no projeto atual</option>
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
            </>
          )}
        </aside>
      </div>

      {formOpen && (
        <McpServerForm
          client={apiClient}
          onClose={() => setFormOpen(false)}
          onCreated={(options) => {
            if (!options?.keepOpen) setFormOpen(false)
            void refresh()
          }}
        />
      )}
    </section>
  )
}

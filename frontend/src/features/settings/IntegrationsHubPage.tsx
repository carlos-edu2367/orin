import {
  useCallback,
  useMemo,
  useState,
  type JSX,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
} from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { PROVIDER_NAMES, type ProviderName } from '../../api/providers'
import { useSession } from '../../app/useSession'
import { McpSection } from '../mcp/McpSection'
import { ProviderDetail } from '../providers/ProviderDetail'
import { ProvidersSection } from '../providers/ProvidersSection'
import { useSettingsBadges } from './useSettingsBadges'

export type IntegrationsTab = 'providers' | 'mcp'

const INTEGRATIONS_TABS: ReadonlyArray<{ id: IntegrationsTab; label: string; badgeKey: 'providers' | 'mcp' }> = [
  { id: 'providers', label: 'Providers & Modelos', badgeKey: 'providers' },
  { id: 'mcp', label: 'Servidores MCP', badgeKey: 'mcp' },
]

const VALID_INTEGRATIONS_TABS: ReadonlyArray<IntegrationsTab> = ['providers', 'mcp']

function isValidIntegrationsTab(value: unknown): value is IntegrationsTab {
  return typeof value === 'string' && (VALID_INTEGRATIONS_TABS as readonly string[]).includes(value)
}

function isValidProviderName(value: unknown): value is ProviderName {
  return typeof value === 'string' && (PROVIDER_NAMES as readonly string[]).includes(value)
}

function resolveTabFromPath(pathname: string): IntegrationsTab | null {
  if (pathname.includes('/mcp')) return 'mcp'
  if (pathname.includes('/providers') || pathname.includes('/vision')) return 'providers'
  return null
}

function resolveProviderFromPath(pathname: string, paramProvider?: string): ProviderName | null {
  if (isValidProviderName(paramProvider)) {
    return paramProvider
  }
  const match = /\/providers\/([^/?#]+)/.exec(pathname)
  if (match && isValidProviderName(match[1])) {
    return match[1]
  }
  return null
}

export function IntegrationsHubPage({
  activeTab,
  selectedProvider,
  client,
  embedded = false,
  basePath,
}: {
  activeTab?: IntegrationsTab
  selectedProvider?: ProviderName | null
  client?: ApiClient
  embedded?: boolean
  basePath?: string
}): JSX.Element {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const badges = useSettingsBadges(apiClient)
  const { capabilities } = useSession()
  const location = useLocation()
  const navigate = useNavigate()
  const params = useParams<{ provider?: string }>()

  const resolvedBasePath = basePath ?? (location.pathname.startsWith('/settings') ? '/settings' : '/integrations')

  const sanitizeProvider = useCallback(
    (candidate: unknown): ProviderName | null => {
      if (!isValidProviderName(candidate)) return null
      if (candidate === 'omniroute' && !capabilities.omniroute) return null
      return candidate
    },
    [capabilities.omniroute],
  )

  const buildSyncKey = useCallback(
    (pathname: string) => `${activeTab ?? ''}|${ pathname }|${selectedProvider ?? ''}|${params.provider ?? ''}`,
    [activeTab, params.provider, selectedProvider],
  )
  const syncKey = buildSyncKey(location.pathname)
  const derivedTab: IntegrationsTab = isValidIntegrationsTab(activeTab)
    ? activeTab
    : (resolveTabFromPath(location.pathname) ?? 'providers')
  const derivedProvider: ProviderName | null = sanitizeProvider(
    selectedProvider !== undefined ? selectedProvider : resolveProviderFromPath(location.pathname, params.provider),
  )

  const [localOverride, setLocalOverride] = useState<{
    key: string
    tab?: IntegrationsTab
    provider?: ProviderName | null
  }>({ key: syncKey })

  const tab: IntegrationsTab =
    localOverride.key === syncKey && localOverride.tab !== undefined ? localOverride.tab : derivedTab
  const openProvider: ProviderName | null =
    localOverride.key === syncKey && localOverride.provider !== undefined ? localOverride.provider : derivedProvider

  const handleCloseDrawer = useCallback(() => {
    const nextPath = `${resolvedBasePath}/providers`
    const nextKey = buildSyncKey(nextPath)
    setLocalOverride((prev) => ({ key: nextKey, tab: prev.key === syncKey ? prev.tab : undefined, provider: null }))
    navigate(nextPath)
  }, [buildSyncKey, navigate, resolvedBasePath, syncKey])

  function handleSelectTab(nextTab: IntegrationsTab) {
    const nextPath = `${resolvedBasePath}/${nextTab}`
    const nextKey = buildSyncKey(nextPath)
    setLocalOverride((prev) => ({
      key: nextKey,
      tab: nextTab,
      provider: nextTab !== 'providers' ? null : (prev.key === syncKey ? prev.provider : undefined),
    }))
    navigate(nextPath)
  }

  function handleProvidersContainerClick(event: ReactMouseEvent<HTMLDivElement>) {
    const target = event.target instanceof HTMLElement ? event.target.closest<HTMLAnchorElement>('a.provider-card') : null
    if (!target) return
    const href = target.getAttribute('href') ?? ''
    const clickedProvider = sanitizeProvider(resolveProviderFromPath(href))
    if (!clickedProvider) return
    event.preventDefault()
    const nextPath = `${resolvedBasePath}/providers/${clickedProvider}`
    const nextKey = buildSyncKey(nextPath)
    setLocalOverride((prev) => ({
      key: nextKey,
      tab: prev.key === syncKey ? prev.tab : undefined,
      provider: clickedProvider,
    }))
    navigate(nextPath)
  }

  function handleDrawerKeyDown(event: ReactKeyboardEvent<HTMLElement>) {
    if (event.key === 'Escape') {
      event.stopPropagation()
      event.nativeEvent.stopImmediatePropagation?.()
      event.preventDefault()
      handleCloseDrawer()
    }
  }

  return (
    <div className={`hub-page${embedded ? ' hub-page--embedded' : ''}`} data-testid="integrations-hub-page">
      <header className="hub-page__header">
        <div className="hub-page__title-group">
          <p className="eyebrow">WORKSPACE / INTEGRAÇÕES</p>
          <h1 className="hub-page__title">Integrações</h1>
          <p className="hub-page__lede">
            Conecte provedores de modelos, configure a leitura visual e gerencie servidores MCP conectados ao seu workspace.
          </p>
        </div>

        <div className="hub-page__tabs" role="tablist" aria-label="Seções de Integrações">
          {INTEGRATIONS_TABS.map((item) => {
            const selected = tab === item.id
            const badge = badges[item.badgeKey]
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                id={`integrations-tab-${item.id}`}
                data-testid={`integrations-tab-${item.id}`}
                aria-selected={selected}
                aria-controls={`integrations-panel-${item.id}`}
                className={`hub-page__tab${selected ? ' is-active' : ''}`}
                onClick={() => handleSelectTab(item.id)}
              >
                <span>{item.label}</span>
                {badge && (
                  <span className="hub-page__tab-badge">
                    {badge.pending && <span className="settings-nav__pending" role="img" aria-label="Aguardando sua ação" />}
                    {badge.value}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      </header>

      <div
        id={`integrations-panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`integrations-tab-${tab}`}
        className="hub-page__body"
      >
        {tab === 'providers' && (
          <div className="hub-page__providers-stage" onClick={handleProvidersContainerClick}>
            <ProvidersSection client={apiClient} />
          </div>
        )}

        {tab === 'mcp' && <McpSection client={apiClient} />}
      </div>

      {openProvider && (
        <div
          className="slide-over-drawer__backdrop"
          data-testid="slide-over-backdrop"
          onClick={handleCloseDrawer}
        >
          <aside
            className="slide-over-drawer settings-content__drawer"
            data-testid="settings-drawer-slot"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={handleDrawerKeyDown}
          >
            <ProviderDetail
              provider={openProvider}
              client={apiClient}
              onClose={handleCloseDrawer}
            />
          </aside>
        </div>
      )}
    </div>
  )
}

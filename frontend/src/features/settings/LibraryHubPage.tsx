import { useMemo, useState, type JSX } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { createSkillsClient } from '../../api/skills'
import { MemoryPage } from '../memory/MemoryPage'
import { PluginsSection } from '../plugins/PluginsSection'
import { SkillsSection } from '../skills/SkillsSection'
import { useSettingsBadges } from './useSettingsBadges'

export type LibraryTab = 'skills' | 'plugins' | 'memory'

const LIBRARY_TABS: ReadonlyArray<{ id: LibraryTab; label: string }> = [
  { id: 'skills', label: 'Skills' },
  { id: 'plugins', label: 'Plugins' },
  { id: 'memory', label: 'Memória' },
]

const VALID_LIBRARY_TABS: ReadonlyArray<LibraryTab> = ['skills', 'plugins', 'memory']

function isValidLibraryTab(value: unknown): value is LibraryTab {
  return typeof value === 'string' && (VALID_LIBRARY_TABS as readonly string[]).includes(value)
}

function resolveTabFromPath(pathname: string): LibraryTab | null {
  if (pathname.includes('/plugins')) return 'plugins'
  if (pathname.includes('/memory')) return 'memory'
  if (pathname.includes('/skills')) return 'skills'
  return null
}

export function LibraryHubPage({
  activeTab,
  client,
  embedded = false,
}: {
  activeTab?: LibraryTab
  client?: ApiClient
  embedded?: boolean
}): JSX.Element {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const skillsClient = useMemo(() => createSkillsClient(apiClient), [apiClient])
  const badges = useSettingsBadges(apiClient)
  const location = useLocation()
  const navigate = useNavigate()

  const syncKey = `${activeTab ?? ''}|${location.pathname}`
  const derivedTab: LibraryTab = isValidLibraryTab(activeTab)
    ? activeTab
    : (resolveTabFromPath(location.pathname) ?? 'skills')

  const [localOverride, setLocalOverride] = useState<{ key: string; tab?: LibraryTab }>({ key: syncKey })
  const tab: LibraryTab =
    localOverride.key === syncKey && localOverride.tab !== undefined ? localOverride.tab : derivedTab

  function handleSelectTab(nextTab: LibraryTab) {
    const nextPath = `/library/${nextTab}`
    setLocalOverride({ key: `${activeTab ?? ''}|${nextPath}`, tab: nextTab })
    navigate(nextPath)
  }

  return (
    <div className={`hub-page${embedded ? ' hub-page--embedded' : ''}`} data-testid="library-hub-page">
      <header className="hub-page__header">
        <div className="hub-page__title-group">
          <p className="eyebrow">WORKSPACE / BIBLIOTECA</p>
          <h1 className="hub-page__title">Biblioteca</h1>
          <p className="hub-page__lede">
            Gerencie procedimentos (Skills), pacotes de extensão (Plugins) e fatos persistentes na memória do agente.
          </p>
        </div>

        <div className="hub-page__tabs" role="tablist" aria-label="Seções da Biblioteca">
          {LIBRARY_TABS.map((item) => {
            const selected = tab === item.id
            const badge = badges[item.id]
            return (
              <button
                key={item.id}
                type="button"
                role="tab"
                id={`library-tab-${item.id}`}
                data-testid={`library-tab-${item.id}`}
                aria-selected={selected}
                aria-controls={`library-panel-${item.id}`}
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
        id={`library-panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`library-tab-${tab}`}
        className="hub-page__body"
      >
        {tab === 'skills' && <SkillsSection client={skillsClient} />}
        {tab === 'plugins' && <PluginsSection client={apiClient} />}
        {tab === 'memory' && <MemoryPage embedded client={apiClient} />}
      </div>
    </div>
  )
}

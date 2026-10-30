import type { ReactNode } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { useSession } from '../../app/useSession'
import { findSettingsItem, visibleSettingsGroups, type SettingsBadge } from './sections'

export type BadgeState = { value: string; pending?: boolean }
export type BadgeMap = Partial<Record<SettingsBadge, BadgeState>>

type PrimaryCategory = {
  id: string
  label: string
  to: string
  matchPrefixes: readonly string[]
  icon: ReactNode
}

const PRIMARY_CATEGORIES: readonly PrimaryCategory[] = [
  {
    id: 'general',
    label: 'Geral',
    to: '/settings/general',
    matchPrefixes: ['/settings/general', '/settings/workspace', '/settings/background', '/settings/browser'],
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path
          d="M2.5 4.5H13.5M2.5 8H13.5M2.5 11.5H13.5"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    id: 'providers',
    label: 'Modelos e provedores',
    to: '/settings/providers',
    matchPrefixes: ['/settings/providers', '/settings/vision', '/settings/media'],
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <circle cx="8" cy="8" r="5.25" stroke="currentColor" strokeWidth="1.4" />
        <circle cx="8" cy="8" r="1.75" fill="currentColor" />
      </svg>
    ),
  },
  {
    id: 'voice',
    label: 'Voz',
    to: '/settings/voice',
    matchPrefixes: ['/settings/voice'],
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <rect x="6" y="2.25" width="4" height="7" rx="2" stroke="currentColor" strokeWidth="1.4" />
        <path
          d="M3.75 7.5A4.25 4.25 0 0 0 12.25 7.5M8 11.75V13.75"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    id: 'memory',
    label: 'Memória',
    to: '/settings/memory',
    matchPrefixes: ['/settings/memory'],
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path
          d="M3.25 4.5C3.25 3.67 3.92 3 4.75 3H11.25C12.08 3 12.75 3.67 12.75 4.5V11.5C12.75 12.33 12.08 13 11.25 13H4.75C3.92 13 3.25 12.33 3.25 11.5V4.5Z"
          stroke="currentColor"
          strokeWidth="1.4"
        />
        <path d="M5.5 6.25H10.5M5.5 9.25H8.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    id: 'security',
    label: 'Segurança',
    to: '/settings/permissions',
    matchPrefixes: [
      '/settings/permissions',
      '/settings/security',
      '/settings/trusted-folders',
      '/settings/hooks',
      '/settings/mcp-scopes',
    ],
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path
          d="M8 2.25L12.75 4.25V7.75C12.75 10.65 10.72 12.88 8 13.75C5.28 12.88 3.25 10.65 3.25 7.75V4.25L8 2.25Z"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
  {
    id: 'updates',
    label: 'Atualizações',
    to: '/settings/about',
    matchPrefixes: ['/settings/about'],
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
        <path
          d="M8 3.25V10.25M8 3.25L5.5 5.75M8 3.25L10.5 5.75M3.5 12.75H12.5"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
]

function isPrimaryActive(pathname: string, prefixes: readonly string[]): boolean {
  return prefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`))
}

export function SettingsNav({ badges }: { badges: BadgeMap }) {
  const { pathname } = useLocation()
  const current = findSettingsItem(pathname)
  const session = useSession()
  return (
    <nav className="settings-nav" aria-label="Settings">
      <div
        className="settings-nav__primary"
        data-testid="settings-primary-categories"
        role="group"
        aria-label="Categorias principais"
      >
        {PRIMARY_CATEGORIES.map((category) => {
          const isActive = isPrimaryActive(pathname, category.matchPrefixes)
          return (
            <Link
              key={category.id}
              to={category.to}
              className={isActive ? 'settings-nav__primary-item is-active' : 'settings-nav__primary-item'}
              aria-current={isActive ? 'page' : undefined}
            >
              <span className="settings-nav__primary-icon" aria-hidden="true">
                {category.icon}
              </span>
              <span className="settings-nav__primary-label">{category.label}</span>
            </Link>
          )
        })}
      </div>

      <div className="settings-nav__advanced">
        {visibleSettingsGroups(session).map((group) => (
          <div className="settings-nav__group" key={group.title}>
            <p className="settings-nav__group-title">{group.title}</p>
            {group.items.map((item) => {
              const badge = item.badge ? badges[item.badge] : undefined
              const active = current?.id === item.id
              return (
                <NavLink
                  key={item.id}
                  to={item.path}
                  className={active ? 'settings-nav__item is-active' : 'settings-nav__item'}
                  aria-current={active ? 'page' : undefined}
                >
                  <span className="settings-nav__label">{item.label}</span>
                  {badge && <span className="settings-nav__badge" data-testid="settings-nav-badge">
                    {badge.pending && <span className="settings-nav__pending" role="img" aria-label="Aguardando sua ação" />}
                    {badge.value}
                  </span>}
                </NavLink>
              )
            })}
          </div>
        ))}
      </div>
    </nav>
  )
}

import { NavLink, useLocation } from 'react-router-dom'
import { SETTINGS_GROUPS, findSettingsItem, type SettingsBadge } from './sections'

export type BadgeState = { value: string; pending?: boolean }
export type BadgeMap = Partial<Record<SettingsBadge, BadgeState>>

export function SettingsNav({ badges }: { badges: BadgeMap }) {
  const current = findSettingsItem(useLocation().pathname)
  return (
    <nav className="settings-nav" aria-label="Settings">
      {SETTINGS_GROUPS.map((group) => (
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
    </nav>
  )
}

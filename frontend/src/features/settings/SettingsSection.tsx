import type { ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { findSettingsItem } from './sections'

export function SettingsSection({ eyebrow, title, lede, actions, children }: {
  eyebrow: string
  title?: string
  lede?: string
  actions?: ReactNode
  children: ReactNode
}) {
  const item = findSettingsItem(useLocation().pathname)
  return <>
    <div className="settings-section__head">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title ?? item?.label ?? 'Settings'}</h1>
        <p className="settings-content__lede">{lede ?? item?.lede ?? ''}</p>
      </div>
      {actions && <div className="settings-section__actions">{actions}</div>}
    </div>
    {children}
  </>
}

import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Brand } from '../../components/Brand'
import { SettingsNav, type BadgeMap } from './SettingsNav'

export function SettingsShell({ badges, drawer, children }: { badges: BadgeMap; drawer?: ReactNode; children: ReactNode }) {
  return <main className="settings-shell">
    <header className="settings-shell__bar">
      <Brand to="/" />
      <span>Settings</span>
      <Link to="/">Voltar ao chat</Link>
    </header>
    <div className="settings-shell__body">
      <SettingsNav badges={badges} />
      <section className="settings-content">
        {children}
        {drawer && <div className="settings-content__drawer" data-testid="settings-drawer-slot">{drawer}</div>}
      </section>
    </div>
  </main>
}

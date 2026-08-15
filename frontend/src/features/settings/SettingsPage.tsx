import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Brand } from '../../components/Brand'

const SECTIONS = [
  ['general', 'General', 'Preferências locais e comportamento do runtime'],
  ['providers', 'Providers', 'Conexões, modelos e chaves'],
  ['omniroute', 'OmniRoute', 'Gateway local e inicialização'],
  ['memory', 'Memory', 'Memórias globais e contextuais'],
  ['skills', 'Skills', 'Biblioteca e associações de agentes'],
  ['mcp', 'MCP', 'Servidores MCP conectados e suas tools'],
  ['plugins', 'Plugins', 'Pacotes declarativos e suas contribuições'],
  ['agents', 'Agents', 'Configurações por agente'],
  ['workspace', 'Workspace', 'Projetos e arquivos locais'],
  ['advanced', 'Advanced', 'Opções técnicas disponíveis'],
] as const

export function SettingsPage({ children }: { children?: ReactNode }) {
  const location = useLocation()
  const current = location.pathname.split('/').at(-1) ?? ''
  return <main className="settings-shell">
    <header className="settings-shell__bar"><Brand to="/" /><span>Settings</span><Link to="/">Voltar ao chat</Link></header>
    <div className="settings-shell__body">
      <nav className="settings-nav" aria-label="Settings">
        {SECTIONS.map(([id, label]) => <Link key={id} to={`/settings/${id}`} className={current === id ? 'is-active' : ''}>{label}</Link>)}
      </nav>
      <section className="settings-content">
        {children ?? <><p className="eyebrow">MANAGEMENT</p><h1>Settings</h1><p className="settings-content__lede">Ajustes globais ficam aqui. O chat continua livre para o trabalho.</p><div className="settings-index">{SECTIONS.slice(0, 5).map(([id, label, detail]) => <Link key={id} to={`/settings/${id}`}><strong>{label}</strong><span>{detail}</span></Link>)}</div></>}
      </section>
    </div>
  </main>
}

export function SettingsPlaceholder({ title, description }: { title: string; description: string }) {
  return <SettingsPage><p className="eyebrow">SETTINGS</p><h1>{title}</h1><p className="settings-content__lede">{description}</p></SettingsPage>
}

import { useEffect, useMemo, useRef, useState, type JSX, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { useTheme, type ThemeAccent, type ThemeMode } from '../../app/ThemeContext'
import { useSession } from '../../app/useSession'
import { UsersSection } from '../users/UsersSection'
import { AboutSection } from './AboutSection'
import { BackgroundSection } from './BackgroundSection'
import { BrowserSection } from './BrowserSection'
import { RuntimeSettingsPage } from './RuntimeSettingsPage'
import { WorkspaceSection } from './WorkspaceSection'

export type PreferencesTab = 'appearance' | 'general' | 'system' | 'about'

export type PreferencesModalProps = {
  open: boolean
  initialTab?: PreferencesTab
  onClose: () => void
  client?: ApiClient
}

const PREFERENCES_TABS: ReadonlyArray<{ id: PreferencesTab; label: string; description: string }> = [
  { id: 'appearance', label: 'Aparência', description: 'Tema base, cor de destaque e efeitos atmosféricos.' },
  { id: 'general', label: 'Geral & Modo Code', description: 'Autonomia padrão, acompanhamento e limites de execução.' },
  { id: 'system', label: 'Sistema & Ambiente', description: 'Workspaces locais, segundo plano e motor do navegador.' },
  { id: 'about', label: 'Conta & Sobre', description: 'Estado da instalação, versões e perfis de acesso.' },
]

const VALID_TABS: ReadonlyArray<PreferencesTab> = ['appearance', 'general', 'system', 'about']

function normalizeTab(tab?: PreferencesTab): PreferencesTab {
  return tab && VALID_TABS.includes(tab) ? tab : 'appearance'
}

const THEME_MODES: ReadonlyArray<{ id: ThemeMode; label: string; hint: string }> = [
  { id: 'dark', label: 'Orin Escuro (Padrão)', hint: 'Grafite quente e lavanda' },
  { id: 'carbon', label: 'Carbon OLED', hint: 'Preto puro de alto contraste' },
  { id: 'midnight', label: 'Noite Profunda', hint: 'Azul-marinho espacial sereno' },
  { id: 'forest', label: 'Floresta Noturna', hint: 'Grafite esverdeado orgânico' },
  { id: 'light', label: 'Papel Quente', hint: 'Claro editorial confortável' },
  { id: 'system', label: 'Automático (Sistema)', hint: 'Segue a preferência do SO' },
]

const THEME_ACCENTS: ReadonlyArray<{ id: ThemeAccent; label: string }> = [
  { id: 'violet', label: 'Lavanda Orin' },
  { id: 'sapphire', label: 'Safira' },
  { id: 'emerald', label: 'Esmeralda' },
  { id: 'amber', label: 'Âmbar Solar' },
  { id: 'rose', label: 'Quartzo Rosa' },
  { id: 'mono', label: 'Monocromático' },
]

export function PreferencesModal({ open, initialTab = 'appearance', onClose, client }: PreferencesModalProps): JSX.Element | null {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const session = useSession()
  const { mode, accent, ambient, setMode, setAccent, setAmbient } = useTheme()
  const syncKey = `${String(open)}|${String(initialTab)}`
  const derivedTab = normalizeTab(initialTab)
  const [localTab, setLocalTab] = useState<{ key: string; tab?: PreferencesTab }>({ key: syncKey })
  const activeTab: PreferencesTab =
    localTab.key === syncKey && localTab.tab !== undefined ? localTab.tab : derivedTab
  const setActiveTab = (next: PreferencesTab) => setLocalTab({ key: syncKey, tab: next })
  const dialogRef = useRef<HTMLDivElement>(null)
  const restoreFocusRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!open) return undefined
    restoreFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    dialogRef.current?.focus()
    return () => {
      restoreFocusRef.current?.focus()
    }
  }, [open])

  useEffect(() => {
    if (!open) return undefined
    const onDocumentKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      // If the event originated inside the dialog, let the React bubble handler on
      // dialogRef handle it so inner controls can stopPropagation if needed.
      if (dialogRef.current && event.target instanceof Node && dialogRef.current.contains(event.target)) {
        return
      }
      event.stopPropagation()
      event.stopImmediatePropagation()
      event.preventDefault()
      onClose()
    }
    document.addEventListener('keydown', onDocumentKeyDown, true)
    return () => document.removeEventListener('keydown', onDocumentKeyDown, true)
  }, [open, onClose])

  if (!open) return null

  function handleDialogKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.stopPropagation()
      event.nativeEvent.stopImmediatePropagation?.()
      event.preventDefault()
      onClose()
    }
  }

  const currentTabMeta = PREFERENCES_TABS.find((item) => item.id === activeTab) ?? PREFERENCES_TABS[0]

  return (
    <div className="preferences-modal__backdrop" data-testid="preferences-modal-backdrop" onClick={onClose}>
      <div
        ref={dialogRef}
        className="preferences-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Preferências"
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
        onKeyDown={handleDialogKeyDown}
      >
        <aside className="preferences-modal__sidebar">
          <div className="preferences-modal__brand">
            <span className="preferences-modal__eyebrow">CONFIGURAÇÕES</span>
            <h2 className="preferences-modal__heading">Preferências</h2>
          </div>

          <div className="preferences-modal__tabs" role="tablist" aria-orientation="vertical" aria-label="Seções de preferências">
            {PREFERENCES_TABS.map((tab) => {
              const selected = activeTab === tab.id
              return (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  id={`preferences-tab-${tab.id}`}
                  data-testid={`preferences-tab-${tab.id}`}
                  aria-selected={selected}
                  aria-controls={`preferences-panel-${tab.id}`}
                  className={`preferences-modal__tab${selected ? ' is-active' : ''}`}
                  onClick={() => setActiveTab(tab.id)}
                >
                  <span>{tab.label}</span>
                </button>
              )
            })}
          </div>
        </aside>

        <section
          id={`preferences-panel-${activeTab}`}
          role="tabpanel"
          aria-labelledby={`preferences-tab-${activeTab}`}
          className="preferences-modal__body"
        >
          <header className="preferences-modal__header">
            <div>
              <h3 className="preferences-modal__title">{currentTabMeta.label}</h3>
              <p className="preferences-modal__lede">{currentTabMeta.description}</p>
            </div>
            <button
              type="button"
              className="button button--quiet preferences-modal__close"
              data-testid="close-preferences-modal"
              aria-label="Fechar preferências"
              onClick={onClose}
            >
              Fechar
            </button>
          </header>

          <div className="preferences-modal__content">
            {activeTab === 'appearance' && (
              <div className="preferences-appearance">
                <section className="preferences-appearance__group" aria-labelledby="pref-theme-mode-heading">
                  <div className="preferences-appearance__group-head">
                    <h4 id="pref-theme-mode-heading">Modo de aparência</h4>
                    <p>Escolha a superfície base do estúdio. Aplicado instantaneamente.</p>
                  </div>
                  <div className="preferences-theme-grid">
                    {THEME_MODES.map((item) => {
                      const active = mode === item.id
                      return (
                        <button
                          key={item.id}
                          type="button"
                          data-testid={`theme-mode-${item.id}`}
                          aria-pressed={active}
                          className={`preferences-theme-card is-${item.id}${active ? ' is-active' : ''}`}
                          onClick={() => setMode(item.id)}
                        >
                          <span className={`preferences-theme-card__preview is-${item.id}`} aria-hidden="true">
                            <span className="preferences-theme-card__preview-rail" />
                            <span className="preferences-theme-card__preview-canvas" />
                          </span>
                          <strong className="preferences-theme-card__label">{item.label}</strong>
                          <small className="preferences-theme-card__hint">{item.hint}</small>
                        </button>
                      )
                    })}
                  </div>
                </section>

                <section className="preferences-appearance__group" aria-labelledby="pref-theme-accent-heading">
                  <div className="preferences-appearance__group-head">
                    <h4 id="pref-theme-accent-heading">Cor de destaque</h4>
                    <p>Define a cor do sinal principal, indicadores ativos e botões primários.</p>
                  </div>
                  <div className="preferences-accent-list">
                    {THEME_ACCENTS.map((item) => {
                      const active = accent === item.id
                      return (
                        <button
                          key={item.id}
                          type="button"
                          data-testid={`theme-accent-${item.id}`}
                          aria-pressed={active}
                          className={`preferences-accent-swatch is-${item.id}${active ? ' is-active' : ''}`}
                          onClick={() => setAccent(item.id)}
                        >
                          <span className={`preferences-accent-swatch__dot is-${item.id}`} aria-hidden="true" />
                          <span className="preferences-accent-swatch__label">{item.label}</span>
                        </button>
                      )
                    })}
                  </div>
                </section>

                <section className="preferences-appearance__group" aria-labelledby="pref-theme-ambient-heading">
                  <div className="preferences-appearance__group-head">
                    <h4 id="pref-theme-ambient-heading">Atmosfera e efeitos</h4>
                    <p>Controla a esfera orbital e o brilho sutil de fundo no palco inicial.</p>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={ambient === 'on'}
                    data-testid="theme-ambient-toggle"
                    className={`preferences-ambient-toggle${ambient === 'on' ? ' is-active' : ''}`}
                    onClick={() => setAmbient(ambient === 'on' ? 'off' : 'on')}
                  >
                    <div className="preferences-ambient-toggle__copy">
                      <strong>Exibir esfera orbital e brilho atmosférico no palco inicial</strong>
                      <small>Desative para zerar efeitos visuais em segundo plano e deixar o palco 100% sólido.</small>
                    </div>
                    <span className="preferences-ambient-toggle__pill" aria-hidden="true">
                      <span className="preferences-ambient-toggle__thumb" />
                    </span>
                  </button>
                </section>
              </div>
            )}

            {activeTab === 'general' && (
              <div className="preferences-modal__stack">
                <RuntimeSettingsPage embedded client={apiClient} />
              </div>
            )}

            {activeTab === 'system' && (
              <div className="preferences-modal__stack">
                <WorkspaceSection />
                <BackgroundSection client={apiClient} />
                <BrowserSection client={apiClient} />
              </div>
            )}

            {activeTab === 'about' && (
              <div className="preferences-modal__stack">
                <AboutSection client={apiClient} />
                {session.isAdmin && session.capabilities.user_admin && <UsersSection client={apiClient} />}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

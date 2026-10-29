import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSession } from '../../app/useSession'

const LOCAL_DISPLAY_NAME_KEY = 'orin.user.displayName'

export function readLocalDisplayName(): string {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return 'Você'
    const stored = window.localStorage.getItem(LOCAL_DISPLAY_NAME_KEY)?.trim()
    return stored && stored.length > 0 ? stored.slice(0, 60) : 'Você'
  } catch {
    return 'Você'
  }
}

export type ProfileMenuProps = {
  showLocalFallback?: boolean
  onOpenPreferences?: () => void
}

export function ProfileMenu({ showLocalFallback = false, onOpenPreferences }: ProfileMenuProps = {}) {
  const session = useSession()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.stopPropagation()
        setOpen(false)
      }
    }
    function handlePointerDown(event: PointerEvent) {
      const target = event.target as Node | null
      if (containerRef.current && target && !containerRef.current.contains(target)) {
        setOpen(false)
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    document.addEventListener('pointerdown', handlePointerDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.removeEventListener('pointerdown', handlePointerDown)
    }
  }, [open])

  const isSessionUser = session.mode === 'session' && Boolean(session.user)
  if (!isSessionUser && !showLocalFallback) return null

  const name = isSessionUser && session.user ? session.user.displayName : readLocalDisplayName()
  const initial = (name.trim().slice(0, 1) || 'V').toUpperCase()

  return (
    <div ref={containerRef} className="profile-menu" data-testid="sidebar-profile-menu">
      <button
        type="button"
        className="profile-menu__button"
        data-testid="sidebar-profile-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Perfil: ${name}`}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="profile-menu__avatar" aria-hidden="true">{initial}</span>
        <span className="profile-menu__name project-navigation__rail-hide">{name}</span>
        <span className="profile-menu__chevron project-navigation__rail-hide" aria-hidden="true">›</span>
      </button>
      {open && (
        <div className="profile-menu__list" role="menu" aria-label="Conta">
          {onOpenPreferences && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false)
                onOpenPreferences()
              }}
            >
              Aparência e temas
            </button>
          )}
          {isSessionUser ? (
            <>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false)
                  navigate('/change-password')
                }}
              >
                Trocar senha
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false)
                  void session.signOut()
                }}
              >
                Sair
              </button>
            </>
          ) : (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false)
                navigate('/settings/general')
              }}
            >
              Configurações
            </button>
          )}
        </div>
      )}
    </div>
  )
}

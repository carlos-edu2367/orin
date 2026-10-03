import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useSession } from '../../app/useSession'

export function ProfileMenu() {
  const session = useSession()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  if (session.mode !== 'session' || !session.user) return null
  const name = session.user.displayName
  return (
    <div className="profile-menu">
      <button type="button" className="profile-menu__button" aria-haspopup="menu" aria-expanded={open} aria-label={`Perfil: ${name}`} onClick={() => setOpen((value) => !value)}>
        <span className="profile-menu__avatar" aria-hidden="true">{name.slice(0, 1).toUpperCase()}</span>
        <span className="profile-menu__name">{name}</span>
      </button>
      {open && (
        <div className="profile-menu__list" role="menu">
          <button type="button" role="menuitem" onClick={() => { setOpen(false); navigate('/change-password') }}>Trocar senha</button>
          <button type="button" role="menuitem" onClick={() => { setOpen(false); void session.signOut() }}>Sair</button>
        </div>
      )}
    </div>
  )
}

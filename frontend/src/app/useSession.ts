import { createContext, useContext } from 'react'
import type { SessionUser } from '../api/auth'
import { LOCAL_CAPABILITIES, type Capabilities } from '../api/session'

export type SessionContextValue = {
  mode: 'local' | 'session'
  user: SessionUser | null
  capabilities: Capabilities
  isAdmin: boolean
  refresh: () => Promise<void>
  signOut: () => Promise<void>
}

const LOCAL_SESSION: SessionContextValue = {
  mode: 'local', user: null, capabilities: LOCAL_CAPABILITIES, isAdmin: false,
  refresh: async () => undefined, signOut: async () => undefined,
}

export const SessionContext = createContext<SessionContextValue>(LOCAL_SESSION)

export function useSession(): SessionContextValue {
  return useContext(SessionContext)
}

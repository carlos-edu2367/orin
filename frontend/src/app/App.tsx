import { useMemo } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { createBrowserApiClient } from '../api/client'
import { KeybindingsProvider } from '../features/keybindings/engine'
import { routes } from './routes'
import { SessionGate } from './SessionGate'
import { UpdateBanner } from '../components/UpdateBanner'

export function App() {
  // W7: one keybinding engine for the whole interface (defaults, then the person's keybindings.json files).
  const client = useMemo(() => createBrowserApiClient(), [])
  return (
    <KeybindingsProvider client={client}>
      <UpdateBanner />
      <BrowserRouter>
        <SessionGate>
          <Routes>
            {routes.map((route) => <Route key={route.path} path={route.path} element={route.element} />)}
            <Route path="*" element={routes[0].element} />
          </Routes>
        </SessionGate>
      </BrowserRouter>
    </KeybindingsProvider>
  )
}

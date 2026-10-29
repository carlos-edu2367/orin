import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type ThemeMode = 'dark' | 'carbon' | 'midnight' | 'forest' | 'light' | 'system'
export type ResolvedTheme = 'dark' | 'carbon' | 'midnight' | 'forest' | 'light'
export type ThemeAccent = 'violet' | 'sapphire' | 'emerald' | 'amber' | 'rose' | 'mono'
export type ThemeAmbient = 'on' | 'off'

const MODE_KEY = 'orin.theme.mode'
const ACCENT_KEY = 'orin.theme.accent'
const AMBIENT_KEY = 'orin.theme.ambient'

const VALID_MODES: readonly ThemeMode[] = ['dark', 'carbon', 'midnight', 'forest', 'light', 'system']
const VALID_ACCENTS: readonly ThemeAccent[] = ['violet', 'sapphire', 'emerald', 'amber', 'rose', 'mono']
const VALID_AMBIENTS: readonly ThemeAmbient[] = ['on', 'off']

function readValid<T extends string>(key: string, valid: readonly T[], fallback: T): T {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return fallback
    const raw = window.localStorage.getItem(key)
    return raw && (valid as readonly string[]).includes(raw) ? (raw as T) : fallback
  } catch {
    return fallback
  }
}

function writeStorage(key: string, value: string): void {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(key, value)
    }
  } catch {
    // Ignore storage errors in restricted modes or when quota is exceeded
  }
}

function resolveSystemTheme(): 'dark' | 'light' {
  try {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'dark'
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function resolveThemeMode(mode: ThemeMode): ResolvedTheme {
  return mode === 'system' ? resolveSystemTheme() : mode
}

export function applyThemeAttributes(mode: ThemeMode, accent: ThemeAccent, ambient: ThemeAmbient): ResolvedTheme {
  const resolved = resolveThemeMode(mode)
  if (typeof document !== 'undefined' && document.documentElement) {
    const root = document.documentElement
    root.setAttribute('data-theme', resolved)
    root.setAttribute('data-theme-preference', mode)
    root.setAttribute('data-accent', accent)
    root.setAttribute('data-ambient', ambient)
  }
  return resolved
}

export function initThemeDom(): void {
  if (typeof window === 'undefined') return
  const mode = readValid(MODE_KEY, VALID_MODES, 'dark')
  const accent = readValid(ACCENT_KEY, VALID_ACCENTS, 'violet')
  const ambient = readValid(AMBIENT_KEY, VALID_AMBIENTS, 'on')
  applyThemeAttributes(mode, accent, ambient)
}

export type ThemeContextValue = {
  mode: ThemeMode
  resolvedTheme: ResolvedTheme
  accent: ThemeAccent
  ambient: ThemeAmbient
  setMode: (next: ThemeMode) => void
  setAccent: (next: ThemeAccent) => void
  setAmbient: (next: ThemeAmbient) => void
  cycleMode: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => readValid(MODE_KEY, VALID_MODES, 'dark'))
  const [accent, setAccentState] = useState<ThemeAccent>(() => readValid(ACCENT_KEY, VALID_ACCENTS, 'violet'))
  const [ambient, setAmbientState] = useState<ThemeAmbient>(() => readValid(AMBIENT_KEY, VALID_AMBIENTS, 'on'))
  const [systemTheme, setSystemTheme] = useState<'dark' | 'light'>(() => resolveSystemTheme())

  const resolvedTheme: ResolvedTheme = mode === 'system' ? systemTheme : mode

  useEffect(() => {
    if (typeof document !== 'undefined' && document.documentElement) {
      const root = document.documentElement
      root.setAttribute('data-theme', resolvedTheme)
      root.setAttribute('data-theme-preference', mode)
      root.setAttribute('data-accent', accent)
      root.setAttribute('data-ambient', ambient)
      root.setAttribute('data-desktop', typeof window !== 'undefined' && Boolean(window.orinDesktop) ? 'true' : 'false')
    }
  }, [mode, resolvedTheme, accent, ambient])

  useEffect(() => {
    if (mode !== 'system' || typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const media = window.matchMedia('(prefers-color-scheme: light)')
    const onChange = () => {
      const nextSys = media.matches ? 'light' : 'dark'
      setSystemTheme(nextSys)
      if (typeof document !== 'undefined' && document.documentElement) {
        document.documentElement.setAttribute('data-theme', nextSys)
      }
    }
    if (typeof media.addEventListener === 'function') {
      media.addEventListener('change', onChange)
      return () => media.removeEventListener('change', onChange)
    }
    if (typeof media.addListener === 'function') {
      media.addListener(onChange)
      return () => media.removeListener(onChange)
    }
    return undefined
  }, [mode])

  const setMode = useCallback(
    (next: ThemeMode) => {
      const validNext = VALID_MODES.includes(next) ? next : 'dark'
      setModeState(validNext)
      if (validNext === 'system') {
        setSystemTheme(resolveSystemTheme())
      }
      writeStorage(MODE_KEY, validNext)
      applyThemeAttributes(validNext, accent, ambient)
    },
    [accent, ambient],
  )

  const setAccent = useCallback(
    (next: ThemeAccent) => {
      const validNext = VALID_ACCENTS.includes(next) ? next : 'violet'
      setAccentState(validNext)
      writeStorage(ACCENT_KEY, validNext)
      applyThemeAttributes(mode, validNext, ambient)
    },
    [mode, ambient],
  )

  const setAmbient = useCallback(
    (next: ThemeAmbient) => {
      const validNext = VALID_AMBIENTS.includes(next) ? next : 'on'
      setAmbientState(validNext)
      writeStorage(AMBIENT_KEY, validNext)
      applyThemeAttributes(mode, accent, validNext)
    },
    [mode, accent],
  )

  const cycleMode = useCallback(() => {
    const order: readonly ResolvedTheme[] = ['dark', 'light', 'carbon']
    const currentIdx = order.indexOf(resolvedTheme)
    const next = order[(currentIdx + 1) % order.length]
    setMode(next)
  }, [resolvedTheme, setMode])

  const value = useMemo<ThemeContextValue>(
    () => ({
      mode,
      resolvedTheme,
      accent,
      ambient,
      setMode,
      setAccent,
      setAmbient,
      cycleMode,
    }),
    [mode, resolvedTheme, accent, ambient, setMode, setAccent, setAmbient, cycleMode],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) {
    return {
      mode: 'dark',
      resolvedTheme: 'dark',
      accent: 'violet',
      ambient: 'on',
      setMode: () => {},
      setAccent: () => {},
      setAmbient: () => {},
      cycleMode: () => {},
    }
  }
  return ctx
}

import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider, initThemeDom, useTheme } from '../ThemeContext'

function ThemeHarness() {
  const { mode, resolvedTheme, accent, ambient, setMode, setAccent, setAmbient, cycleMode } = useTheme()
  return (
    <div>
      <span data-testid="mode">{mode}</span>
      <span data-testid="resolved">{resolvedTheme}</span>
      <span data-testid="accent">{accent}</span>
      <span data-testid="ambient">{ambient}</span>
      <button type="button" onClick={() => setMode('light')}>Light</button>
      <button type="button" onClick={() => setMode('carbon')}>Carbon</button>
      <button type="button" onClick={() => setMode('system')}>System</button>
      <button type="button" onClick={() => setAccent('sapphire')}>Sapphire</button>
      <button type="button" onClick={() => setAccent('emerald')}>Emerald</button>
      <button type="button" onClick={() => setAccent('amber')}>Amber</button>
      <button type="button" onClick={() => setAccent('mono')}>Mono</button>
      <button type="button" onClick={() => setAmbient('off')}>Ambient Off</button>
      <button type="button" onClick={cycleMode}>Cycle</button>
    </div>
  )
}

describe('ThemeContext & initThemeDom', () => {
  beforeEach(() => {
    window.localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.removeAttribute('data-theme-preference')
    document.documentElement.removeAttribute('data-accent')
    document.documentElement.removeAttribute('data-ambient')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('applies default dark/violet/on attributes to documentElement and persists changes across all modes, accents, and ambient', async () => {
    const user = userEvent.setup()
    initThemeDom()
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(document.documentElement.getAttribute('data-accent')).toBe('violet')
    expect(document.documentElement.getAttribute('data-ambient')).toBe('on')

    render(
      <ThemeProvider>
        <ThemeHarness />
      </ThemeProvider>,
    )

    await user.click(screen.getByRole('button', { name: 'Light' }))
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(window.localStorage.getItem('orin.theme.mode')).toBe('light')

    await user.click(screen.getByRole('button', { name: 'Carbon' }))
    expect(document.documentElement.getAttribute('data-theme')).toBe('carbon')
    expect(window.localStorage.getItem('orin.theme.mode')).toBe('carbon')

    for (const [btnName, accentVal] of [
      ['Sapphire', 'sapphire'],
      ['Emerald', 'emerald'],
      ['Amber', 'amber'],
      ['Mono', 'mono'],
    ] as const) {
      await user.click(screen.getByRole('button', { name: btnName }))
      expect(document.documentElement.getAttribute('data-accent')).toBe(accentVal)
      expect(window.localStorage.getItem('orin.theme.accent')).toBe(accentVal)
    }

    await user.click(screen.getByRole('button', { name: 'Ambient Off' }))
    expect(document.documentElement.getAttribute('data-ambient')).toBe('off')
    expect(window.localStorage.getItem('orin.theme.ambient')).toBe('off')
  })

  it('cycles through dark -> light -> carbon -> dark and falls back safely on invalid storage values', async () => {
    const user = userEvent.setup()
    window.localStorage.setItem('orin.theme.mode', 'invalid-mode')
    window.localStorage.setItem('orin.theme.accent', 'invalid-accent')
    window.localStorage.setItem('orin.theme.ambient', 'invalid-ambient')

    render(
      <ThemeProvider>
        <ThemeHarness />
      </ThemeProvider>,
    )

    expect(screen.getByTestId('mode')).toHaveTextContent('dark')
    expect(screen.getByTestId('accent')).toHaveTextContent('violet')
    expect(screen.getByTestId('ambient')).toHaveTextContent('on')

    await user.click(screen.getByRole('button', { name: 'Cycle' }))
    expect(screen.getByTestId('mode')).toHaveTextContent('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')

    await user.click(screen.getByRole('button', { name: 'Cycle' }))
    expect(screen.getByTestId('mode')).toHaveTextContent('carbon')
    expect(document.documentElement.getAttribute('data-theme')).toBe('carbon')

    await user.click(screen.getByRole('button', { name: 'Cycle' }))
    expect(screen.getByTestId('mode')).toHaveTextContent('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })

  it('handles localStorage throwing SecurityError or QuotaExceededError gracefully without crashing', async () => {
    const user = userEvent.setup()
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Access is denied', 'SecurityError')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Quota exceeded', 'QuotaExceededError')
    })

    expect(() => initThemeDom()).not.toThrow()
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')

    render(
      <ThemeProvider>
        <ThemeHarness />
      </ThemeProvider>,
    )

    await user.click(screen.getByRole('button', { name: 'Light' }))
    expect(screen.getByTestId('mode')).toHaveTextContent('light')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })

  it('reacts to system prefers-color-scheme matchMedia changes when mode is system', async () => {
    const user = userEvent.setup()
    let matchesLight = false
    const listeners = new Set<() => void>()
    const originalMatchMedia = window.matchMedia

    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        get matches() {
          return query.includes('light') ? matchesLight : !matchesLight
        },
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: (_event: string, cb: unknown) => {
          if (typeof cb === 'function') listeners.add(cb as () => void)
        },
        removeEventListener: (_event: string, cb: unknown) => {
          if (typeof cb === 'function') listeners.delete(cb as () => void)
        },
        dispatchEvent: vi.fn(),
      })),
    })

    try {
      render(
        <ThemeProvider>
          <ThemeHarness />
        </ThemeProvider>,
      )

      await user.click(screen.getByRole('button', { name: 'System' }))
      expect(screen.getByTestId('mode')).toHaveTextContent('system')
      expect(screen.getByTestId('resolved')).toHaveTextContent('dark')
      expect(document.documentElement.getAttribute('data-theme')).toBe('dark')

      act(() => {
        matchesLight = true
        listeners.forEach((cb) => cb())
      })

      expect(screen.getByTestId('resolved')).toHaveTextContent('light')
      expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    } finally {
      Object.defineProperty(window, 'matchMedia', {
        writable: true,
        configurable: true,
        value: originalMatchMedia,
      })
    }
  })

  it('returns safe default fallback when useTheme is called outside ThemeProvider', () => {
    render(<ThemeHarness />)
    expect(screen.getByTestId('mode')).toHaveTextContent('dark')
    expect(screen.getByTestId('resolved')).toHaveTextContent('dark')
    expect(screen.getByTestId('accent')).toHaveTextContent('violet')
    expect(screen.getByTestId('ambient')).toHaveTextContent('on')
  })
})

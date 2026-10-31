import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider, initThemeDom, useTheme } from '../ThemeContext'
import themeCss from '../../styles/theme.css?raw'
import liveCss from '../../styles/live.css?raw'
import agentosCss from '../../styles/agentos.css?raw'
import indexCss from '../../styles/index.css?raw'

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
      <button type="button" onClick={() => setMode('midnight')}>Midnight</button>
      <button type="button" onClick={() => setMode('forest')}>Forest</button>
      <button type="button" onClick={() => setMode('system')}>System</button>
      <button type="button" onClick={() => setAccent('sapphire')}>Sapphire</button>
      <button type="button" onClick={() => setAccent('emerald')}>Emerald</button>
      <button type="button" onClick={() => setAccent('amber')}>Amber</button>
      <button type="button" onClick={() => setAccent('rose')}>Rose</button>
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
    document.documentElement.removeAttribute('data-desktop')
    delete (window as { orinDesktop?: unknown }).orinDesktop
  })

  afterEach(() => {
    delete (window as { orinDesktop?: unknown }).orinDesktop
    vi.restoreAllMocks()
  })

  it('applies default dark/violet/on attributes to documentElement and persists changes across all modes, accents, and ambient', async () => {
    const user = userEvent.setup()
    initThemeDom()
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(document.documentElement.getAttribute('data-accent')).toBe('violet')
    expect(document.documentElement.getAttribute('data-ambient')).toBe('on')
    expect(document.documentElement.getAttribute('data-desktop')).toBe('false')

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

    await user.click(screen.getByRole('button', { name: 'Midnight' }))
    expect(document.documentElement.getAttribute('data-theme')).toBe('midnight')
    expect(window.localStorage.getItem('orin.theme.mode')).toBe('midnight')

    await user.click(screen.getByRole('button', { name: 'Forest' }))
    expect(document.documentElement.getAttribute('data-theme')).toBe('forest')
    expect(window.localStorage.getItem('orin.theme.mode')).toBe('forest')

    for (const [btnName, accentVal] of [
      ['Sapphire', 'sapphire'],
      ['Emerald', 'emerald'],
      ['Amber', 'amber'],
      ['Rose', 'rose'],
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

  it('sets data-desktop synchronously via initThemeDom when window.orinDesktop is present and preserves pre-set data-desktop', () => {
    ;(window as { orinDesktop?: { isDesktop?: boolean } }).orinDesktop = { isDesktop: true }
    initThemeDom()
    expect(document.documentElement.getAttribute('data-desktop')).toBe('true')

    delete (window as { orinDesktop?: unknown }).orinDesktop
    document.documentElement.setAttribute('data-desktop', 'true')
    initThemeDom()
    expect(document.documentElement.getAttribute('data-desktop')).toBe('true')

    render(
      <ThemeProvider>
        <ThemeHarness />
      </ThemeProvider>,
    )
    expect(document.documentElement.getAttribute('data-desktop')).toBe('true')
  })

  it('defines canonical reference tokens (#101014, #141419, #1A1A21, #1E1E26, #B4A4EF, --orin-accent-ink) and custom theme blocks in theme.css', () => {
    expect(themeCss).toMatch(/--orin-page-bg:\s*#101014/i)
    expect(themeCss).toMatch(/--orin-surface-1:\s*#141419/i)
    expect(themeCss).toMatch(/--orin-surface-2:\s*#1A1A21/i)
    expect(themeCss).toMatch(/--orin-surface-3:\s*#1E1E26/i)
    expect(themeCss).toMatch(/--orin-accent:\s*#B4A4EF/i)
    expect(themeCss).toMatch(/--orin-accent-ink:\s*#181523/i)
    expect(themeCss).toContain('[data-theme="midnight"]')
    expect(themeCss).toContain('[data-theme="forest"]')
    expect(themeCss).toContain('[data-accent="rose"]')
    expect(themeCss).toContain('--orin-titlebar-height')
    expect(themeCss).toContain('--orin-titlebar-right-inset')
    expect(themeCss).toContain('html[data-desktop="true"]')
  })

  it('defines Electron window controls clearance across live.css, agentos.css, and index.css topbars', () => {
    expect(liveCss).toContain('html[data-desktop="true"] .live-stage__top')
    expect(liveCss).toContain('html[data-desktop="true"] .friend-drawer__header')
    expect(liveCss).toContain('html[data-desktop="true"] .friend-view__header')
    expect(agentosCss).toContain('html[data-desktop="true"] .chat__head')
    expect(agentosCss).toContain('html[data-desktop="true"] .settings-shell__bar')
    expect(agentosCss).toContain('html[data-desktop="true"] .rewind-time-machine__topbar')
    expect(indexCss).toContain('html[data-desktop="true"] .update-banner')
    expect(indexCss).toContain('html[data-desktop="true"] .topbar')
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

  it('scopes [data-theme="light"] .orin-mascot with luminous pastel tokens and overrides for all 6 light-mode accents', () => {
    expect(themeCss).toContain('[data-theme="light"] .orin-mascot')
    expect(themeCss).toContain('[data-theme="light"][data-accent="violet"] .orin-mascot')
    expect(themeCss).toContain(':root[data-theme="system"] .orin-mascot')
    expect(themeCss).toMatch(/--orin-accent-soft:\s*#E4DCFF/i)
    expect(themeCss).toMatch(/--orin-accent-strong:\s*#8870DF/i)
    expect(themeCss).toMatch(/--orin-mascot-shadow:\s*rgba\(28,\s*22,\s*48,\s*0\.12\)/i)
    expect(themeCss).toMatch(/--orin-mascot-feature:\s*#282333/i)

    for (const accent of ['violet', 'sapphire', 'emerald', 'amber', 'rose', 'mono'] as const) {
      expect(themeCss).toContain(`[data-theme="light"][data-accent="${accent}"] .orin-mascot`)
    }
    for (const accent of ['sapphire', 'emerald', 'amber', 'rose', 'mono'] as const) {
      expect(themeCss).toContain(`:root[data-theme="system"][data-accent="${accent}"] .orin-mascot`)
    }

    expect(themeCss).toMatch(/--orin-accent-soft:\s*#DCE8FF;\s*--orin-accent:\s*#82A8F8;\s*--orin-accent-strong:\s*#547EEB;/i)
    expect(themeCss).toMatch(/--orin-accent-soft:\s*#D1FAE5;\s*--orin-accent:\s*#5CE0B2;\s*--orin-accent-strong:\s*#22B886;/i)
    expect(themeCss).toMatch(/--orin-accent-soft:\s*#FEF3C7;\s*--orin-accent:\s*#FCD34D;\s*--orin-accent-strong:\s*#F59E0B;/i)
    expect(themeCss).toMatch(/--orin-accent-soft:\s*#FCE0E8;\s*--orin-accent:\s*#F4A2B8;\s*--orin-accent-strong:\s*#E47293;/i)
    expect(themeCss).toMatch(/--orin-accent-soft:\s*#F4F4F6;\s*--orin-accent:\s*#D4D4D8;\s*--orin-accent-strong:\s*#A1A1AA;/i)

    const hexLuminance = (hex: string): number => {
      const clean = hex.replace('#', '')
      const [r, g, b] = [0, 2, 4].map((offset) => {
        const channel = Number.parseInt(clean.slice(offset, offset + 2), 16) / 255
        return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
      })
      return 0.2126 * r + 0.7152 * g + 0.0722 * b
    }
    const contrastRatio = (hexA: string, hexB: string): number => {
      const lumA = hexLuminance(hexA)
      const lumB = hexLuminance(hexB)
      const brightest = Math.max(lumA, lumB)
      const darkest = Math.min(lumA, lumB)
      return (brightest + 0.05) / (darkest + 0.05)
    }

    const lightMascotPalettes = [
      { accent: 'violet', soft: '#E4DCFF', mid: '#B4A4EF', strong: '#8870DF' },
      { accent: 'sapphire', soft: '#DCE8FF', mid: '#82A8F8', strong: '#547EEB' },
      { accent: 'emerald', soft: '#D1FAE5', mid: '#5CE0B2', strong: '#22B886' },
      { accent: 'amber', soft: '#FEF3C7', mid: '#FCD34D', strong: '#F59E0B' },
      { accent: 'rose', soft: '#FCE0E8', mid: '#F4A2B8', strong: '#E47293' },
      { accent: 'mono', soft: '#F4F4F6', mid: '#D4D4D8', strong: '#A1A1AA' },
    ] as const

    for (const p of lightMascotPalettes) {
      expect(hexLuminance(p.soft)).toBeGreaterThan(hexLuminance(p.mid))
      expect(hexLuminance(p.mid)).toBeGreaterThan(hexLuminance(p.strong))
      expect(contrastRatio(p.mid, '#282333')).toBeGreaterThan(4.5)
    }

    const strippedCss = themeCss.replace(/\/\*[\s\S]*?\*\//g, '')
    const openBraces = (strippedCss.match(/\{/g) ?? []).length
    const closeBraces = (strippedCss.match(/\}/g) ?? []).length
    expect(openBraces).toBe(closeBraces)
  })
})

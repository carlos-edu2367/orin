import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { Home } from '../../src/app/Home'
import { ThemeProvider, useTheme } from '../../src/app/ThemeContext'
import { routes } from '../../src/app/routes'
import { OrinMascot } from '../../src/components/OrinMascot'
import { loadLiveVoicePrefs } from '../../src/features/conversations/live/liveVoicePrefs'
import { PreferencesModal } from '../../src/features/settings/PreferencesModal'
import { RuntimeSettingsPage } from '../../src/features/settings/RuntimeSettingsPage'
import { SettingsNav } from '../../src/features/settings/SettingsNav'
import { VoiceSettingsSection } from '../../src/features/settings/VoiceSettingsSection'
import agentosCss from '../../src/styles/agentos.css?raw'

function MotionProbe() {
  const { reduceMotion, setReduceMotion } = useTheme()
  return (
    <button
      type="button"
      data-testid="motion-probe"
      onClick={() => setReduceMotion(!reduceMotion)}
    >
      {reduceMotion ? 'reduced' : 'full'}
    </button>
  )
}

function createMockClient(): ApiClient {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
    if (url.includes('/api/runtime/settings')) {
      return new Response(JSON.stringify({ max_iterations: 24 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.includes('/api/code-mode/settings')) {
      return new Response(
        JSON.stringify({ autonomy: 'approval_required', system_notifications: false, monitoring_enabled: true }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (url.includes('/api/installation')) {
      return new Response(
        JSON.stringify({
          installation_kind: 'development',
          current_version: '0.1.12',
          installed_versions: [],
          latest_release: null,
          latest_release_error: null,
          checked_at: '2026-10-30T00:00:00Z',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }
    return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  return new ApiClient({ fetchImpl, createIdempotencyKey: () => 'test-key' })
}

describe('Phase 7 Task 1: SettingsNav, VoiceSettingsSection, and Reduce Motion', () => {
  beforeEach(() => {
    window.localStorage.clear()
    document.documentElement.removeAttribute('data-reduce-motion')
    document.body.classList.remove('reduce-motion')
  })

  describe('ThemeContext reduceMotion', () => {
    it('defaults to false and toggles data-reduce-motion and body.reduce-motion class', () => {
      render(
        <ThemeProvider>
          <MotionProbe />
        </ThemeProvider>,
      )

      const probe = screen.getByTestId('motion-probe')
      expect(probe).toHaveTextContent('full')
      expect(document.documentElement.getAttribute('data-reduce-motion')).toBe('false')
      expect(document.body.classList.contains('reduce-motion')).toBe(false)

      fireEvent.click(probe)
      expect(probe).toHaveTextContent('reduced')
      expect(window.localStorage.getItem('orin.theme.reduceMotion')).toBe('true')
      expect(document.documentElement.getAttribute('data-reduce-motion')).toBe('true')
      expect(document.body.classList.contains('reduce-motion')).toBe(true)

      fireEvent.click(probe)
      expect(probe).toHaveTextContent('full')
      expect(window.localStorage.getItem('orin.theme.reduceMotion')).toBe('false')
      expect(document.documentElement.getAttribute('data-reduce-motion')).toBe('false')
      expect(document.body.classList.contains('reduce-motion')).toBe(false)
    })

    it('survives localStorage SecurityError / QuotaExceededError without crashing', () => {
      const getItemSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
        throw new DOMException('Blocked', 'SecurityError')
      })
      const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new DOMException('Quota', 'QuotaExceededError')
      })

      try {
        render(
          <ThemeProvider>
            <MotionProbe />
          </ThemeProvider>,
        )
        const probe = screen.getByTestId('motion-probe')
        expect(probe).toHaveTextContent('full')
        fireEvent.click(probe)
        expect(probe).toHaveTextContent('reduced')
        expect(document.documentElement.getAttribute('data-reduce-motion')).toBe('true')
      } finally {
        getItemSpy.mockRestore()
        setItemSpy.mockRestore()
      }
    })
  })

  describe('SettingsNav 6 human primary categories', () => {
    it('renders the 6 primary categories at the top of SettingsNav and highlights matching routes', () => {
      const { unmount } = render(
        <MemoryRouter initialEntries={['/settings/security']}>
          <SettingsNav badges={{}} />
        </MemoryRouter>,
      )

      const primaryGroup = screen.getByTestId('settings-primary-categories')
      expect(primaryGroup).toHaveAttribute('role', 'group')
      expect(primaryGroup).toHaveAttribute('aria-label', 'Categorias principais')

      const links = within(primaryGroup).getAllByRole('link')
      expect(links.map((link) => link.textContent?.trim())).toEqual([
        'Geral',
        'Modelos e provedores',
        'Voz',
        'Memória',
        'Segurança',
        'Atualizações',
      ])

      const securityLink = within(primaryGroup).getByRole('link', { name: 'Segurança' })
      expect(securityLink).toHaveAttribute('aria-current', 'page')
      expect(securityLink.className).toContain('is-active')

      const generalLink = within(primaryGroup).getByRole('link', { name: 'Geral' })
      expect(generalLink).not.toHaveAttribute('aria-current')

      unmount()

      render(
        <MemoryRouter initialEntries={['/settings/voice']}>
          <SettingsNav badges={{}} />
        </MemoryRouter>,
      )
      expect(
        within(screen.getByTestId('settings-primary-categories')).getByRole('link', { name: 'Voz' }),
      ).toHaveAttribute('aria-current', 'page')
    })
  })

  describe('VoiceSettingsSection & routes', () => {
    it('registers /settings/voice and /settings/security in routes', () => {
      const paths = new Set(routes.map((r) => r.path))
      expect(paths.has('/settings/voice')).toBe(true)
      expect(paths.has('/settings/security')).toBe(true)
    })

    it('renders inline voice preferences without opening a dialog and persists changes', () => {
      render(
        <MemoryRouter initialEntries={['/settings/voice']}>
          <VoiceSettingsSection />
        </MemoryRouter>,
      )

      expect(screen.queryByRole('dialog')).toBeNull()
      expect(screen.getByRole('heading', { level: 1, name: 'Voz' })).toBeInTheDocument()
      const section = screen.getByTestId('settings-voice-section')
      expect(section).toBeInTheDocument()

      const sttSelect = screen.getByLabelText(/Reconhecimento de fala/i)
      fireEvent.change(sttSelect, { target: { value: 'local_sidecar' } })
      expect(loadLiveVoicePrefs().stt).toBe('local_sidecar')
      expect(screen.getByText('✓ Preferências de voz salvas')).toBeInTheDocument()

      const ttsSelect = screen.getByLabelText(/Síntese de voz/i)
      fireEvent.change(ttsSelect, { target: { value: 'kokoro' } })
      expect(loadLiveVoicePrefs().tts).toBe('kokoro')

      const rateSlider = screen.getByLabelText(/Velocidade da fala/i)
      fireEvent.change(rateSlider, { target: { value: '1.25' } })
      expect(loadLiveVoicePrefs().rate).toBe(1.25)

      const bargeInCheckbox = screen.getByRole('checkbox', { name: /Interromper ao falar \(Barge-in\)/i })
      expect(bargeInCheckbox).toBeChecked()
      fireEvent.click(bargeInCheckbox)
      expect(loadLiveVoicePrefs().bargeIn).toBe(false)

      const wakeWordCheckbox = screen.getByRole('checkbox', { name: /Ativar dizendo "Orin"/i })
      expect(wakeWordCheckbox).toBeChecked()
      fireEvent.click(wakeWordCheckbox)
      expect(loadLiveVoicePrefs().wakeWordEnabled).toBe(false)
    })
  })

  describe('PreferencesModal and RuntimeSettingsPage reduce motion toggles', () => {
    it('toggles reduceMotion from PreferencesModal appearance tab and RuntimeSettingsPage', () => {
      const mockClient = createMockClient()
      render(
        <MemoryRouter initialEntries={['/settings/general']}>
          <ThemeProvider>
            <PreferencesModal open initialTab="appearance" onClose={() => {}} client={mockClient} />
            <RuntimeSettingsPage embedded client={mockClient} />
          </ThemeProvider>
        </MemoryRouter>,
      )

      const modalToggle = screen.getByTestId('theme-reduce-motion-toggle')
      expect(modalToggle).toHaveAttribute('role', 'switch')
      expect(modalToggle).toHaveAttribute('aria-checked', 'false')

      fireEvent.click(modalToggle)
      expect(modalToggle).toHaveAttribute('aria-checked', 'true')
      expect(document.documentElement.getAttribute('data-reduce-motion')).toBe('true')
      expect(document.body.classList.contains('reduce-motion')).toBe(true)

      const generalToggle = screen.getByTestId('general-reduce-motion-toggle') as HTMLInputElement
      expect(generalToggle.checked).toBe(true)

      fireEvent.click(generalToggle)
      expect(generalToggle.checked).toBe(false)
      expect(document.documentElement.getAttribute('data-reduce-motion')).toBe('false')
      expect(document.body.classList.contains('reduce-motion')).toBe(false)
    })
  })

  describe('Phase 7 Task 4: OrinMascot particles + celebration & Home interactive choreography', () => {
    it('renders 9 radial particles when showParticles or celebrating is true and sets data-celebrating', () => {
      const { container, rerender } = render(<OrinMascot size={88} variant="soft3d" />)
      const mascot = screen.getByTestId('orin-mascot')
      expect(mascot).toHaveAttribute('data-celebrating', 'false')
      expect(mascot).not.toHaveClass('is-celebrating')
      expect(screen.queryByTestId('orin-mascot-particles')).toBeNull()

      rerender(<OrinMascot size={88} variant="soft3d" showParticles />)
      expect(mascot).toHaveAttribute('data-celebrating', 'false')
      const particles = screen.getByTestId('orin-mascot-particles')
      const dots = container.querySelectorAll('.orin-mascot__particle')
      expect(particles).toBeInTheDocument()
      expect(dots).toHaveLength(9)
      expect((dots[0] as HTMLElement).style.getPropertyValue('--dx')).toBe('-58px')
      expect((dots[0] as HTMLElement).style.getPropertyValue('--dy')).toBe('-38px')
      expect((dots[8] as HTMLElement).style.getPropertyValue('--dx')).toBe('22px')
      expect((dots[8] as HTMLElement).style.getPropertyValue('--dy')).toBe('62px')

      rerender(<OrinMascot size={88} variant="soft3d" celebrating />)
      expect(mascot).toHaveAttribute('data-celebrating', 'true')
      expect(mascot).toHaveClass('is-celebrating')
      expect(screen.getByTestId('orin-mascot-particles')).toBeInTheDocument()
      expect(container.querySelectorAll('.orin-mascot__particle')).toHaveLength(9)
    })

    it('triggers celebration in Home when clicking hero mascot or quick chip and cleans up timer on unmount', async () => {
      vi.useFakeTimers()
      const clearTimeoutSpy = vi.spyOn(window, 'clearTimeout')
      const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url
        if (url.includes('/models')) {
          return new Response(
            JSON.stringify({
              items: [
                {
                  provider: 'openrouter',
                  model_id: 'anthropic/model-a',
                  display_name: 'Model A',
                  context_window: 128000,
                  capabilities: [],
                  input_modalities: ['text'],
                  output_modalities: ['text'],
                  pricing: null,
                  is_favorite: false,
                  refreshed_at: null,
                },
              ],
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          )
        }
        return new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      })
      const client = new ApiClient({ fetchImpl, maxAttempts: 1 })

      try {
        const { unmount } = render(
          <ThemeProvider>
            <MemoryRouter initialEntries={['/']}>
              <Home client={client} bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }} />
            </MemoryRouter>
          </ThemeProvider>,
        )

        const heroMascotWrap = screen.getByTestId('home-hero-mascot')
        const heroMascot = within(heroMascotWrap).getByTestId('orin-mascot')
        const header = screen.getByTestId('canvas-header')
        const topbarMascot = within(header).getByTestId('orin-mascot')

        expect(heroMascotWrap).toHaveAttribute('data-celebrating', 'false')
        expect(heroMascot).toHaveAttribute('data-celebrating', 'false')
        expect(within(heroMascotWrap).getByTestId('orin-mascot-particles')).toBeInTheDocument()

        // 1. Click hero mascot -> celebrates for 950ms
        fireEvent.click(heroMascotWrap)
        expect(heroMascotWrap).toHaveAttribute('data-celebrating', 'true')
        expect(heroMascot).toHaveAttribute('data-celebrating', 'true')
        expect(topbarMascot).toHaveAttribute('data-celebrating', 'true')

        act(() => {
          vi.advanceTimersByTime(950)
        })
        expect(heroMascotWrap).toHaveAttribute('data-celebrating', 'false')
        expect(heroMascot).toHaveAttribute('data-celebrating', 'false')
        expect(topbarMascot).toHaveAttribute('data-celebrating', 'false')

        // 2. Click a quick chip -> celebrates and resets previous timer cleanly
        const quickActions = screen.getByTestId('home-quick-actions')
        const firstChip = within(quickActions).getAllByRole('button')[0]
        fireEvent.click(firstChip)
        expect(heroMascotWrap).toHaveAttribute('data-celebrating', 'true')

        // Unmount while timer is active -> clears timeout without leaking
        unmount()
        expect(clearTimeoutSpy).toHaveBeenCalled()
      } finally {
        clearTimeoutSpy.mockRestore()
        vi.useRealTimers()
      }
    })

    it('defines Phase 7 motion choreography, ref-14/ref-15 grids, and reduced-motion rules in agentos.css', () => {
      expect(agentosCss).toContain('--orin-ease-spring: cubic-bezier(.23, 1, .32, 1)')
      expect(agentosCss).toContain('@keyframes orin-floaty')
      expect(agentosCss).toContain('@keyframes orin-blink')
      expect(agentosCss).toContain('@keyframes orin-hero-celebrate')
      expect(agentosCss).toContain('@keyframes orin-little-celebrate')
      expect(agentosCss).toContain('@keyframes orin-particle-pop')
      expect(agentosCss).toContain('.providers-ref14__grid')
      expect(agentosCss).toContain('.security-ref15__grid')
      expect(agentosCss).toContain('body.reduce-motion')
    })
  })
})

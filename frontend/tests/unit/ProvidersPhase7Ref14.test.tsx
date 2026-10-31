import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { LOCAL_CAPABILITIES } from '../../src/api/session'
import { SessionContext } from '../../src/app/useSession'
import { ProviderDetail } from '../../src/features/providers/ProviderDetail'
import { ProvidersSection } from '../../src/features/providers/ProvidersSection'

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location-probe">{location.pathname}</output>
}

function createMockClient(
  modelsByProvider: Record<string, Array<{ model_id: string; display_name: string }>> = {},
  enabledProviders: string[] = ['openai', 'openrouter'],
) {
  const fetchImpl = vi.fn<typeof fetch>((input) => {
    const url = String(input)
    if (url.endsWith('/settings/vision-model')) {
      return Promise.resolve(
        new Response(JSON.stringify({ mode: 'automatic', provider: null, model_id: null }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
    if (url.endsWith('/settings/media-models')) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            image: { mode: 'automatic', provider: null, model_id: null },
            audio: { mode: 'automatic', provider: null, model_id: null },
            stt: { mode: 'automatic', provider: null, model_id: null },
            video: { mode: 'automatic', provider: null, model_id: null },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    }
    const modelsMatch = /\/v1\/providers\/([^/]+)\/models$/.exec(url)
    if (modelsMatch) {
      const provider = modelsMatch[1]
      const list = modelsByProvider[provider] ?? []
      return Promise.resolve(
        new Response(
          JSON.stringify({
            items: list.map((item) => ({
              provider,
              model_id: item.model_id,
              display_name: item.display_name,
              context_window: 128000,
              capabilities: [],
              input_modalities: ['text'],
              output_modalities: ['text'],
              pricing: null,
              is_favorite: false,
              is_custom: false,
              refreshed_at: null,
              route_kind: 'model',
            })),
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    }
    const providerMatch = /\/v1\/providers\/([^/]+)$/.exec(url)
    if (providerMatch) {
      const provider = providerMatch[1]
      const enabled = enabledProviders.includes(provider)
      return Promise.resolve(
        new Response(JSON.stringify({ provider, enabled }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }
    return Promise.resolve(new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  })

  return new ApiClient({
    fetchImpl,
    csrfToken: 'csrf-test',
    maxAttempts: 1,
    createIdempotencyKey: () => 'intent-test',
  })
}

function renderProvidersSection(options: { omniroute?: boolean; client?: ApiClient; initialPath?: string } = {}) {
  const client = options.client ?? createMockClient()
  return render(
    <SessionContext.Provider
      value={{
        mode: 'local',
        user: null,
        capabilities: { ...LOCAL_CAPABILITIES, omniroute: options.omniroute ?? true },
        isAdmin: false,
        refresh: async () => undefined,
        signOut: async () => undefined,
      }}
    >
      <MemoryRouter initialEntries={[options.initialPath ?? '/settings/providers']}>
        <ProvidersSection client={client} />
        <LocationProbe />
      </MemoryRouter>
    </SessionContext.Provider>,
  )
}

describe('ProvidersSection Phase 7 (ref-14.png: Modelos e provedores)', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('preserves the single h1 from SettingsSection and renders the ref-14 h2 editorial header', () => {
    renderProvidersSection()

    const h1s = screen.getAllByRole('heading', { level: 1 })
    expect(h1s).toHaveLength(1)

    const heading = screen.getByTestId('providers-ref14-heading')
    expect(heading.tagName).toBe('H2')
    expect(heading).toHaveTextContent('Modelos e provedores')
    expect(screen.getByText('Escolha a IA que trabalha com você.')).toBeInTheDocument()
  })

  it('lists only genuinely configured providers in Provedores conectados and shows empty state when none are configured', async () => {
    const user = userEvent.setup()
    const { unmount } = renderProvidersSection({ client: createMockClient({}, []) })

    const emptyCard = screen.getByTestId('providers-connected-card')
    expect(within(emptyCard).getByTestId('providers-connected-empty')).toHaveTextContent(
      'Nenhum provedor conectado ainda. Conecte um provedor abaixo para começar.',
    )
    expect(within(emptyCard).queryByText('OpenAI')).not.toBeInTheDocument()
    expect(within(emptyCard).queryByText(/Ollama/)).not.toBeInTheDocument()
    unmount()

    renderProvidersSection()

    const openAiLinks = screen.getAllByRole('link', { name: /OpenAI/ })
    expect(openAiLinks).toHaveLength(1)
    expect(openAiLinks[0]).toHaveClass('provider-card')

    const connectedCard = screen.getByTestId('providers-connected-card')
    expect(within(connectedCard).getByRole('heading', { level: 3, name: 'Provedores conectados' })).toBeInTheDocument()
    expect(await within(connectedCard).findByText('OpenAI')).toBeInTheDocument()
    expect(within(connectedCard).getByText('OpenRouter')).toBeInTheDocument()
    expect(within(connectedCard).queryByText(/Ollama/)).not.toBeInTheDocument()

    const manageOpenAiBtn = screen.getByTestId('providers-manage-openai')
    expect(manageOpenAiBtn.tagName).toBe('BUTTON')
    expect(manageOpenAiBtn).toHaveTextContent(/Gerenciar/)

    await user.click(manageOpenAiBtn)
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/settings/providers/openai')
  })

  it('renders Card 1 (Modelo principal), switches provider and model options, and loads dynamic models from API', async () => {
    const user = userEvent.setup()
    const client = createMockClient({
      anthropic: [{ model_id: 'claude-opus-4-1', display_name: 'Claude Opus 4.1 API' }],
    })
    renderProvidersSection({ client, omniroute: true })

    const primaryCard = screen.getByTestId('providers-primary-card')
    expect(within(primaryCard).getByRole('heading', { level: 3, name: 'Modelo principal' })).toBeInTheDocument()
    expect(within(primaryCard).getByText('Usado por padrão nas novas conversas.')).toBeInTheDocument()

    const providerSelect = screen.getByTestId('providers-primary-provider-select') as HTMLSelectElement
    const modelSelect = screen.getByTestId('providers-primary-model-select') as HTMLSelectElement

    expect(within(primaryCard).getByLabelText('Provedor')).toBe(providerSelect)
    expect(within(primaryCard).getByLabelText('Modelo')).toBe(modelSelect)
    expect(providerSelect.value).toBe('openai')
    expect(modelSelect.value).toBe('gpt-5.4')

    await user.selectOptions(providerSelect, 'anthropic')
    expect(providerSelect.value).toBe('anthropic')
    expect(modelSelect.value).toBe('claude-sonnet-4-5')
    expect(await within(modelSelect).findByRole('option', { name: 'Claude Opus 4.1 API' })).toBeInTheDocument()

    await user.selectOptions(providerSelect, 'google')
    expect(modelSelect.value).toBe('gemini-2.5-pro')

    await user.selectOptions(providerSelect, 'groq')
    expect(modelSelect.value).toBe('llama-3.3-70b-versatile')
  })

  it('renders Card 2 (Se houver uma falha), Card Uso e custos, and persists settings on Salvar alterações', async () => {
    const user = userEvent.setup()
    renderProvidersSection()

    const fallbackCard = screen.getByTestId('providers-fallback-card')
    expect(within(fallbackCard).getByRole('heading', { level: 3, name: 'Se houver uma falha' })).toBeInTheDocument()

    const fallbackSelect = screen.getByTestId('providers-fallback-select') as HTMLSelectElement
    expect(within(fallbackCard).getByLabelText('Provedor e modelo reserva')).toBe(fallbackSelect)
    expect(fallbackSelect.value).toBe('ollama:local')

    const fallbackAutoCheckbox = screen.getByTestId('providers-fallback-auto-checkbox') as HTMLInputElement
    expect(fallbackAutoCheckbox.checked).toBe(true)

    const costCard = screen.getByTestId('providers-cost-card')
    expect(within(costCard).getByRole('heading', { level: 3, name: 'Uso e custos' })).toBeInTheDocument()
    expect(within(costCard).getByText('O envio a provedores externos depende da conexão escolhida.')).toBeInTheDocument()

    const costCheckbox = screen.getByTestId('providers-cost-estimate-checkbox') as HTMLInputElement
    expect(costCheckbox.checked).toBe(true)

    await user.selectOptions(screen.getByTestId('providers-primary-provider-select'), 'openrouter')
    await user.selectOptions(fallbackSelect, 'anthropic:claude-sonnet-4-5')
    await user.click(fallbackAutoCheckbox)
    await user.click(costCheckbox)

    const saveBtn = screen.getByTestId('providers-save-btn')
    expect(saveBtn).toHaveTextContent('Salvar alterações')
    await user.click(saveBtn)

    const notice = screen.getByTestId('providers-saved-notice')
    expect(notice).toHaveAttribute('role', 'status')
    expect(notice).toHaveTextContent('Alterações salvas.')

    expect(window.localStorage.getItem('agentos.provider')).toBe('openrouter')
    expect(window.localStorage.getItem('agentos.model')).toBe('openrouter/auto')
    expect(window.localStorage.getItem('orin.providers.fallback')).toBe('anthropic:claude-sonnet-4-5')
    expect(window.localStorage.getItem('orin.providers.fallbackAuto')).toBe('false')
    expect(window.localStorage.getItem('orin.providers.showCostEstimate')).toBe('false')
  })

  it('constrains .slide-over-drawer, .settings-drawer, .ollama-flow, and .ollama-flow__modes against horizontal overflow in CSS and renders AnthropicSetup inside ProviderDetail cleanly', async () => {
    const user = userEvent.setup()
    const agentosCss = readFileSync(resolve(__dirname, '../../src/styles/agentos.css'), 'utf8')
    const indexCss = readFileSync(resolve(__dirname, '../../src/styles/index.css'), 'utf8')

    // 1. Verify .slide-over-drawer width, overflow-x, box-sizing, and desktop top padding
    expect(agentosCss).toMatch(/\.slide-over-drawer\s*\{[^}]*width:\s*min\(560px,\s*100vw\)/s)
    expect(agentosCss).toMatch(/\.slide-over-drawer\s*\{[^}]*overflow-x:\s*hidden/s)
    expect(agentosCss).toMatch(/\.slide-over-drawer\s*\{[^}]*box-sizing:\s*border-box/s)
    expect(agentosCss).toMatch(
      /html\[data-desktop="true"\]\s+\.slide-over-drawer\s*\{[^}]*padding-top:\s*max\(24px,\s*calc\(14px\s*\+\s*var\(--orin-titlebar-height,\s*0px\)\)\)/s,
    )

    // 2. Verify .settings-drawer and .settings-drawer__body grid constraints
    expect(agentosCss).toMatch(
      /\.settings-drawer\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)[^}]*min-width:\s*0[^}]*max-width:\s*100%[^}]*box-sizing:\s*border-box/s,
    )
    expect(agentosCss).toMatch(
      /\.settings-drawer__body\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)[^}]*min-width:\s*0[^}]*max-width:\s*100%[^}]*box-sizing:\s*border-box/s,
    )

    // 3. Verify .ollama-flow, .ollama-flow__connection, and .provider-key-list grid constraints in index.css
    expect(indexCss).toMatch(
      /\.ollama-flow\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)[^}]*min-width:\s*0[^}]*max-width:\s*100%[^}]*box-sizing:\s*border-box/s,
    )
    expect(indexCss).toMatch(
      /\.ollama-flow__connection\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)[^}]*min-width:\s*0[^}]*max-width:\s*100%[^}]*box-sizing:\s*border-box/s,
    )
    expect(indexCss).toMatch(
      /\.provider-key-list\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)[^}]*min-width:\s*0[^}]*max-width:\s*100%[^}]*box-sizing:\s*border-box/s,
    )

    // 4. Verify wrapping on headings/overview and .ollama-flow__modes + .ollama-flow .chip
    expect(indexCss).toMatch(/\.ollama-flow__overview\s*\{[^}]*flex-wrap:\s*wrap[^}]*min-width:\s*0/s)
    expect(indexCss).toMatch(/\.ollama-flow__section-heading\s*\{[^}]*flex-wrap:\s*wrap[^}]*min-width:\s*0/s)
    expect(indexCss).toMatch(/\.provider-panel__section-heading\s*\{[^}]*flex-wrap:\s*wrap[^}]*min-width:\s*0/s)
    expect(indexCss).not.toMatch(/\.ollama-flow__modes\s*\{[^}]*width:\s*max-content/s)
    expect(indexCss).toMatch(
      /\.ollama-flow__modes\s*\{[^}]*display:\s*flex[^}]*flex-wrap:\s*wrap[^}]*width:\s*100%[^}]*max-width:\s*100%[^}]*box-sizing:\s*border-box/s,
    )
    expect(indexCss).toMatch(
      /\.ollama-flow\s+\.chip\s*\{[^}]*flex:\s*1\s+1\s+auto[^}]*min-width:\s*0[^}]*max-width:\s*100%[^}]*white-space:\s*normal[^}]*text-align:\s*center/s,
    )

    // 5. Verify .settings-drawer single-column rules in index.css
    expect(indexCss).toContain('.settings-drawer .ollama-flow__overview')
    expect(indexCss).toContain('.settings-drawer .ollama-flow__fields')
    expect(indexCss).toContain('.settings-drawer .ollama-flow__mode-field')
    expect(indexCss).toContain('.settings-drawer .ollama-flow__footer')
    expect(indexCss).toContain('.settings-drawer .ollama-flow__actions')
    expect(indexCss).toContain('.settings-drawer .provider-key-list__fields')
    expect(indexCss).toContain('.settings-drawer .provider-key-list__cooldown')
    expect(indexCss).toContain('.settings-drawer .provider-key-list__cooldown-controls')

    // 6. Verify AnthropicSetup inside ProviderDetail renders all controls cleanly
    const onClose = vi.fn()
    const client = createMockClient({ anthropic: [] }, ['anthropic'])
    const { container } = render(
      <ProviderDetail
        provider="anthropic"
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }}
        onClose={onClose}
      />,
    )

    const drawer = screen.getByRole('region', { name: 'Anthropic' })
    expect(drawer).toHaveClass('settings-drawer')
    expect(container.querySelector('.settings-drawer__body')).not.toBeNull()
    expect(container.querySelector('form.ollama-flow')).not.toBeNull()
    expect(container.querySelector('.ollama-flow__overview')).not.toBeNull()
    expect(container.querySelector('.ollama-flow__connection')).not.toBeNull()
    expect(container.querySelector('.provider-key-list')).not.toBeNull()

    const modeGroup = within(drawer).getByRole('radiogroup', { name: 'Modo da Anthropic' })
    expect(modeGroup).toHaveClass('ollama-flow__modes')
    const localChip = within(modeGroup).getByRole('radio', { name: 'Assinatura Claude Code (CLI Local)' })
    const cloudChip = within(modeGroup).getByRole('radio', { name: 'Cloud (Chave de API)' })
    expect(localChip).toBeInTheDocument()
    expect(cloudChip).toBeInTheDocument()

    expect(within(drawer).getByLabelText('URL do servidor')).toBeInTheDocument()
    expect(within(drawer).getByLabelText('Chave de API')).toBeInTheDocument()
    expect(within(drawer).getByRole('button', { name: 'Testar conexão' })).toBeInTheDocument()
    expect(within(drawer).getByRole('button', { name: 'Salvar' })).toBeInTheDocument()
    expect(within(drawer).getByRole('button', { name: 'Revogar acesso' })).toBeInTheDocument()

    await user.click(localChip)
    expect(localChip).toHaveAttribute('aria-checked', 'true')
    expect(within(drawer).getByText('Assinatura CLI Local')).toBeInTheDocument()

    await user.click(cloudChip)
    expect(cloudChip).toHaveAttribute('aria-checked', 'true')
    expect(within(drawer).getByText('Anthropic Cloud')).toBeInTheDocument()
  })

  it('handles adversarial edge cases across provider setup flows (missing_csrf, empty vs filled cloud API key, Escape key, and all provider variants)', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    const client = createMockClient({ anthropic: [] }, ['anthropic'])

    // 1. Missing CSRF disables actions safely without crashing
    const { unmount } = render(
      <ProviderDetail
        provider="anthropic"
        client={client}
        bootstrap={{ status: 'missing_csrf' }}
        onClose={onClose}
      />,
    )
    const disabledDrawer = screen.getByRole('region', { name: 'Anthropic' })
    expect(within(disabledDrawer).getByRole('button', { name: 'Testar conexão' })).toBeDisabled()
    expect(within(disabledDrawer).getByRole('button', { name: 'Salvar' })).toBeDisabled()
    expect(within(disabledDrawer).getByRole('button', { name: 'Revogar acesso' })).toBeDisabled()
    unmount()

    // 2. Ready session: local mode allows saving without API key; cloud mode requires API key
    const { unmount: unmountReady } = render(
      <ProviderDetail
        provider="anthropic"
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }}
        onClose={onClose}
      />,
    )
    const readyDrawer = screen.getByRole('region', { name: 'Anthropic' })
    const saveBtn = within(readyDrawer).getByRole('button', { name: 'Salvar' })
    const localChip = within(readyDrawer).getByRole('radio', { name: 'Assinatura Claude Code (CLI Local)' })
    const cloudChip = within(readyDrawer).getByRole('radio', { name: 'Cloud (Chave de API)' })

    await user.click(localChip)
    expect(saveBtn).not.toBeDisabled()

    await user.click(cloudChip)
    expect(saveBtn).toBeDisabled()

    await user.type(within(readyDrawer).getByLabelText('Chave de API'), 'sk-ant-test-key-123')
    expect(saveBtn).not.toBeDisabled()

    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
    unmountReady()

    // 3. Other providers using .ollama-flow / .provider-panel__form mount cleanly inside .settings-drawer
    for (const provider of ['opencode', 'nvidia', 'ollama', 'openai'] as const) {
      const { container, unmount: unmountProvider } = render(
        <ProviderDetail
          provider={provider}
          client={client}
          bootstrap={{ status: 'ready', csrfToken: 'csrf-test' }}
          onClose={onClose}
        />,
      )
      expect(container.querySelector('.settings-drawer')).not.toBeNull()
      expect(container.querySelector('.settings-drawer__body')).not.toBeNull()
      expect(container.querySelector('.provider-key-list')).not.toBeNull()
      unmountProvider()
    }
  })

  it('survives localStorage errors when reading or saving and keeps catalog components mounted', async () => {
    const user = userEvent.setup()
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError')
    })

    const { container } = renderProvidersSection()

    expect(container.querySelector('.providers-ref14__catalog')).not.toBeNull()
    expect(screen.getByRole('region', { name: 'Leitura visual' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Geração de mídia e Voz Live' })).toBeInTheDocument()

    await user.click(screen.getByTestId('providers-save-btn'))
    expect(screen.getByTestId('providers-saved-notice')).toHaveTextContent('Alterações salvas.')

    await user.click(screen.getByTestId('providers-connect-btn'))
    expect(screen.getByTestId('location-probe')).toHaveTextContent('/settings/providers/openai')
  })
})

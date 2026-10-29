import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import * as providersApi from '../../../api/providers'
import { appRoutes } from '../../../app/routes'
import { OnboardingWizard, sanitizeOnboardingDisplayName } from '../OnboardingWizard'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function createWizardClientStub(options?: { failConfigure?: boolean; status?: number }) {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
    if (url.includes('/v1/session')) {
      return jsonResponse({ status: 'ready', csrf_token: 'csrf-onboarding-test' })
    }
    if (url.includes('/v1/providers/')) {
      if (options?.failConfigure) {
        return jsonResponse(
          { error: { code: 'invalid_credentials', message: 'Invalid API key provided' } },
          options.status ?? 401,
        )
      }
      return jsonResponse({ provider: 'openrouter', enabled: true })
    }
    return jsonResponse({ ok: true })
  })

  return {
    client: new ApiClient({ fetchImpl, maxAttempts: 1 }),
    fetchImpl,
  }
}

describe('OnboardingWizard (ref-02.png)', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renderiza o layout de 3 colunas conforme ref-02.png (Stepper 4 passos, Palco Central no Passo 2 por padrão e Painel Contextual com OrinMascot waving)', () => {
    const { client } = createWizardClientStub()

    render(
      <MemoryRouter initialEntries={['/onboarding']}>
        <OnboardingWizard client={client} />
      </MemoryRouter>,
    )

    // Coluna 1: Stepper Lateral Esquerdo com Brand + 4 passos
    const stepper = screen.getByTestId('onboarding-stepper')
    expect(within(stepper).getByLabelText(/Orin, início/i)).toBeInTheDocument()
    expect(within(stepper).getByText('Boas-vindas')).toBeInTheDocument()
    expect(within(stepper).getByText('Conheça o Orin')).toBeInTheDocument()
    expect(within(stepper).getByText('Conectar IA')).toBeInTheDocument()
    expect(within(stepper).getByText('Escolha como começar')).toBeInTheDocument()
    expect(within(stepper).getByText('Seu Friend')).toBeInTheDocument()
    expect(within(stepper).getByText('Personalize seu companheiro')).toBeInTheDocument()
    expect(within(stepper).getByText('Tudo pronto')).toBeInTheDocument()
    expect(within(stepper).getByText('Hora de começar')).toBeInTheDocument()

    // Coluna 2: Palco Central iniciando no Passo 2 ("Conectar IA")
    const stage = screen.getByTestId('onboarding-stage')
    expect(
      within(stage).getByRole('heading', { level: 1, name: 'Escolha como começar' }),
    ).toBeInTheDocument()
    expect(within(stage).getByText('Você pode mudar isso depois.')).toBeInTheDocument()

    // 3 cards selecionáveis
    expect(within(stage).getByText('Conectar um provedor')).toBeInTheDocument()
    expect(within(stage).getByText('Use uma chave de API do seu serviço.')).toBeInTheDocument()
    expect(within(stage).getByText('Usar IA no computador')).toBeInTheDocument()
    expect(within(stage).getByText('Conecte um modelo local pelo Ollama.')).toBeInTheDocument()
    expect(within(stage).getByText('Já tenho uma conexão')).toBeInTheDocument()
    expect(within(stage).getByText('Importar configurações.')).toBeInTheDocument()

    // Coluna 3: Painel Contextual Direito com OrinMascot waving (size 112)
    const contextPanel = screen.getByTestId('onboarding-context-panel')
    const mascot = within(contextPanel).getByTestId('orin-mascot')
    expect(mascot).toHaveAttribute('data-variant', 'waving')
    expect(mascot).toHaveStyle({ width: '112px', height: '112px' })
    expect(within(contextPanel).getByText('Vamos preparar seu espaço.')).toBeInTheDocument()
    expect(
      within(contextPanel).getByText(
        'Conecte sua IA preferida para começar a conversar, criar e ser mais produtivo com o Orin.',
      ),
    ).toBeInTheDocument()
  })

  it('permite escolher o provedor, alternar visibilidade da chave (👁) e testar conexão exibindo o badge verde "Conexão verificada"', async () => {
    const user = userEvent.setup()
    const { client } = createWizardClientStub()
    const configureSpy = vi.spyOn(providersApi, 'configureProvider')

    render(
      <MemoryRouter initialEntries={['/onboarding']}>
        <OnboardingWizard client={client} />
      </MemoryRouter>,
    )

    // Seletor de provedor inicia em openrouter ("OpenRouter") e permite escolher os demais
    const providerSelect = screen.getByLabelText(/Provedor de IA/i) as HTMLSelectElement
    expect(providerSelect.value).toBe('openrouter')
    expect(within(providerSelect).getByRole('option', { name: 'OpenRouter' })).toBeInTheDocument()
    expect(within(providerSelect).getByRole('option', { name: 'OpenAI' })).toBeInTheDocument()
    expect(within(providerSelect).getByRole('option', { name: 'Anthropic' })).toBeInTheDocument()
    expect(within(providerSelect).getByRole('option', { name: 'NVIDIA' })).toBeInTheDocument()
    expect(within(providerSelect).getByRole('option', { name: 'OmniRoute' })).toBeInTheDocument()
    expect(within(providerSelect).getByRole('option', { name: 'OpenCode' })).toBeInTheDocument()

    // Input de chave de API (type="password", autoComplete="off") + botão 👁
    const keyInput = screen.getByLabelText('Chave de API') as HTMLInputElement
    expect(keyInput).toHaveAttribute('type', 'password')
    expect(keyInput).toHaveAttribute('autocomplete', 'off')

    await user.type(keyInput, 'sk-or-v1-secret-token-12345')

    const toggleVisibilityBtn = screen.getByTestId('onboarding-toggle-key-visibility')
    await user.click(toggleVisibilityBtn)
    expect(keyInput).toHaveAttribute('type', 'text')
    await user.click(toggleVisibilityBtn)
    expect(keyInput).toHaveAttribute('type', 'password')

    // Clicar em "Testar conexão" chama configureProvider e exibe o badge verde
    expect(screen.queryByTestId('onboarding-connection-verified')).not.toBeInTheDocument()
    await user.click(screen.getByTestId('onboarding-test-connection'))

    expect(configureSpy).toHaveBeenCalledWith(
      client,
      'openrouter',
      expect.objectContaining({ apiKey: 'sk-or-v1-secret-token-12345', enabled: true }),
    )

    const badge = await screen.findByTestId('onboarding-connection-verified')
    expect(badge).toBeInTheDocument()
    expect(badge).toHaveTextContent(/Conexão verificada/i)

    // [Red-Team] Alterar a chave após verificação invalida imediatamente o selo "Conexão verificada"
    await user.type(keyInput, 'x')
    expect(screen.queryByTestId('onboarding-connection-verified')).not.toBeInTheDocument()
  })

  it('navega pelos 4 passos (Voltar / Continuar →), salva nome do usuário e Friend, e conclui o onboarding navegando para "/"', async () => {
    const user = userEvent.setup()
    const { client } = createWizardClientStub()

    render(
      <MemoryRouter initialEntries={['/onboarding']}>
        <Routes>
          <Route path="/onboarding" element={<OnboardingWizard client={client} />} />
          <Route path="/" element={<div data-testid="home-destination">Tela Inicial</div>} />
        </Routes>
      </MemoryRouter>,
    )

    // Começa no Passo 2 -> clicar em "Voltar" leva ao Passo 1 (Boas-vindas)
    await user.click(screen.getByRole('button', { name: /Voltar/i }))
    expect(
      screen.getByRole('heading', { level: 1, name: /Bem-vindo ao Orin/i }),
    ).toBeInTheDocument()

    const nameInput = screen.getByTestId('onboarding-display-name') as HTMLInputElement
    await user.clear(nameInput)
    await user.type(nameInput, 'Ana Clara')

    // Avançar para Passo 2 ("Continuar →") salva o displayName no localStorage
    await user.click(screen.getByRole('button', { name: /Continuar/i }))
    expect(window.localStorage.getItem('orin.user.displayName')).toBe('Ana Clara')
    expect(
      screen.getByRole('heading', { level: 1, name: 'Escolha como começar' }),
    ).toBeInTheDocument()

    // Avançar para Passo 3 (Seu Friend)
    await user.click(screen.getByRole('button', { name: /Continuar/i }))
    expect(
      screen.getByRole('heading', { level: 1, name: /Personalize seu companheiro/i }),
    ).toBeInTheDocument()

    const friendNameInput = screen.getByTestId('onboarding-friend-name') as HTMLInputElement
    await user.clear(friendNameInput)
    await user.type(friendNameInput, 'Atlas')
    await user.click(screen.getByRole('button', { name: /Direto/i }))

    // Avançar para Passo 4 (Tudo pronto)
    await user.click(screen.getByRole('button', { name: /Continuar/i }))
    expect(
      screen.getByRole('heading', { level: 1, name: /Tudo pronto para começar/i }),
    ).toBeInTheDocument()

    // Concluir onboarding
    await user.click(screen.getByRole('button', { name: /Começar a usar o Orin/i }))
    expect(window.localStorage.getItem('orin.onboarding.completed')).toBe('true')
    expect(await screen.findByTestId('home-destination')).toBeInTheDocument()
  })

  it('[Red-Team] bloqueia teste de conexão com chave vazia/espaços e trata falhas de API sem vazar a chave secreta', async () => {
    const user = userEvent.setup()
    const { client } = createWizardClientStub({ failConfigure: true, status: 401 })
    const configureSpy = vi.spyOn(providersApi, 'configureProvider')

    render(
      <MemoryRouter initialEntries={['/onboarding']}>
        <OnboardingWizard client={client} />
      </MemoryRouter>,
    )

    const keyInput = screen.getByLabelText('Chave de API')
    const testBtn = screen.getByTestId('onboarding-test-connection')

    // 1. Tentativa com espaços em branco não chama rede e exibe role="alert"
    await user.type(keyInput, '    ')
    await user.click(testBtn)

    const emptyAlert = screen.getByRole('alert')
    expect(emptyAlert).toHaveTextContent('Informe uma chave de API válida.')
    expect(configureSpy).not.toHaveBeenCalled()
    expect(screen.queryByTestId('onboarding-connection-verified')).not.toBeInTheDocument()

    // 2. Quando a API falha (401/500), limpa "Conexão verificada", exibe erro amigável e nunca vaza a chave
    const secretKey = 'sk-or-super-secret-never-leak-999'
    await user.clear(keyInput)
    await user.type(keyInput, secretKey)
    await user.click(testBtn)

    expect(configureSpy).toHaveBeenCalledTimes(1)
    const errorAlert = await screen.findByRole('alert')
    expect(errorAlert).toBeInTheDocument()
    expect(errorAlert.textContent).not.toContain(secretKey)
    expect(screen.queryByTestId('onboarding-connection-verified')).not.toBeInTheDocument()
    expect(window.localStorage.getItem('orin.provider.apiKey')).toBeNull()

    // 3. Sanitização de nome de usuário adversária
    expect(sanitizeOnboardingDisplayName('   ')).toBe('Carlos')
    expect(sanitizeOnboardingDisplayName('A'.repeat(120)).length).toBe(60)
  })

  it('registra a rota /onboarding em appRoutes (routes.tsx)', () => {
    const router = createMemoryRouter(appRoutes, {
      initialEntries: ['/onboarding'],
    })
    render(<RouterProvider router={router} />)
    expect(
      screen.getByRole('heading', { level: 1, name: 'Escolha como começar' }),
    ).toBeInTheDocument()
  })
})

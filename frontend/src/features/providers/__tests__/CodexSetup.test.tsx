import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import type { CodexCliStatus } from '../../../api/providers'
import { CodexSetup } from '../CodexSetup'
import { ProviderDetail } from '../ProviderDetail'

function createMockClient(statusResponse: CodexCliStatus | (() => Response | Promise<Response>)): ApiClient {
  const fetchImpl = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith('/v1/providers/openai-codex/status')) {
      if (typeof statusResponse === 'function') {
        return await statusResponse()
      }
      return new Response(JSON.stringify(statusResponse), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith('/v1/providers/openai-codex')) {
      return new Response(
        JSON.stringify({ provider: 'openai-codex', enabled: null, extra: {} }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (url.endsWith('/v1/providers/openai-codex/keys')) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  return new ApiClient({
    baseUrl: 'http://localhost:8000',
    csrfToken: 'csrf-token',
    fetchImpl,
    maxAttempts: 1,
  })
}

describe('CodexSetup', () => {
  it('shows install instructions and no API key field when Codex CLI binary is not installed', async () => {
    const client = createMockClient({
      installed: false,
      version: null,
      supported: false,
      authenticated: false,
      login_command: 'codex login',
    })

    render(
      <CodexSetup
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
      />,
    )

    expect(await screen.findByText(/Instale o Codex CLI/i)).toBeInTheDocument()
    expect(screen.getByText(/0\.167\.0/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/Chave de API/i)).toBeNull()
    expect(screen.getByRole('button', { name: /Salvar e ativar/i })).toBeDisabled()
  })

  it('shows codex login instruction when binary is installed and supported but auth.json is missing', async () => {
    const client = createMockClient({
      installed: true,
      version: '0.167.0',
      supported: true,
      authenticated: false,
      login_command: 'codex login',
    })

    render(
      <CodexSetup
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
      />,
    )

    expect(await screen.findByText(/codex login/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/Chave de API/i)).toBeNull()
    expect(screen.getByRole('button', { name: /Salvar e ativar/i })).toBeDisabled()
  })

  it('shows version and Pronto badge when installed, supported, and authenticated, allowing Salvar e ativar', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn((event) => event?.preventDefault?.())
    const client = createMockClient({
      installed: true,
      version: '0.167.0',
      supported: true,
      authenticated: true,
      login_command: 'codex login',
    })

    render(
      <CodexSetup
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
        onSave={onSave}
      />,
    )

    expect(await screen.findByText(/0\.167\.0/)).toBeInTheDocument()
    expect(screen.getByText('Pronto')).toBeInTheDocument()
    expect(
      screen.getByText(
        /Usa sua assinatura ChatGPT\/Codex via o Codex CLI oficial instalado nesta máquina\. Sujeito aos limites do seu plano ChatGPT\./i,
      ),
    ).toBeInTheDocument()
    expect(screen.queryByLabelText(/Chave de API/i)).toBeNull()

    const saveBtn = screen.getByRole('button', { name: /Salvar e ativar/i })
    expect(saveBtn).not.toBeDisabled()

    await user.click(saveBtn)
    expect(onSave).toHaveBeenCalledTimes(1)
  })

  it('warns when installed version is unsupported (< 0.167.0) and blocks activation', async () => {
    const client = createMockClient({
      installed: true,
      version: '0.160.0',
      supported: false,
      authenticated: true,
      login_command: 'codex login',
    })

    render(
      <CodexSetup
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
      />,
    )

    expect(await screen.findByText(/0\.160\.0/)).toBeInTheDocument()
    expect(screen.getByText(/atualizar.*0\.167\.0/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Salvar e ativar/i })).toBeDisabled()
  })

  it('re-checks status when clicking Verificar novamente and handles transient API errors gracefully', async () => {
    const user = userEvent.setup()
    let attempt = 0
    const client = createMockClient(() => {
      attempt += 1
      if (attempt === 1) {
        return new Response(JSON.stringify({ error: { code: 'internal', category: 'INTERNAL' } }), {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      return new Response(
        JSON.stringify({
          installed: true,
          version: '0.167.0',
          supported: true,
          authenticated: true,
          login_command: 'codex login',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    })

    render(
      <CodexSetup
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
      />,
    )

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Verificar novamente/i }))

    expect(await screen.findByText('Pronto')).toBeInTheDocument()
  })

  it('renders CodexSetup inside ProviderDetail for openai-codex without API key list', async () => {
    const client = createMockClient({
      installed: true,
      version: '0.167.0',
      supported: true,
      authenticated: true,
      login_command: 'codex login',
    })

    render(
      <ProviderDetail
        provider="openai-codex"
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
        onClose={() => {}}
      />,
    )

    expect(await screen.findByText('Pronto')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Chave de API/i)).toBeNull()
  })
})

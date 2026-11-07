import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import type { AntigravityCliStatus } from '../../../api/providers'
import { AntigravitySetup } from '../AntigravitySetup'
import { ProviderDetail } from '../ProviderDetail'

function createMockClient(
  statusResponse: AntigravityCliStatus | (() => Response | Promise<Response>),
  providerExtra: Record<string, unknown> = {},
  onPut?: (body: Record<string, unknown>) => void,
): ApiClient {
  const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/v1/providers/google-antigravity/status')) {
      if (typeof statusResponse === 'function') {
        return await statusResponse()
      }
      return new Response(JSON.stringify(statusResponse), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith('/v1/providers/google-antigravity') && init?.method === 'PUT') {
      const parsed = init.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {}
      onPut?.(parsed)
      return new Response(
        JSON.stringify({
          provider: 'google-antigravity',
          enabled: parsed.enabled ?? true,
          risk_acknowledged_at: '2026-11-06T22:00:00Z',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (url.endsWith('/v1/providers/google-antigravity')) {
      return new Response(
        JSON.stringify({
          provider: 'google-antigravity',
          enabled: null,
          ...providerExtra,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (url.endsWith('/v1/providers/google-antigravity/models:refresh')) {
      return new Response(
        JSON.stringify({ count: 3, refreshed_at: '2026-11-06T22:00:00Z' }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }
    if (url.endsWith('/v1/providers/google-antigravity/models')) {
      return new Response(JSON.stringify({ items: [] }), {
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
    createIdempotencyKey: () => 'intent-agy-test',
  })
}

describe('AntigravitySetup', () => {
  it('shows install instructions and no API key field when Antigravity CLI binary is not installed', async () => {
    const client = createMockClient({
      installed: false,
      version: null,
      supported: false,
      authenticated: false,
      login_command: 'agy',
    })

    render(
      <AntigravitySetup
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
      />,
    )

    expect(await screen.findByText(/Instale o Antigravity CLI/i)).toBeInTheDocument()
    expect(screen.getByText(/1\.1\.1/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/Chave de API/i)).toBeNull()
    expect(screen.getByRole('button', { name: /Salvar e ativar/i })).toBeDisabled()
  })

  it('shows agy login instruction when binary is installed and supported but jetski_state.pbtxt is missing', async () => {
    const client = createMockClient({
      installed: true,
      version: '1.5.2',
      supported: true,
      authenticated: false,
      login_command: 'agy',
    })

    render(
      <AntigravitySetup
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
      />,
    )

    expect(
      await screen.findByText(/Abra.*uma vez.*entre com sua conta Google/i),
    ).toBeInTheDocument()
    expect(screen.queryByLabelText(/Chave de API/i)).toBeNull()
    expect(screen.getByRole('button', { name: /Salvar e ativar/i })).toBeDisabled()
  })

  it('keeps Salvar e ativar disabled when everything is ready but the D2 risk acknowledgment checkbox is unchecked', async () => {
    const client = createMockClient({
      installed: true,
      version: '1.5.2',
      supported: true,
      authenticated: true,
      login_command: 'agy',
    })

    render(
      <AntigravitySetup
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
      />,
    )

    expect(await screen.findByText(/1\.5\.2/)).toBeInTheDocument()
    expect(screen.getByText('Pronto')).toBeInTheDocument()
    expect(
      screen.getByText(
        /Wrapping agy inside third-party coding agents or applications, or automating account rotation, is not supported under consumer plans\./i,
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        /Using third-party software, tools, or services to access Antigravity is a violation of our Terms of Service\./i,
      ),
    ).toBeInTheDocument()
    expect(screen.getByText(/sua conta Google pode ser restringida/i)).toBeInTheDocument()

    const faqLink = screen.getByRole('link', { name: /antigravity\.google\/docs\/faq/i })
    expect(faqLink).toHaveAttribute('href', 'https://antigravity.google/docs/faq/')

    const riskCheckbox = screen.getByRole('checkbox', {
      name: /Estou ciente do aviso do FAQ oficial, entendo que minha conta Google pode ser restringida e assumo o risco/i,
    })
    expect(riskCheckbox).not.toBeChecked()

    const saveBtn = screen.getByRole('button', { name: /Salvar e ativar/i })
    expect(saveBtn).toBeDisabled()
  })

  it('enables Salvar e ativar only after checking the risk acknowledgment checkbox and never persists risk opt-in to localStorage', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn((event) => event?.preventDefault?.())
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem')
    const client = createMockClient({
      installed: true,
      version: '1.5.2',
      supported: true,
      authenticated: true,
      login_command: 'agy',
    })

    render(
      <AntigravitySetup
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
        onSave={onSave}
      />,
    )

    expect(await screen.findByText('Pronto')).toBeInTheDocument()

    const saveBtn = screen.getByRole('button', { name: /Salvar e ativar/i })
    expect(saveBtn).toBeDisabled()

    const riskCheckbox = screen.getByRole('checkbox', {
      name: /Estou ciente do aviso do FAQ oficial, entendo que minha conta Google pode ser restringida e assumo o risco/i,
    })
    await user.click(riskCheckbox)
    expect(riskCheckbox).toBeChecked()
    expect(saveBtn).not.toBeDisabled()

    await user.click(saveBtn)
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(setItemSpy).not.toHaveBeenCalled()
    setItemSpy.mockRestore()
  })

  it('warns when installed version is unsupported (< 1.1.1) and blocks activation even if risk checkbox is checked', async () => {
    const user = userEvent.setup()
    const client = createMockClient({
      installed: true,
      version: '1.0.14',
      supported: false,
      authenticated: true,
      login_command: 'agy',
    })

    render(
      <AntigravitySetup
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
      />,
    )

    expect(await screen.findByText(/1\.0\.14/)).toBeInTheDocument()
    expect(screen.getByText(/atualizar.*1\.1\.1/i)).toBeInTheDocument()

    const riskCheckbox = screen.getByRole('checkbox', {
      name: /Estou ciente do aviso do FAQ oficial, entendo que minha conta Google pode ser restringida e assumo o risco/i,
    })
    await user.click(riskCheckbox)
    expect(screen.getByRole('button', { name: /Salvar e ativar/i })).toBeDisabled()
  })

  it('renders AntigravitySetup inside ProviderDetail for google-antigravity and sends risk_acknowledged: true on PUT', async () => {
    const user = userEvent.setup()
    let sentBody: Record<string, unknown> | null = null
    const client = createMockClient(
      {
        installed: true,
        version: '1.5.2',
        supported: true,
        authenticated: true,
        login_command: 'agy',
      },
      {},
      (body) => {
        sentBody = body
      },
    )

    render(
      <ProviderDetail
        provider="google-antigravity"
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
        onClose={() => {}}
      />,
    )

    expect(await screen.findByText('Pronto')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Chave de API/i)).toBeNull()

    const saveBtn = screen.getByRole('button', { name: /Salvar e ativar/i })
    expect(saveBtn).toBeDisabled()

    const riskCheckbox = screen.getByRole('checkbox', {
      name: /Estou ciente do aviso do FAQ oficial, entendo que minha conta Google pode ser restringida e assumo o risco/i,
    })
    await user.click(riskCheckbox)
    expect(saveBtn).not.toBeDisabled()

    await user.click(saveBtn)

    await waitFor(() => {
      expect(sentBody).toEqual({
        api_key: '',
        enabled: true,
        risk_acknowledged: true,
      })
    })
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
          version: '1.5.2',
          supported: true,
          authenticated: true,
          login_command: 'agy',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    })

    render(
      <AntigravitySetup
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
      />,
    )

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Verificar novamente/i }))

    expect(await screen.findByText('Pronto')).toBeInTheDocument()
  })

  it('pre-checks the risk acknowledgment checkbox when backend state already has risk_acknowledged_at', async () => {
    const client = createMockClient(
      {
        installed: true,
        version: '1.5.2',
        supported: true,
        authenticated: true,
        login_command: 'agy',
      },
      { risk_acknowledged_at: '2026-11-06T20:00:00Z' },
    )

    render(
      <ProviderDetail
        provider="google-antigravity"
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
        onClose={() => {}}
      />,
    )

    expect(await screen.findByText('Pronto')).toBeInTheDocument()
    const riskCheckbox = screen.getByRole('checkbox', {
      name: /Estou ciente do aviso do FAQ oficial, entendo que minha conta Google pode ser restringida e assumo o risco/i,
    })
    await waitFor(() => {
      expect(riskCheckbox).toBeChecked()
    })
    expect(screen.getByRole('button', { name: /Salvar e ativar/i })).not.toBeDisabled()
  })
})

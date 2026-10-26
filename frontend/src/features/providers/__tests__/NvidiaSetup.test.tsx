import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { NvidiaSetup } from '../NvidiaSetup'
import { ProviderDetail } from '../ProviderDetail'

describe('NvidiaSetup', () => {
  it('renders mode toggle (NVIDIA NIM Cloud vs Self-Hosted NIM), default URL, and guidance callout', async () => {
    const user = userEvent.setup()
    const onModeChange = vi.fn()
    const onBaseUrlChange = vi.fn()

    render(<NvidiaSetup onModeChange={onModeChange} onBaseUrlChange={onBaseUrlChange} />)

    const cloudRadio = screen.getByRole('radio', { name: 'NVIDIA NIM Cloud' })
    const selfHostedRadio = screen.getByRole('radio', { name: 'Self-Hosted NIM' })

    expect(cloudRadio).toHaveAttribute('aria-checked', 'true')
    expect(selfHostedRadio).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByLabelText('URL do servidor')).toHaveValue('https://integrate.api.nvidia.com/v1')
    expect(screen.getByText(/build\.nvidia\.com/i)).toBeInTheDocument()
    expect(screen.getByText(/rotação automática/i)).toBeInTheDocument()

    await user.click(selfHostedRadio)
    expect(onModeChange).toHaveBeenCalledWith('self-hosted')
    expect(screen.getByLabelText('URL do servidor')).toHaveValue('http://localhost:8000/v1')
  })

  it('triggers onTestConnection and displays reachability, authentication status, and discovered model count', async () => {
    const user = userEvent.setup()
    const onTestConnection = vi.fn().mockResolvedValue({
      reachable: true,
      authenticated: true,
      model_count: 18,
    })

    render(<NvidiaSetup onTestConnection={onTestConnection} />)

    await user.type(screen.getByLabelText('Chave de API'), 'nvapi-secret-123')
    await user.click(screen.getByRole('button', { name: 'Testar conexão' }))

    expect(onTestConnection).toHaveBeenCalledWith({
      apiKey: 'nvapi-secret-123',
      baseUrl: 'https://integrate.api.nvidia.com/v1',
    })
    expect(await screen.findByText(/Endpoint acessível/i)).toBeInTheDocument()
    expect(screen.getByText(/Autenticado/i)).toBeInTheDocument()
    expect(screen.getByText(/18 modelos disponíveis/i)).toBeInTheDocument()
  })

  it('displays unauthenticated status when connection is reachable but key is rejected', async () => {
    const user = userEvent.setup()
    const onTestConnection = vi.fn().mockResolvedValue({
      reachable: true,
      authenticated: false,
      model_count: 0,
    })

    render(<NvidiaSetup onTestConnection={onTestConnection} />)

    await user.type(screen.getByLabelText('Chave de API'), 'nvapi-expired')
    await user.click(screen.getByRole('button', { name: 'Testar conexão' }))

    expect(await screen.findByText(/Endpoint acessível/i)).toBeInTheDocument()
    expect(screen.getByText(/Não autenticado/i)).toBeInTheDocument()
    expect(screen.getByText(/0 modelos disponíveis/i)).toBeInTheDocument()
  })

  it('triggers onSave when submitting the form and allows keyless save in Self-Hosted NIM mode', async () => {
    const user = userEvent.setup()
    const onSave = vi.fn((event) => event?.preventDefault?.())

    render(<NvidiaSetup onSave={onSave} />)

    const saveBtn = screen.getByRole('button', { name: /Salvar/i })
    expect(saveBtn).toBeDisabled()

    await user.click(screen.getByRole('radio', { name: 'Self-Hosted NIM' }))
    expect(saveBtn).not.toBeDisabled()

    await user.click(saveBtn)
    expect(onSave).toHaveBeenCalledTimes(1)
  })

  it('resets nvidiaTestResult in useProviderState when switching modes in ProviderDetail', async () => {
    const user = userEvent.setup()
    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.endsWith('/v1/providers/nvidia/test') && init?.method === 'POST') {
        return new Response(
          JSON.stringify({ reachable: true, authenticated: true, model_count: 12 }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      if (url.endsWith('/v1/providers/nvidia/keys')) {
        return new Response(JSON.stringify({ keys: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      if (url.endsWith('/v1/providers/nvidia')) {
        return new Response(
          JSON.stringify({ provider: 'nvidia', enabled: null, extra: {} }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    const client = new ApiClient({
      baseUrl: 'http://localhost:8000',
      csrfToken: 'csrf-token',
      fetchImpl,
      maxAttempts: 1,
    })

    render(
      <ProviderDetail
        provider="nvidia"
        client={client}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-token' }}
        onClose={() => {}}
      />,
    )

    await user.type(await screen.findByLabelText('Chave de API'), 'nvapi-test-key')
    await user.click(screen.getByRole('button', { name: 'Testar conexão' }))

    expect(await screen.findByText(/Endpoint acessível/i)).toBeInTheDocument()
    expect(screen.getByText(/12 modelos disponíveis/i)).toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: 'Self-Hosted NIM' }))

    expect(screen.queryByText(/Endpoint acessível/i)).toBeNull()
    expect(screen.queryByText(/12 modelos disponíveis/i)).toBeNull()
  })
})

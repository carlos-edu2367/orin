import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { Home } from '../../src/app/Home'

const READY_SESSION = { status: 'ready', csrfToken: 'csrf-home-test' } as const

const MODEL = {
  provider: 'openrouter', model_id: 'anthropic/model-a', display_name: 'Model A', context_window: 128000,
  capabilities: [], input_modalities: ['text'], output_modalities: ['text'], pricing: null, is_favorite: false, refreshed_at: null,
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** Route every call by path so the component is free to order its requests. */
function router(routes: Record<string, () => Response>) {
  return vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : (input as Request).url ?? String(input)
    for (const [fragment, respond] of Object.entries(routes)) {
      if (url.includes(fragment)) return respond()
    }
    return json({ items: [] })
  })
}

function renderHome(fetchImpl: ReturnType<typeof router>) {
  const client = new ApiClient({ fetchImpl, maxAttempts: 1 })
  return render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Home client={client} bootstrap={READY_SESSION} />} />
        <Route path="/chats/:conversationId" element={<p>Chat aberto</p>} />
        <Route path="/providers" element={<p>Providers</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Home', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('presents one input as the primary action with the decorative layer hidden', async () => {
    renderHome(router({
      '/models': () => json({ items: [MODEL] }),
      '/v1/conversations': () => json({ items: [] }),
    }))

    expect(await screen.findByRole('textbox', { name: 'Mensagem' })).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Enviar mensagem' })).toBeInTheDocument()
  })

  it('offers a way to configure a provider when the catalog is empty', async () => {
    renderHome(router({
      '/models': () => json({ items: [] }),
      '/v1/conversations': () => json({ items: [] }),
    }))

    const action = await screen.findByRole('link', { name: /Configurar provider/i })
    expect(action).toHaveAttribute('href', '/providers')
  })

  it('says the catalog failed rather than pretending there are no models', async () => {
    renderHome(router({
      '/models': () => json({ error: { code: 'provider_catalog_unavailable', category: 'PROVIDER', message_key: 'x', correlation_id: 'c', retryable: true } }, 503),
      '/v1/conversations': () => json({ items: [] }),
    }))

    expect(await screen.findByRole('link', { name: /Catálogo indisponível/i })).toBeInTheDocument()
  })

  it('starts a conversation and navigates to it', async () => {
    const fetchImpl = router({
      '/models': () => json({ items: [MODEL] }),
      '/v1/conversations': () => json({ conversation_id: 'chat-new', title: 'Nova', turn_id: 't', message_id: 'm', state: 'queued' }, 201),
    })
    renderHome(fetchImpl)

    const user = userEvent.setup()
    await user.type(await screen.findByRole('textbox', { name: 'Mensagem' }), 'Organizar a semana')
    await user.click(screen.getByRole('button', { name: 'Enviar mensagem' }))

    expect(await screen.findByText('Chat aberto')).toBeInTheDocument()
  })

  it('explains a backend failure in terms the operator can act on', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input) => {
      const url = typeof input === 'string' ? input : String(input)
      if (url.includes('/models')) return json({ items: [MODEL] })
      if (url.includes('/v1/conversations') && !url.includes('/chats')) {
        return json({ error: { code: 'internal_error', category: 'INTERNAL', message_key: 'internal_error', correlation_id: 'c', retryable: false } }, 500)
      }
      return json({ items: [] })
    })
    renderHome(fetchImpl as ReturnType<typeof router>)

    const user = userEvent.setup()
    await user.type(await screen.findByRole('textbox', { name: 'Mensagem' }), 'Falhar')
    await user.click(screen.getByRole('button', { name: 'Enviar mensagem' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/worker/i)
  })

  it('lets a recent conversation be reopened without leaving the home', async () => {
    renderHome(router({
      '/models': () => json({ items: [MODEL] }),
      '/v1/conversations': () => json({ items: [{ conversation_id: 'chat-existing', title: 'Planejar a semana', state: 'completed' }] }),
    }))

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /Planejar a semana/i }))

    expect(await screen.findByText('Chat aberto')).toBeInTheDocument()
  })

  it('opens the command palette with the keyboard', async () => {
    renderHome(router({ '/models': () => json({ items: [MODEL] }), '/v1/conversations': () => json({ items: [] }) }))
    await screen.findByRole('textbox', { name: 'Mensagem' })

    const user = userEvent.setup()
    await user.keyboard('{Control>}k{/Control}')

    expect(await screen.findByRole('dialog', { name: 'Navegação' })).toBeInTheDocument()
  })

  it('shows a chip after picking a file on the home screen', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.includes('/models')) return json({ items: [MODEL] })
      if (url.endsWith('/v1/uploads') && method === 'POST') {
        return json({ upload_id: 'upl_1', filename: 'foto.png', media_type: 'image/png', kind: 'image', bytes: 3 }, 201)
      }
      return json({ items: [] })
    })
    const { container } = renderHome(fetchImpl as ReturnType<typeof router>)
    await screen.findByRole('textbox', { name: 'Mensagem' })

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File([new Uint8Array([1, 2, 3])], 'foto.png', { type: 'image/png' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    expect(await screen.findByText('foto.png')).toBeInTheDocument()
  })

  it('sends a new conversation with only a file and no text', async () => {
    const created = vi.fn()
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.includes('/models')) return json({ items: [MODEL] })
      if (url.endsWith('/v1/uploads') && method === 'POST') {
        return json({ upload_id: 'upl_1', filename: 'foto.png', media_type: 'image/png', kind: 'image', bytes: 3 }, 201)
      }
      if (url.endsWith('/v1/conversations') && method === 'POST') {
        created(String(init?.body ?? ''))
        return json({ conversation_id: 'chat-new', title: 'Nova', turn_id: 't', message_id: 'm', state: 'queued' }, 201)
      }
      return json({ items: [] })
    })
    const { container } = renderHome(fetchImpl as ReturnType<typeof router>)
    await screen.findByRole('textbox', { name: 'Mensagem' })

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File([new Uint8Array([1, 2, 3])], 'foto.png', { type: 'image/png' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => expect(screen.getByRole('button', { name: 'Enviar mensagem' })).not.toBeDisabled())
    fireEvent.click(screen.getByRole('button', { name: 'Enviar mensagem' }))

    await waitFor(() => expect(created).toHaveBeenCalled())
    const body = JSON.parse(created.mock.calls[0][0] as string)
    expect(body).toMatchObject({ message: '', attachments: ['upl_1'] })
    expect(await screen.findByText('Chat aberto')).toBeInTheDocument()
  })

  it('blocks sending while an upload is still in flight', async () => {
    let resolveUpload: (() => void) | undefined
    const uploadPending = new Promise<void>((resolve) => { resolveUpload = resolve })
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : String(input)
      const method = (init?.method ?? 'GET').toUpperCase()
      if (url.includes('/models')) return json({ items: [MODEL] })
      if (url.endsWith('/v1/uploads') && method === 'POST') {
        await uploadPending
        return json({ upload_id: 'upl_1', filename: 'foto.png', media_type: 'image/png', kind: 'image', bytes: 3 }, 201)
      }
      return json({ items: [] })
    })
    const { container } = renderHome(fetchImpl as ReturnType<typeof router>)
    await screen.findByRole('textbox', { name: 'Mensagem' })

    const fileInput = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File([new Uint8Array([1, 2, 3])], 'foto.png', { type: 'image/png' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await screen.findByText('foto.png')
    expect(screen.getByRole('button', { name: 'Enviar mensagem' })).toBeDisabled()

    resolveUpload?.()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Enviar mensagem' })).not.toBeDisabled())
  })
})

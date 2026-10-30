// @vitest-environment jsdom
import '../setup'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { MemoryPage } from '../../src/features/memory/MemoryPage'

function createMockClient(
  initialItems: Array<{
    memory_id: string
    fact: string
    tags: string[]
    scope: 'user' | 'project'
    project_id: string | null
    conversation_id: string | null
    created_at: string | null
    updated_at: string | null
  }> = [],
) {
  let items = [...initialItems]
  const deletedIds: string[] = []
  const updatedCalls: Array<{ id: string; fact: string }> = []
  const createdBodies: Array<Record<string, unknown>> = []

  const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    const method = (init?.method ?? 'GET').toUpperCase()

    if (url.includes('/v1/memories/') && method === 'DELETE') {
      const match = url.match(/\/v1\/memories\/([^?]+)/)
      const id = match ? decodeURIComponent(match[1]) : ''
      deletedIds.push(id)
      items = items.filter((item) => item.memory_id !== id)
      return new Response(null, { status: 204 })
    }

    if (url.includes('/v1/memories/') && method === 'PATCH') {
      const match = url.match(/\/v1\/memories\/([^?]+)/)
      const id = match ? decodeURIComponent(match[1]) : ''
      const body = JSON.parse(String(init?.body ?? '{}')) as { fact: string }
      updatedCalls.push({ id, fact: body.fact })
      const existing = items.find((item) => item.memory_id === id)
      const updated = existing
        ? { ...existing, fact: body.fact, updated_at: '2026-10-30T12:00:00Z' }
        : {
            memory_id: id,
            fact: body.fact,
            tags: [],
            scope: 'user' as const,
            project_id: null,
            conversation_id: null,
            created_at: '2026-10-30T12:00:00Z',
            updated_at: '2026-10-30T12:00:00Z',
          }
      items = items.map((item) => (item.memory_id === id ? updated : item))
      return new Response(JSON.stringify(updated), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    if (url.includes('/v1/memories') && method === 'POST') {
      const body = JSON.parse(String(init?.body ?? '{}')) as {
        fact: string
        scope?: 'user' | 'project'
        project_id?: string | null
        tags?: string[]
      }
      createdBodies.push(body as Record<string, unknown>)
      const created = {
        memory_id: `mem-${items.length + 1}`,
        fact: body.fact,
        tags: body.tags ?? [],
        scope: body.scope ?? 'user',
        project_id: body.project_id ?? null,
        conversation_id: null,
        created_at: '2026-10-30T12:00:00Z',
        updated_at: '2026-10-30T12:00:00Z',
      }
      items = [created, ...items]
      return new Response(JSON.stringify(created), {
        status: 201,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    if (url.includes('/v1/memories')) {
      return new Response(JSON.stringify({ items, next_cursor: null }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    return new Response(JSON.stringify({ items: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  })

  const client = new ApiClient({
    fetchImpl,
    csrfToken: 'test-csrf',
    maxAttempts: 1,
    createIdempotencyKey: () => 'test-idempotency',
  })

  return { client, fetchImpl, deletedIds, updatedCalls, createdBodies }
}

const SAMPLE_MEMORIES = [
  {
    memory_id: 'mem-work-1',
    fact: 'Prefere respostas diretas em português e código tipado.',
    tags: ['work'],
    scope: 'user' as const,
    project_id: null,
    conversation_id: null,
    created_at: '2026-10-12T10:00:00Z',
    updated_at: '2026-10-12T10:00:00Z',
  },
  {
    memory_id: 'mem-proj-1',
    fact: 'Usar Vitest e Playwright antes de concluir cada entrega.',
    tags: ['project'],
    scope: 'project' as const,
    project_id: 'proj-1',
    conversation_id: null,
    created_at: '2026-10-14T10:00:00Z',
    updated_at: '2026-10-14T10:00:00Z',
  },
  {
    memory_id: 'mem-work-2',
    fact: 'Prefere exemplos em TypeScript.',
    tags: ['work'],
    scope: 'user' as const,
    project_id: null,
    conversation_id: null,
    created_at: '2026-10-15T10:00:00Z',
    updated_at: '2026-10-15T10:00:00Z',
  },
]

describe('Phase 6 — MemoryPage (ref-11.png)', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    window.localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders header, scope tabs outside add button, search input, and honest empty state when API returns empty list', async () => {
    const { client } = createMockClient([])

    render(
      <MemoryRouter initialEntries={['/library/memory']}>
        <MemoryPage embedded client={client} />
      </MemoryRouter>,
    )

    const heading = screen.getByTestId('memory-heading')
    expect(heading).toHaveTextContent('Memória sob seu controle')
    expect(
      screen.getByText(
        'Tudo o que o Orin lembra sobre você e seus projetos. Você pode editar ou apagar qualquer item.',
      ),
    ).toBeInTheDocument()

    const tablist = screen.getByRole('tablist', { name: 'Escopo da memória' })
    expect(within(tablist).getByRole('tab', { name: 'Trabalho' })).toHaveAttribute('aria-selected', 'true')
    expect(within(tablist).getByRole('tab', { name: 'Friend' })).toHaveAttribute('aria-selected', 'false')
    expect(within(tablist).getByRole('tab', { name: 'Por projeto' })).toHaveAttribute('aria-selected', 'false')

    const addBtn = screen.getByTestId('memory-add-button')
    expect(addBtn).toHaveTextContent('+ Adicionar lembrança')
    expect(tablist.contains(addBtn)).toBe(false)

    const searchInput = screen.getByLabelText('Buscar memórias')
    expect(searchInput).toHaveAttribute('placeholder', 'Buscar lembranças...')

    expect(await screen.findByTestId('memory-empty-state')).toHaveTextContent('Nenhuma memória salva neste filtro.')
    expect(screen.queryByText(/Auryly/)).not.toBeInTheDocument()
  })

  it('updates the right-hand inspector when selecting a memory card and tracks the 200-char limit', async () => {
    const { client } = createMockClient(SAMPLE_MEMORIES)

    render(
      <MemoryRouter initialEntries={['/library/memory']}>
        <MemoryPage embedded client={client} />
      </MemoryRouter>,
    )

    const firstFact = SAMPLE_MEMORIES[0].fact
    await screen.findByText(firstFact)

    const inspector = screen.getByTestId('memory-inspector')
    expect(within(inspector).getByRole('heading', { name: 'Editar lembrança' })).toBeInTheDocument()

    const textarea = within(inspector).getByLabelText('Texto da lembrança') as HTMLTextAreaElement
    expect(textarea).toHaveAttribute('maxLength', '200')
    expect(textarea.value).toBe(firstFact)

    const counter = screen.getByTestId('memory-char-counter')
    expect(counter).toHaveTextContent(`${firstFact.length} / 200 caracteres`)

    const scopeSelect = within(inspector).getByLabelText('Escopo') as HTMLSelectElement
    const optionLabels = Array.from(scopeSelect.options).map((opt) => opt.textContent)
    expect(optionLabels).toEqual(['Trabalho', 'Friend', 'Por projeto'])
    expect(scopeSelect.value).toBe('Trabalho')

    expect(screen.getByTestId('memory-save-button')).toHaveTextContent('Salvar')
    expect(screen.getByTestId('memory-forget-button')).toHaveTextContent('Esquecer')

    // Click second card (project scope)
    fireEvent.click(screen.getByText(SAMPLE_MEMORIES[1].fact))
    expect(textarea.value).toContain(SAMPLE_MEMORIES[1].fact)
    expect(scopeSelect.value).toBe('Por projeto')
  })

  it('saves an edited or newly added memory, calls createManagedMemory without fake project fallback, and reverts on undo', async () => {
    const { client, createdBodies } = createMockClient(SAMPLE_MEMORIES)

    render(
      <MemoryRouter initialEntries={['/library/memory']}>
        <MemoryPage embedded client={client} />
      </MemoryRouter>,
    )

    await screen.findByText(SAMPLE_MEMORIES[0].fact)

    const inspector = screen.getByTestId('memory-inspector')
    const textarea = within(inspector).getByLabelText('Texto da lembrança') as HTMLTextAreaElement

    // Edit existing item and save
    fireEvent.change(textarea, {
      target: { value: 'Prefere respostas concisas em português.' },
    })
    fireEvent.click(screen.getByTestId('memory-save-button'))

    const toast = await screen.findByTestId('memory-saved-toast')
    expect(toast).toHaveTextContent('Nova lembrança salva.')
    expect(screen.getByText('Prefere respostas concisas em português.')).toBeInTheDocument()

    // Click Desfazer to revert edit
    const undoBtn = screen.getByTestId('memory-undo-button')
    fireEvent.click(undoBtn)

    await waitFor(() => {
      expect(screen.getByText(SAMPLE_MEMORIES[0].fact)).toBeInTheDocument()
    })

    // Add a brand new memory with "Por projeto" scope when projectId is not set -> project_id must be null (never 'auryly')
    fireEvent.click(screen.getByTestId('memory-add-button'))
    fireEvent.change(within(inspector).getByLabelText('Escopo'), {
      target: { value: 'Por projeto' },
    })
    fireEvent.change(within(inspector).getByLabelText('Texto da lembrança'), {
      target: { value: 'Sempre rodar vitest antes do commit' },
    })
    fireEvent.click(screen.getByTestId('memory-save-button'))

    expect(await screen.findByText('Sempre rodar vitest antes do commit')).toBeInTheDocument()
    expect(screen.getByTestId('memory-saved-toast')).toHaveTextContent('Nova lembrança salva.')
    await waitFor(() => {
      expect(createdBodies.length).toBeGreaterThan(0)
      expect(createdBodies[0]).toMatchObject({
        fact: 'Sempre rodar vitest antes do commit',
        scope: 'project',
        project_id: null,
      })
    })

    // Undo creation removes the newly created memory immediately
    fireEvent.click(screen.getByTestId('memory-undo-button'))
    await waitFor(() => {
      expect(screen.queryByText('Sempre rodar vitest antes do commit')).not.toBeInTheDocument()
    })
  })

  it('removes a memory item when clicking "Esquecer" and calls deleteManagedMemory for API items', async () => {
    const { client, deletedIds } = createMockClient([
      {
        memory_id: 'api-mem-1',
        fact: 'Usar Bun nos scripts utilitários',
        tags: ['work'],
        scope: 'user',
        project_id: null,
        conversation_id: null,
        created_at: '2026-10-12T10:00:00Z',
        updated_at: '2026-10-12T10:00:00Z',
      },
      {
        memory_id: 'api-mem-2',
        fact: 'Evitar mocks globais desnecessários',
        tags: ['work'],
        scope: 'user',
        project_id: null,
        conversation_id: null,
        created_at: '2026-10-15T10:00:00Z',
        updated_at: '2026-10-15T10:00:00Z',
      },
    ])

    render(
      <MemoryRouter initialEntries={['/library/memory']}>
        <MemoryPage embedded client={client} />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Usar Bun nos scripts utilitários')).toBeInTheDocument()
    expect(screen.getByText('Evitar mocks globais desnecessários')).toBeInTheDocument()

    // Click Esquecer on selected first item ('api-mem-1')
    fireEvent.click(screen.getByTestId('memory-forget-button'))

    await waitFor(() => {
      expect(deletedIds).toContain('api-mem-1')
      expect(screen.queryByText('Usar Bun nos scripts utilitários')).not.toBeInTheDocument()
    })
    expect(screen.getByText('Evitar mocks globais desnecessários')).toBeInTheDocument()
  })

  it('handles corrupted localStorage safely, enforces 200-char clamping and whitespace validation, and filters by scope tab and search query', async () => {
    window.localStorage.setItem('orin.memory.custom.v1', '{corrupted-json')
    const { client } = createMockClient(SAMPLE_MEMORIES)

    render(
      <MemoryRouter initialEntries={['/library/memory']}>
        <MemoryPage embedded client={client} />
      </MemoryRouter>,
    )

    await screen.findByText(SAMPLE_MEMORIES[0].fact)

    // Filter by "Por projeto" tab
    fireEvent.click(screen.getByRole('tab', { name: 'Por projeto' }))
    expect(screen.getByText(SAMPLE_MEMORIES[1].fact)).toBeInTheDocument()
    expect(screen.queryByText(SAMPLE_MEMORIES[2].fact)).not.toBeInTheDocument()

    // Return to "Trabalho" tab and filter by search query
    fireEvent.click(screen.getByRole('tab', { name: 'Trabalho' }))
    const searchInput = screen.getByLabelText('Buscar memórias')
    fireEvent.change(searchInput, { target: { value: 'TypeScript' } })
    expect(screen.getByText(SAMPLE_MEMORIES[2].fact)).toBeInTheDocument()
    expect(screen.queryByText(SAMPLE_MEMORIES[0].fact)).not.toBeInTheDocument()

    // Clear search and test 200-char limit & whitespace-only save prevention
    fireEvent.change(searchInput, { target: { value: '' } })
    const inspector = screen.getByTestId('memory-inspector')
    const textarea = within(inspector).getByLabelText('Texto da lembrança') as HTMLTextAreaElement

    fireEvent.change(textarea, { target: { value: '   ' } })
    expect(screen.getByTestId('memory-save-button')).toBeDisabled()

    const overLimit = 'A'.repeat(250)
    fireEvent.change(textarea, { target: { value: overLimit } })
    expect(textarea.value).toHaveLength(200)
    expect(screen.getByTestId('memory-char-counter')).toHaveTextContent('200 / 200 caracteres')
  })
})

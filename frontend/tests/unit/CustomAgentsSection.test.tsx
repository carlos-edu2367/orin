import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { CustomAgentsSection, customAgentProblems, draftOf, type AgentForm } from '../../src/features/agents/CustomAgentsSection'
import { FileMentionPicker, matchingAgents } from '../../src/features/conversations/FileMentionPicker'

afterEach(() => {
  cleanup()
})

const JSON_HEADERS = { 'Content-Type': 'application/json' }

function model(provider: string, id: string) {
  return {
    provider, model_id: id, display_name: id, context_window: null, capabilities: [], input_modalities: ['text'],
    output_modalities: ['text'], pricing: null, is_favorite: false, is_custom: false, refreshed_at: null, route_kind: 'model',
  }
}

const REVISOR = {
  name: 'revisor', description: 'Revisa o diff', prompt: 'Revise.', provider: 'openai-codex', models: ['gpt-5-codex', 'gpt-5'],
  tools: null, disallowed_tools: [], effort: null, permission_mode: null, max_turns: null, enabled: true,
}

function server(initial: unknown[] = []) {
  let agents = [...initial]
  const calls: Array<{ method: string; url: string; body: unknown }> = []
  const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : null
    calls.push({ method, url, body })
    if (url.includes('/v1/providers/openai-codex/models')) {
      return new Response(JSON.stringify({ items: [model('openai-codex', 'gpt-5-codex'), model('openai-codex', 'gpt-5')] }), { status: 200, headers: JSON_HEADERS })
    }
    if (url.includes('/v1/providers/')) return new Response(JSON.stringify({ items: [] }), { status: 200, headers: JSON_HEADERS })
    if (url.endsWith('/v1/custom-agents') && method === 'GET') {
      return new Response(JSON.stringify({ agents, limit: 100 }), { status: 200, headers: JSON_HEADERS })
    }
    if (url.endsWith('/v1/custom-agents') && method === 'POST') {
      const created = { ...REVISOR, ...(body as object), name: (body as { name: string }).name }
      agents = [...agents, created]
      return new Response(JSON.stringify({ agent: created }), { status: 201, headers: JSON_HEADERS })
    }
    if (url.includes('/v1/custom-agents/') && method === 'PUT') {
      return new Response(JSON.stringify({ error: { code: 'invalid_agent', details: { problems: ["'gpt-5' is not in the catalog of openai-codex"] } } }), { status: 422, headers: JSON_HEADERS })
    }
    return new Response('{}', { status: 404, headers: JSON_HEADERS })
  })
  return { client: new ApiClient({ fetchImpl, maxAttempts: 1 }), calls }
}

function renderSection(client: ApiClient) {
  return render(<MemoryRouter><CustomAgentsSection client={client} /></MemoryRouter>)
}

const FORM: AgentForm = {
  name: 'revisor', description: 'Revisa', prompt: 'Revise.', provider: 'openai-codex', models: ['gpt-5'], tools: '', disallowed: '',
  effort: '', permissionMode: '', maxTurns: '', enabled: true,
}

describe('customAgentProblems', () => {
  it('accepts a complete agent and refuses what the server would refuse', () => {
    expect(customAgentProblems(FORM, { creating: true })).toEqual([])
    expect(customAgentProblems({ ...FORM, name: 'explore' }, { creating: true }).join(' ')).toContain('agente do Orin')
    expect(customAgentProblems({ ...FORM, models: [] }, { creating: true }).join(' ')).toContain('pelo menos um modelo')
    expect(customAgentProblems({ ...FORM, description: ' ' }, { creating: true }).join(' ')).toContain('quando usar')
    expect(customAgentProblems({ ...FORM, tools: 'Read(*)' }, { creating: true }).join(' ')).toContain('nome de ferramenta')
  })

  it('sends no models when the agent runs on the caller’s provider', () => {
    expect(draftOf({ ...FORM, provider: '', models: ['gpt-5'] })).toMatchObject({ provider: null, models: [] })
    expect(draftOf({ ...FORM, tools: 'Read, Grep' })).toMatchObject({ provider: 'openai-codex', models: ['gpt-5'], tools: ['Read', 'Grep'] })
  })
})

describe('CustomAgentsSection', () => {
  it('lists the person’s agents with their provider and models', async () => {
    const { client } = server([REVISOR])
    renderSection(client)
    const item = await screen.findByTestId('custom-agent-revisor')
    expect(item.textContent).toContain('@revisor')
    expect(item.textContent).toContain('OpenAI Codex (Assinatura): gpt-5-codex | gpt-5')
  })

  it('creates an agent pinned to a provider’s models, the first being the default', async () => {
    const { client, calls } = server()
    renderSection(client)
    fireEvent.click(await screen.findByRole('button', { name: 'Novo agente' }))
    fireEvent.change(screen.getByLabelText('Nome'), { target: { value: 'Revisor' } })
    fireEvent.change(screen.getByLabelText('Quando usar'), { target: { value: 'Revisa o diff' } })
    fireEvent.change(screen.getByLabelText('Instruções'), { target: { value: 'Revise o diff.' } })
    const providerSelect = screen.getByLabelText('Provedor')
    await waitFor(() => expect(within(providerSelect).getByRole('option', { name: 'OpenAI Codex (Assinatura)' })).toBeInTheDocument())
    expect(within(providerSelect).queryByRole('option', { name: /Anthropic/ })).toBeNull() // lists no models: not offered
    fireEvent.change(providerSelect, { target: { value: 'openai-codex' } })
    const catalog = screen.getByRole('list', { name: 'Modelos do provedor' })
    fireEvent.click(within(catalog).getByLabelText('gpt-5'))
    fireEvent.click(within(catalog).getByLabelText('gpt-5-codex'))
    const chosen = screen.getByRole('list', { name: 'Modelos escolhidos' })
    expect(within(chosen).getAllByRole('listitem')[0].textContent).toContain('padrão')
    fireEvent.click(within(chosen).getByRole('button', { name: 'Tornar padrão' }))
    fireEvent.click(screen.getByRole('button', { name: 'Salvar' }))

    await screen.findByTestId('custom-agent-revisor')
    const post = calls.find((call) => call.method === 'POST')
    expect(post?.body).toMatchObject({ name: 'revisor', provider: 'openai-codex', models: ['gpt-5-codex', 'gpt-5'], enabled: true })
  })

  it('shows the server’s reasons when a save is refused', async () => {
    const { client } = server([REVISOR])
    renderSection(client)
    fireEvent.click(within(await screen.findByTestId('custom-agent-revisor')).getByRole('button', { name: 'Editar' }))
    expect(screen.getByLabelText('Nome')).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Salvar' }))
    expect(await screen.findByText("'gpt-5' is not in the catalog of openai-codex")).toBeInTheDocument()
  })
})

describe('@ mentions of agents', () => {
  const agents = [{ name: 'revisor', description: 'Revisa o diff' }, { name: 'pesquisador', description: 'Pesquisa' }]

  it('matches agents by name and never for a path', () => {
    expect(matchingAgents(agents, 're').map((agent) => agent.name)).toEqual(['revisor'])
    expect(matchingAgents(agents, 'or').map((agent) => agent.name)).toEqual(['pesquisador', 'revisor'])
    expect(matchingAgents(agents, 'pe').map((agent) => agent.name)).toEqual(['pesquisador'])
    expect(matchingAgents(agents, '').length).toBe(2)
    expect(matchingAgents(agents, 'src/re')).toEqual([])
  })

  it('offers the agents before the files and inserts the chosen name', () => {
    const onSelect = vi.fn()
    render(<FileMentionPicker files={['src/revisor.ts']} agents={agents} query="rev" onSelect={onSelect} onDismiss={() => {}} />)
    const options = screen.getAllByRole('option')
    expect(options[0].textContent).toContain('@revisor')
    expect(options[0].textContent).toContain('Agente')
    expect(options[1].textContent).toContain('@src/revisor.ts')
    fireEvent.mouseDown(screen.getByTestId('mention-agent-revisor'))
    expect(onSelect).toHaveBeenCalledWith('revisor')
  })
})

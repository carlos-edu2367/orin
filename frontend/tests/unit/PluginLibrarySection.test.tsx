import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { PluginLibrarySection } from '../../src/features/plugins/PluginLibrarySection'
import { ApiClient } from '../../src/api/client'

function response(body: unknown) { return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }) }

it('lists library entries and opens the install dialog pre-filled and inspected', async () => {
  const sourceUrl = 'https://github.com/acme/other-mcp.git'
  const fetchImpl = vi.fn<typeof fetch>(async (input) => {
    if (String(input).includes('/v1/plugins/inspect')) {
      return response({ plugin_id: 'other-mcp', version: '1.0.0', display_name: 'Other MCP Inspected', description: 'd', author: 'a', homepage: null, state: 'pending_approval', warnings: [], contribution_count: 1, package_digest: 'x', skills: [], mcp_servers: [], agents: [] })
    }
    return response({ entries: [{ name: 'Other MCP', description: 'd', source_url: sourceUrl, origin: 'web', installable_kind: 'plugin' }], web_search_available: true })
  })
  render(<PluginLibrarySection client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onInstalled={() => {}} />)
  expect(await screen.findByText('Other MCP')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Ver no GitHub/ })).toHaveAttribute('href', 'https://github.com/acme/other-mcp')
  fireEvent.click(screen.getByRole('button', { name: 'Instalar' }))
  expect(await screen.findByRole('heading', { name: 'Other MCP Inspected' })).toBeInTheDocument()
  expect(screen.getByDisplayValue(sourceUrl)).toBeInTheDocument()
})

it('shows a note when web search is unavailable', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({ entries: [], web_search_available: false }))
  render(<PluginLibrarySection client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onInstalled={() => {}} />)
  expect(await screen.findByText(/Busca na web indisponível/)).toBeInTheDocument()
})

it('lets the user search by a free-text query', async () => {
  const fetchImpl = vi.fn<typeof fetch>(async (input) => {
    if (String(input).includes('q=obsidian')) {
      return response({ entries: [{ name: 'obsidian-second-brain', description: 'd', source_url: 'https://github.com/acme/obsidian-second-brain.git', origin: 'web', installable_kind: 'plugin' }], web_search_available: true })
    }
    return response({ entries: [], web_search_available: true })
  })
  render(<PluginLibrarySection client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onInstalled={() => {}} />)
  expect(await screen.findByText('Nenhum plugin encontrado no momento.')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Buscar na biblioteca de plugins'), { target: { value: 'obsidian' } })
  fireEvent.click(screen.getByRole('button', { name: 'Buscar' }))
  expect(await screen.findByText('obsidian-second-brain')).toBeInTheDocument()
})

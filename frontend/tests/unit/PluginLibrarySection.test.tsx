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

it('offers "Adicionar como servidor MCP" for an mcp_raw entry instead of Instalar', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({
    entries: [{ name: 'Raw MCP', description: 'd', source_url: 'https://github.com/acme/raw-mcp.git', origin: 'web', installable_kind: 'mcp_raw' }],
    web_search_available: true,
  }))
  render(<PluginLibrarySection client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onInstalled={() => {}} />)
  expect(await screen.findByText('Raw MCP')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Adicionar como servidor MCP' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Instalar' })).not.toBeInTheDocument()
})

it('falls back to the MCP dialog when an unknown-kind entry turns out to have no manifest', async () => {
  const sourceUrl = 'https://github.com/acme/unknown-kind.git'
  const fetchImpl = vi.fn<typeof fetch>(async (input) => {
    if (String(input).includes('/v1/plugins/inspect')) {
      return new Response(JSON.stringify({ error: { code: 'plugin_no_manifest', category: 'CONFLICT', message_key: 'plugin_no_manifest', correlation_id: 'c1', retryable: false, retry_after: null } }), { status: 409, headers: { 'Content-Type': 'application/json' } })
    }
    if (String(input).includes('/v1/plugins/library/infer-mcp')) {
      return response({ display_name: 'unknown-kind', transport: 'stdio', command: 'npx', args: ['-y', 'unknown-kind'], url: null, secret_names: [], confidence: 'structured' })
    }
    return response({ entries: [{ name: 'unknown-kind', description: 'd', source_url: sourceUrl, origin: 'web', installable_kind: 'unknown' }], web_search_available: true })
  })
  render(<PluginLibrarySection client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onInstalled={() => {}} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Instalar' }))
  expect(await screen.findByRole('heading', { name: 'Adicionar como servidor MCP' })).toBeInTheDocument()
})

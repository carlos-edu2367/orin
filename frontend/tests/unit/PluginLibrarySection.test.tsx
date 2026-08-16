import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { PluginLibrarySection } from '../../src/features/plugins/PluginLibrarySection'
import { ApiClient } from '../../src/api/client'

function response(body: unknown) { return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }) }

it('lists library entries and opens the install dialog', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({ entries: [{ name: 'Other MCP', description: 'd', source_url: 'https://github.com/acme/other-mcp.git', origin: 'web' }], web_search_available: true }))
  render(<PluginLibrarySection client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onInstalled={() => {}} />)
  expect(await screen.findByText('Other MCP')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Instalar' }))
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
})

it('shows a note when web search is unavailable', async () => {
  const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({ entries: [], web_search_available: false }))
  render(<PluginLibrarySection client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onInstalled={() => {}} />)
  expect(await screen.findByText(/Busca na web indisponível/)).toBeInTheDocument()
})

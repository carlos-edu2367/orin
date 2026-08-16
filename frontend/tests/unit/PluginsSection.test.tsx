import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { PluginsSection } from '../../src/features/plugins/PluginsSection'
import { ApiClient } from '../../src/api/client'
import { MemoryRouter } from 'react-router-dom'

it('lists installed plugins and offers installation', async () => { const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify([{ plugin_id:'demo',version:'1.0.0',display_name:'Demo',description:'d',author:'a',homepage:null,state:'active',warnings:[],contribution_count:1 }]), { status:200, headers:{'Content-Type':'application/json'} })); render(<MemoryRouter><PluginsSection client={new ApiClient({fetchImpl,maxAttempts:1})} /></MemoryRouter>); expect(await screen.findByText('Demo')).toBeInTheDocument(); expect(screen.getByRole('button', { name:'Instalar plugin' })).toBeInTheDocument(); expect(screen.getByRole('tab', { name: 'Biblioteca' })).toBeInTheDocument() })

it('switches to the Biblioteca tab and hides the installed list', async () => {
  const fetchImpl = vi.fn<typeof fetch>(async (input) => {
    if (String(input).includes('/v1/plugins/library')) return new Response(JSON.stringify({ entries: [], web_search_available: true }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    return new Response(JSON.stringify([{ plugin_id: 'demo', version: '1.0.0', display_name: 'Demo', description: 'd', author: 'a', homepage: null, state: 'active', warnings: [], contribution_count: 1 }]), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  render(<MemoryRouter><PluginsSection client={new ApiClient({ fetchImpl, maxAttempts: 1 })} /></MemoryRouter>)
  expect(await screen.findByText('Demo')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('tab', { name: 'Biblioteca' }))
  expect(await screen.findByText('Nenhum plugin encontrado no momento.')).toBeInTheDocument()
  expect(screen.queryByText('Demo')).not.toBeInTheDocument()
})

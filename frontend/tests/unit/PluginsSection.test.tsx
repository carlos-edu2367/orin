import { render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { PluginsSection } from '../../src/features/plugins/PluginsSection'
import { ApiClient } from '../../src/api/client'
import { MemoryRouter } from 'react-router-dom'

it('lists installed plugins and offers installation', async () => { const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify([{ plugin_id:'demo',version:'1.0.0',display_name:'Demo',description:'d',author:'a',homepage:null,state:'active',warnings:[],contribution_count:1 }]), { status:200, headers:{'Content-Type':'application/json'} })); render(<MemoryRouter><PluginsSection client={new ApiClient({fetchImpl,maxAttempts:1})} /></MemoryRouter>); expect(await screen.findByText('Demo')).toBeInTheDocument(); expect(screen.getByRole('button', { name:'Instalar plugin' })).toBeInTheDocument() })

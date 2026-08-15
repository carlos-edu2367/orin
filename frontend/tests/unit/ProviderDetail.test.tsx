import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { ProviderDetail } from '../../src/features/providers/ProviderDetail'

describe('ProviderDetail', () => {
  it('keeps the credential field write-only and renders a provider-specific setup', () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(new Response(JSON.stringify({ provider: 'openai', enabled: true }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    const client = new ApiClient({ fetchImpl, maxAttempts: 1 })
    render(<MemoryRouter><ProviderDetail provider="openai" client={client} bootstrap={{ status: 'ready', csrfToken: 'csrf' }} onClose={() => {}} /></MemoryRouter>)
    expect(screen.getByRole('region', { name: 'OpenAI' })).toBeInTheDocument()
    expect(screen.getByLabelText('Chave de API')).toHaveValue('')
  })
})

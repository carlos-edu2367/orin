import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { ProviderGrid } from '../../src/features/providers/ProviderGrid'

const states = {
  openai: { status: 'configured' as const, detail: '42 modelos' },
  anthropic: { status: 'configured' as const, detail: '9 modelos' },
  openrouter: { status: 'unconfigured' as const, detail: '' },
  omniroute: { status: 'configured' as const, detail: 'gateway local' },
  ollama: { status: 'unavailable' as const, detail: 'não responde' },
}

function renderGrid(pathname = '/settings/providers') {
  return render(<MemoryRouter initialEntries={[pathname]}><ProviderGrid states={states} /></MemoryRouter>)
}

describe('ProviderGrid', () => {
  it('renders real providers, detail links, status words and animation indexes', () => {
    renderGrid()
    expect(screen.getAllByRole('link')).toHaveLength(5)
    const openai = screen.getByRole('link', { name: /OpenAI/ })
    expect(within(openai).getByText('42 modelos')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Anthropic/ })).toHaveAttribute('href', '/settings/providers/anthropic')
    expect(within(screen.getByRole('link', { name: /OpenRouter/ })).getByText('Não configurado')).toBeInTheDocument()
    expect(openai).toHaveStyle({ '--card-index': '0' })
  })

  it('marks the provider whose drawer route is open', () => {
    renderGrid('/settings/providers/openai')
    expect(screen.getByRole('link', { name: /OpenAI/ })).toHaveAttribute('aria-current', 'true')
  })
})

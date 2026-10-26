import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ProviderModel } from '../../../api/providers'
import { formatContextWindow, ProviderModelList } from '../ProviderModelList'

function model(model_id: string, display_name: string, output_modalities: string[], extra: Partial<ProviderModel> = {}): ProviderModel {
  return {
    provider: 'openrouter', model_id, display_name, context_window: null, capabilities: [], input_modalities: ['text'],
    output_modalities, pricing: null, is_favorite: false, is_custom: false, refreshed_at: null, route_kind: 'model', ...extra,
  }
}

const catalog = [
  model('~anthropic/claude-haiku-latest', 'Anthropic: Claude Haiku Latest', ['text'], { is_favorite: true, context_window: 200000 }),
  model('qwen/qwen-image-3', 'Qwen: Qwen Image 3', ['image']),
  model('elevenlabs/eleven-v4', 'ElevenLabs: Eleven v4', ['audio']),
  model('google/veo-3.1', 'Google: Veo 3.1', ['video']),
  model('minimaxai/minimax-m3.1-flash', 'minimaxai/minimax-m3.1-flash', ['text'], { context_window: 1048576 }),
]

function renderList() {
  return render(<ProviderModelList provider="openrouter" models={catalog} disabled={false} onToggleFavorite={vi.fn()} onRemoveCustom={vi.fn()} />)
}

function visibleIds(): string[] {
  return screen.getAllByRole('listitem').map((item) => within(item).getByText((_, el) => el?.tagName === 'CODE').textContent ?? '')
}

describe('ProviderModelList', () => {
  it('filters by output modality and shows per-filter counts', async () => {
    renderList()
    const group = screen.getByRole('group', { name: 'Filtrar por tipo de saída' })

    expect(within(group).getByRole('button', { name: /Imagem/ })).toHaveTextContent('Imagem 1')
    await userEvent.click(within(group).getByRole('button', { name: /Áudio/ }))

    expect(within(group).getByRole('button', { name: /Áudio/ })).toHaveAttribute('aria-pressed', 'true')
    expect(visibleIds()).toEqual(['elevenlabs/eleven-v4'])
    expect(screen.getByText('1 de 5 modelos')).toBeInTheDocument()

    await userEvent.click(within(group).getByRole('button', { name: /Favoritos/ }))
    expect(visibleIds()).toEqual(['~anthropic/claude-haiku-latest'])
  })

  it('searches every word across the display name and the id', async () => {
    renderList()

    await userEvent.type(screen.getByLabelText('Buscar modelo'), 'qwen image')
    expect(visibleIds()).toEqual(['qwen/qwen-image-3'])

    await userEvent.clear(screen.getByLabelText('Buscar modelo'))
    await userEvent.type(screen.getByLabelText('Buscar modelo'), 'nothing-matches')
    expect(screen.queryAllByRole('listitem')).toHaveLength(0)
    expect(screen.getByText('Nenhum modelo corresponde à busca e ao filtro.')).toBeInTheDocument()
  })

  it('shows the context window when the catalog knows it', () => {
    renderList()

    expect(screen.getByText('Contexto 1M')).toBeInTheDocument()
    expect(screen.getByText('Contexto 200k')).toBeInTheDocument()
    expect(formatContextWindow(131072)).toBe('131k')
    expect(formatContextWindow(1_500_000)).toBe('1.5M')
    expect(formatContextWindow(null)).toBeNull()
  })
})

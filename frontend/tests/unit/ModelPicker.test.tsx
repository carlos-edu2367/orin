import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ModelPicker } from '../../src/components/ModelPicker'
import type { ProviderModel } from '../../src/api/providers'

function model(overrides: Partial<ProviderModel>): ProviderModel {
  return {
    provider: 'ollama', model_id: 'm', display_name: 'M', context_window: 8192,
    capabilities: ['completion', 'tools'], input_modalities: ['text'], output_modalities: ['text'],
    pricing: null, is_favorite: false, is_custom: false, refreshed_at: null, route_kind: 'model', ...overrides,
  }
}

function picker(models: ProviderModel[]) {
  return <ModelPicker
    providers={['ollama']} provider="ollama" onProviderChange={vi.fn()}
    models={models} modelId="" onModelChange={vi.fn()}
  />
}

describe('ModelPicker tool support', () => {
  it('marks a model that cannot use tools', async () => {
    render(picker([model({ model_id: 'plain:1b', display_name: 'Plain', capabilities: ['completion'] })]))

    await userEvent.click(screen.getByRole('button', { name: /escolher modelo/i }))

    expect(screen.getByRole('option', { name: /Plain/ })).toHaveTextContent('sem ferramentas')
  })

  it('does not mark a model that supports tools', async () => {
    render(picker([model({ model_id: 'qwen3:8b', display_name: 'Qwen3' })]))

    await userEvent.click(screen.getByRole('button', { name: /escolher modelo/i }))

    expect(screen.getByRole('option', { name: /Qwen3/ })).not.toHaveTextContent('sem ferramentas')
  })

  it('ranks tool-capable models above the rest', async () => {
    render(picker([
      model({ model_id: 'plain:1b', display_name: 'Plain', capabilities: ['completion'] }),
      model({ model_id: 'qwen3:8b', display_name: 'Qwen3' }),
    ]))

    await userEvent.click(screen.getByRole('button', { name: /escolher modelo/i }))

    expect(screen.getAllByRole('option').map((item) => item.textContent)).toEqual([
      expect.stringContaining('Qwen3'),
      expect.stringContaining('Plain'),
    ])
  })

  it('leaves a provider that reports no capabilities unmarked', async () => {
    render(picker([model({ provider: 'openai', model_id: 'gpt', display_name: 'GPT', capabilities: [] })]))

    await userEvent.click(screen.getByRole('button', { name: /escolher modelo/i }))

    expect(screen.getByRole('option', { name: /GPT/ })).not.toHaveTextContent('sem ferramentas')
  })
})

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { createConversation, sendConversationMessage } from '../../../api/conversations'
import { Composer } from '../Composer'
import { clampEffort, useEffortSelection } from '../useEffortSelection'
import { acceptedEffortsForAgentModels } from '../../agents/CustomAgentsSection'
import { acceptedEffortsForModelInput } from '../../codeSettings/AgentsSection'
import type { ProviderModel } from '../../../api/providers'

function makeFetchSpy(initialByModel: Record<string, string> = {}) {
  let effortByModel: Record<string, string> = { ...initialByModel }
  const requests: Array<{ url: string; method: string; body: Record<string, unknown> | null }> = []

  const fetchImpl = vi.fn<typeof fetch>((input, init) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    const parsedBody = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : null
    requests.push({ url, method, body: parsedBody })

    if (url.includes('/v1/settings/orchestration')) {
      if (method === 'PUT' && parsedBody?.changes && typeof parsedBody.changes === 'object') {
        const nextMap = (parsedBody.changes as Record<string, unknown>).effortByModel
        if (nextMap && typeof nextMap === 'object') {
          effortByModel = { ...(nextMap as Record<string, string>) }
        } else if (nextMap === null) {
          effortByModel = {}
        }
      }
      return Promise.resolve(
        new Response(
          JSON.stringify({
            project_root: null,
            trusted: false,
            items: [
              {
                path: 'effortByModel',
                type: 'map',
                group: 'effort',
                label: 'Esforço lembrado por modelo',
                description: 'Último esforço por modelo.',
                scope: 'user_only',
                default: {},
                value: effortByModel,
                layer: Object.keys(effortByModel).length > 0 ? 'user' : 'default',
                user_value: effortByModel,
                has_user_override: Object.keys(effortByModel).length > 0,
                minimum: null,
                maximum: null,
                choices: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
                item_type: 'choice',
                value_minimum: null,
                value_maximum: null,
                locked: false,
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    }

    if (url.includes('/v1/conversations')) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            conversation_id: 'conv-1',
            title: 'Teste',
            turn_id: 'turn-1',
            message_id: 'msg-1',
            state: 'queued',
            mode: 'code',
            permission_mode: 'accept_edits',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        ),
      )
    }

    return Promise.resolve(new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } }))
  })

  return { fetchImpl, requests }
}

type TestModel = {
  provider: 'anthropic' | 'openai-codex' | 'ollama'
  modelId: string
  supportedEfforts: string[]
  defaultEffort: string | null
}

const MODELS: Record<string, TestModel> = {
  codex: {
    provider: 'openai-codex',
    modelId: 'gpt-5-codex',
    supportedEfforts: ['low', 'medium', 'high', 'xhigh', 'ultra'],
    defaultEffort: 'medium',
  },
  claude: {
    provider: 'anthropic',
    modelId: 'claude-sonnet-5-5',
    supportedEfforts: ['low', 'medium', 'high', 'xhigh', 'max'],
    defaultEffort: 'medium',
  },
  small: {
    provider: 'openai-codex',
    modelId: 'o3-mini',
    supportedEfforts: ['low', 'medium', 'high'],
    defaultEffort: 'medium',
  },
  none: {
    provider: 'ollama',
    modelId: 'llama3.2',
    supportedEfforts: [],
    defaultEffort: null,
  },
}

function Harness({
  client,
  initialModel = 'claude',
  mode = 'send',
}: {
  client: ApiClient
  initialModel?: keyof typeof MODELS
  mode?: 'send' | 'create'
}) {
  const [modelKey, setModelKey] = useState<keyof typeof MODELS>(initialModel)
  const [text, setText] = useState('')
  const active = MODELS[modelKey]
  const effort = useEffortSelection({
    client,
    provider: active.provider,
    modelId: active.modelId,
    supportedEfforts: active.supportedEfforts,
    defaultEffort: active.defaultEffort,
  })

  async function handleSubmit() {
    const payload = effort.payloadFields()
    if (mode === 'create') {
      await createConversation(client, {
        message: text,
        provider: active.provider,
        model_id: active.modelId,
        ...payload,
      })
    } else {
      await sendConversationMessage(client, 'conv-1', {
        message: text,
        selection: { provider: active.provider, model_id: active.modelId },
        ...payload,
      })
    }
    effort.consumeBoost()
    setText('')
  }

  return (
    <div>
      <div>
        {(Object.keys(MODELS) as Array<keyof typeof MODELS>).map((key) => (
          <button key={key} type="button" onClick={() => setModelKey(key)}>
            switch-{key}
          </button>
        ))}
      </div>
      <span data-testid="selected-effort">{effort.selectedEffort ?? 'auto'}</span>
      <Composer
        value={text}
        onChange={setText}
        onSubmit={() => void handleSubmit()}
        effortControl={{
          supportedEfforts: active.supportedEfforts,
          defaultEffort: active.defaultEffort,
          value: effort.selectedEffort,
          onChange: (next) => void effort.selectEffort(next),
          boost: effort.boostNextTurn,
          onToggleBoost: effort.toggleBoost,
        }}
      />
    </div>
  )
}

describe('ComposerEffort & useEffortSelection', () => {
  it('clamps unsupported effort to the highest accepted level not exceeding requested', () => {
    expect(clampEffort('ultra', ['low', 'medium', 'high', 'xhigh'])).toBe('xhigh')
    expect(clampEffort('max', ['low', 'medium', 'high'])).toBe('high')
    expect(clampEffort('low', ['medium', 'high'])).toBe('medium')
    expect(clampEffort('high', [])).toBeNull()
  })

  it('omits effort from POST when user did not explicitly choose one, and sends boost only for a single turn', async () => {
    const user = userEvent.setup()
    const { fetchImpl, requests } = makeFetchSpy()
    const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl })

    render(<Harness client={client} initialModel="claude" mode="send" />)

    // First turn without explicit effort or boost
    await user.type(screen.getByRole('textbox'), 'Oi sem esforço explícito')
    await user.click(screen.getByRole('button', { name: /enviar mensagem/i }))

    await waitFor(() => {
      const msgReqs = requests.filter((r) => r.url.endsWith('/v1/conversations/conv-1/messages'))
      expect(msgReqs).toHaveLength(1)
      expect(msgReqs[0].body).not.toHaveProperty('effort')
      expect(msgReqs[0].body).not.toHaveProperty('boost')
    })

    // Toggle "Pensar mais" -> sends boost: true once and resets automatically
    await user.click(screen.getByTestId('effort-boost-button'))
    expect(screen.getByTestId('effort-boost-button')).toHaveAttribute('aria-pressed', 'true')

    await user.type(screen.getByRole('textbox'), 'Com pensar mais')
    await user.click(screen.getByRole('button', { name: /enviar mensagem/i }))

    await waitFor(() => {
      const msgReqs = requests.filter((r) => r.url.endsWith('/v1/conversations/conv-1/messages'))
      expect(msgReqs).toHaveLength(2)
      expect(msgReqs[1].body).not.toHaveProperty('effort')
      expect(msgReqs[1].body?.boost).toBe(true)
    })

    // Boost must have reset after submit
    expect(screen.getByTestId('effort-boost-button')).toHaveAttribute('aria-pressed', 'false')

    // Next turn has neither effort nor boost
    await user.type(screen.getByRole('textbox'), 'Turno seguinte normal')
    await user.click(screen.getByRole('button', { name: /enviar mensagem/i }))

    await waitFor(() => {
      const msgReqs = requests.filter((r) => r.url.endsWith('/v1/conversations/conv-1/messages'))
      expect(msgReqs).toHaveLength(3)
      expect(msgReqs[2].body).not.toHaveProperty('boost')
    })
  })

  it('remembers effort per model, clamps when remembered level is unsupported, hides picker when model has no levels, and sends explicit effort on create', async () => {
    const user = userEvent.setup()
    const { fetchImpl, requests } = makeFetchSpy({
      'openai-codex/o3-mini': 'ultra',
    })
    const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl })

    render(<Harness client={client} initialModel="codex" mode="create" />)

    // Choose ultra on codex
    await user.click(screen.getByTestId('effort-picker-trigger'))
    await user.click(screen.getByRole('option', { name: /ultra/i }))
    expect(screen.getByTestId('selected-effort')).toHaveTextContent('ultra')

    // Persists effortByModel via orchestration settings
    await waitFor(() => {
      const putReq = requests.find((r) => r.method === 'PUT' && r.url.includes('/v1/settings/orchestration'))
      expect(putReq).toBeDefined()
      expect((putReq?.body?.changes as Record<string, unknown>)?.effortByModel).toMatchObject({
        'openai-codex/gpt-5-codex': 'ultra',
      })
    })

    // Switch to claude (no explicit effort remembered -> auto)
    await user.click(screen.getByRole('button', { name: 'switch-claude' }))
    expect(screen.getByTestId('selected-effort')).toHaveTextContent('auto')

    // Switch to small (remembered 'ultra' is not supported by o3-mini -> clamped to 'high')
    await user.click(screen.getByRole('button', { name: 'switch-small' }))
    await waitFor(() => {
      expect(screen.getByTestId('selected-effort')).toHaveTextContent('high')
    })

    // Switch to model without supported efforts -> picker hidden
    await user.click(screen.getByRole('button', { name: 'switch-none' }))
    expect(screen.queryByTestId('effort-picker-trigger')).not.toBeInTheDocument()

    // Switch back to codex -> restores 'ultra' and sends it on createConversation
    await user.click(screen.getByRole('button', { name: 'switch-codex' }))
    expect(screen.getByTestId('selected-effort')).toHaveTextContent('ultra')

    await user.type(screen.getByRole('textbox'), 'Primeira mensagem com ultra')
    await user.click(screen.getByRole('button', { name: /enviar mensagem/i }))

    await waitFor(() => {
      const createReq = requests.find((r) => r.method === 'POST' && r.url.endsWith('/v1/conversations'))
      expect(createReq).toBeDefined()
      expect(createReq?.body?.effort).toBe('ultra')
      expect(createReq?.body).not.toHaveProperty('boost')
    })
  })

  it('filters agent effort levels by selected models, including ultra for Codex and all levels when model is unspecified', () => {
    const baseModel = (overrides: Partial<ProviderModel>): ProviderModel => ({
      provider: 'openai-codex',
      model_id: 'gpt-5-codex',
      display_name: 'GPT-5 Codex',
      context_window: 200000,
      capabilities: ['tools', 'reasoning'],
      input_modalities: ['text'],
      output_modalities: ['text'],
      pricing: null,
      is_favorite: false,
      is_custom: false,
      refreshed_at: null,
      route_kind: 'model',
      supported_efforts: ['low', 'medium', 'high', 'xhigh', 'ultra'],
      default_effort: 'medium',
      ...overrides,
    })

    const codex = baseModel({})
    const claude = baseModel({
      provider: 'anthropic',
      model_id: 'claude-sonnet-5-5',
      supported_efforts: ['low', 'medium', 'high', 'xhigh', 'max'],
    })
    const plain = baseModel({
      provider: 'ollama',
      model_id: 'llama3.2',
      supported_efforts: [],
      default_effort: null,
    })

    // Unspecified model -> all valid levels including ultra
    expect(acceptedEffortsForAgentModels([codex, claude], []).levels).toEqual(['low', 'medium', 'high', 'xhigh', 'max', 'ultra'])
    // Codex model -> includes ultra, omits max
    expect(acceptedEffortsForAgentModels([codex], ['gpt-5-codex']).levels).toEqual(['low', 'medium', 'high', 'xhigh', 'ultra'])
    // Claude model -> omits ultra
    expect(acceptedEffortsForAgentModels([claude], ['claude-sonnet-5-5']).levels).toEqual(['low', 'medium', 'high', 'xhigh', 'max'])
    // Model with no supported efforts -> restrictedToEmpty
    expect(acceptedEffortsForAgentModels([plain], ['llama3.2'])).toEqual({ levels: [], restrictedToEmpty: true })

    // File-agent model input helper
    expect(acceptedEffortsForModelInput('', [codex, claude])).toEqual(['low', 'medium', 'high', 'xhigh', 'max', 'ultra'])
    expect(acceptedEffortsForModelInput('openai-codex/gpt-5-codex', [codex, claude])).toEqual(['low', 'medium', 'high', 'xhigh', 'ultra'])
    expect(acceptedEffortsForModelInput('claude-sonnet-5-5', [codex, claude])).toEqual(['low', 'medium', 'high', 'xhigh', 'max'])
    expect(acceptedEffortsForModelInput('llama3.2', [plain])).toEqual([])
  })
})

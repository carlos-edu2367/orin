import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../api/client'
import { OverviewPanel, formatUsdCost } from '../overview/OverviewPanel'
import { ProviderDetail } from '../providers/ProviderDetail'
import { formatTurnWorkDuration, toolKindLabel } from './activitySummary'
import { MarkdownMessage } from './MarkdownMessage'
import { TurnTimeline } from './TurnTimeline'
import { WorkspaceFileCard, WorkspaceFilePreview, previewKindFor } from './WorkspaceFileCard'

describe('Media generation previews & UX metrics (Turn Duration + Overview USD Cost)', () => {
  it('formatTurnWorkDuration formats seconds, minutes, and hours in Portuguese and returns null for invalid/missing timestamps', () => {
    expect(formatTurnWorkDuration(null, '2026-10-17T12:00:14Z')).toBeNull()
    expect(formatTurnWorkDuration('2026-10-17T12:00:00Z', null)).toBeNull()
    expect(formatTurnWorkDuration('invalid', '2026-10-17T12:00:14Z')).toBeNull()
    expect(formatTurnWorkDuration('2026-10-17T12:00:20Z', '2026-10-17T12:00:10Z')).toBeNull()

    // Sub-second rounds up to 1s
    expect(formatTurnWorkDuration('2026-10-17T12:00:00.000Z', '2026-10-17T12:00:00.250Z')).toBe('Trabalhou por 1s')
    // < 60s
    expect(formatTurnWorkDuration('2026-10-17T12:00:00Z', '2026-10-17T12:00:14Z')).toBe('Trabalhou por 14s')
    // Exact minutes
    expect(formatTurnWorkDuration('2026-10-17T12:00:00Z', '2026-10-17T12:02:00Z')).toBe('Trabalhou por 2m')
    // Minutes + seconds
    expect(formatTurnWorkDuration('2026-10-17T12:00:00Z', '2026-10-17T12:02:15Z')).toBe('Trabalhou por 2m 15s')
    // Hours + minutes
    expect(formatTurnWorkDuration('2026-10-17T12:00:00Z', '2026-10-17T13:05:00Z')).toBe('Trabalhou por 1h 5m')
    // Exact hours
    expect(formatTurnWorkDuration('2026-10-17T12:00:00Z', '2026-10-17T14:00:00Z')).toBe('Trabalhou por 2h')
  })

  it('TurnTimeline renders the work duration label only when turnCompleted is true and timestamps are valid', () => {
    const { rerender } = render(
      <TurnTimeline
        items={[{ id: 'txt-1', kind: 'text', content: 'Resposta final pronta.' }]}
        conversationId="conv-1"
        turnCompleted={false}
        turnStartedAt="2026-10-17T12:00:00Z"
        turnFinishedAt="2026-10-17T12:00:19Z"
      />,
    )
    expect(screen.queryByTestId('turn-work-duration')).toBeNull()

    rerender(
      <TurnTimeline
        items={[{ id: 'txt-1', kind: 'text', content: 'Resposta final pronta.' }]}
        conversationId="conv-1"
        turnCompleted={true}
        turnStartedAt="2026-10-17T12:00:00Z"
        turnFinishedAt="2026-10-17T12:00:19Z"
      />,
    )
    const textEl = screen.getByText('Resposta final pronta.')
    const durationEl = screen.getByTestId('turn-work-duration')
    expect(durationEl).toHaveTextContent('Trabalhou por 19s')
    expect(
      Boolean(textEl.compareDocumentPosition(durationEl) & Node.DOCUMENT_POSITION_FOLLOWING),
    ).toBe(true)
  })

  it('formatUsdCost formats sub-cent, standard USD, zero, and unavailable/adversarial costs', () => {
    expect(formatUsdCost(undefined)).toBe('indisponível')
    expect(
      formatUsdCost({
        input_tokens: null,
        output_tokens: null,
        total_tokens: null,
        estimated_cost_usd: null,
        usage_reported: false,
      }),
    ).toBe('indisponível')
    expect(
      formatUsdCost({
        input_tokens: 1000,
        output_tokens: 500,
        total_tokens: 1500,
        estimated_cost_usd: null,
        usage_reported: true,
      }),
    ).toBe('indisponível')
    expect(
      formatUsdCost({
        input_tokens: 1000,
        output_tokens: 500,
        total_tokens: 1500,
        estimated_cost_usd: -0.25,
        usage_reported: true,
      }),
    ).toBe('indisponível')
    expect(
      formatUsdCost({
        input_tokens: 1000,
        output_tokens: 500,
        total_tokens: 1500,
        estimated_cost_usd: Number.NaN,
        usage_reported: true,
      }),
    ).toBe('indisponível')
    expect(
      formatUsdCost({
        input_tokens: 1000,
        output_tokens: 500,
        total_tokens: 1500,
        estimated_cost_usd: Number.POSITIVE_INFINITY,
        usage_reported: true,
      }),
    ).toBe('indisponível')
    expect(
      formatUsdCost({
        input_tokens: 1000,
        output_tokens: 500,
        total_tokens: 1500,
        estimated_cost_usd: 0,
        usage_reported: true,
      }),
    ).toBe('$0.00')
    expect(
      formatUsdCost({
        input_tokens: 1000,
        output_tokens: 500,
        total_tokens: 1500,
        estimated_cost_usd: 0.0042,
        usage_reported: true,
      }),
    ).toBe('$0.0042')
    expect(
      formatUsdCost({
        input_tokens: 50000,
        output_tokens: 10000,
        total_tokens: 60000,
        estimated_cost_usd: 0.15,
        usage_reported: true,
      }),
    ).toBe('$0.15')
  })

  it('OverviewPanel displays Gasto estimado (USD) in overall stats and selected agent details', async () => {
    const user = userEvent.setup()
    const mockClient = {
      request: vi.fn().mockImplementation(async (opts: { parse: (v: unknown) => unknown }) =>
        opts.parse({
          conversation_id: 'conv-cost-1',
          title: 'Execução com Custo',
          state: 'completed',
          provider: 'openai',
          model_id: 'gpt-4o-mini',
          activity_count: 4,
          duration_seconds: 12.4,
          token_usage: {
            input_tokens: 2000,
            output_tokens: 1000,
            total_tokens: 3000,
            estimated_cost_usd: 0.0009,
            usage_reported: true,
          },
          agents: [
            {
              agent_id: 'agent:main',
              name: 'Orin',
              role: 'primary',
              parent_agent_id: null,
              provider: 'openai',
              model_id: 'gpt-4o-mini',
              state: 'completed',
              token_usage: {
                input_tokens: 2000,
                output_tokens: 1000,
                total_tokens: 3000,
                estimated_cost_usd: 0.0009,
                usage_reported: true,
              },
            },
          ],
          tools: [
            {
              tool_name: 'generate_image',
              kind: 'media',
              count: 1,
              failures: 0,
              agent_id: 'agent:main',
            },
          ],
          messages: [],
          errors: [],
          turns: [],
        }),
      ),
    } as unknown as ApiClient

    expect(toolKindLabel('media')).toBe('mídia')

    render(
      <OverviewPanel
        conversationId="conv-cost-1"
        client={mockClient}
        liveEvents={[]}
        onClose={() => undefined}
      />,
    )

    await waitFor(() => {
      expect(screen.getByTestId('overview-estimated-cost-usd')).toHaveTextContent('$0.0009')
    })
    expect(screen.getByText('mídia')).toBeInTheDocument()

    // Select the agent to inspect per-agent cost
    await user.click(screen.getByRole('button', { name: /Orin/i }))
    expect(screen.getByTestId('agent-estimated-cost-usd')).toHaveTextContent('$0.0009')
  })

  it('WorkspaceFileCard, WorkspaceFilePreview, and MarkdownMessage render inline audio, video, and image previews and reject path traversal', () => {
    expect(previewKindFor('generated/audio-1.mp3')).toBe('audio')
    expect(previewKindFor('generated/audio-2.wav')).toBe('audio')
    expect(previewKindFor('generated/audio-3.ogg')).toBe('audio')
    expect(previewKindFor('generated/video-1.mp4')).toBe('video')
    expect(previewKindFor('generated/video-2.webm')).toBe('video')
    expect(previewKindFor('generated/image-1.png')).toBe('image')

    const { unmount } = render(
      <div>
        <WorkspaceFileCard reference={{ conversationId: 'conv-1', path: 'generated/image-1.png' }} />
        <WorkspaceFileCard reference={{ conversationId: 'conv-1', path: 'generated/audio-1.mp3' }} />
        <WorkspaceFileCard reference={{ conversationId: 'conv-1', path: 'generated/video-1.mp4' }} />
        <MarkdownMessage
          conversationId="conv-1"
          content={[
            '![Banner](workspace://generated/banner.png)',
            '![Narração](workspace://generated/voice.mp3)',
            '![Clipe](workspace://generated/clip.mp4)',
            '![Traversal](workspace://../etc/passwd)',
          ].join('\n\n')}
        />
      </div>,
    )

    const images = Array.from(document.querySelectorAll('img.workspace-file-card__image')) as HTMLImageElement[]
    expect(images.map((el) => el.getAttribute('src'))).toEqual([
      '/v1/conversations/conv-1/files/generated/image-1.png?disposition=inline',
      '/v1/conversations/conv-1/files/generated/banner.png?disposition=inline',
    ])

    const audios = Array.from(document.querySelectorAll('audio.workspace-file-card__audio')) as HTMLAudioElement[]
    expect(audios.map((el) => el.getAttribute('src'))).toEqual([
      '/v1/conversations/conv-1/files/generated/audio-1.mp3?disposition=inline',
      '/v1/conversations/conv-1/files/generated/voice.mp3?disposition=inline',
    ])
    expect(audios.every((el) => el.hasAttribute('controls'))).toBe(true)

    const videos = Array.from(document.querySelectorAll('video.workspace-file-card__video')) as HTMLVideoElement[]
    expect(videos.map((el) => el.getAttribute('src'))).toEqual([
      '/v1/conversations/conv-1/files/generated/video-1.mp4?disposition=inline',
      '/v1/conversations/conv-1/files/generated/clip.mp4?disposition=inline',
    ])
    expect(videos.every((el) => el.hasAttribute('controls'))).toBe(true)

    // Path traversal must NOT be converted into a /v1/conversations/conv-1/files/... URL
    const allImgs = Array.from(document.querySelectorAll('img'))
    expect(allImgs.some((el) => (el.getAttribute('src') ?? '').includes('passwd') && (el.getAttribute('src') ?? '').includes('/v1/conversations/'))).toBe(false)

    unmount()

    render(
      <WorkspaceFilePreview
        reference={{ conversationId: 'conv-1', path: 'generated/video-1.mp4' }}
        onClose={() => undefined}
      />,
    )
    const modalVideo = document.querySelector('video.workspace-file-preview__video') as HTMLVideoElement | null
    expect(modalVideo).not.toBeNull()
    expect(modalVideo?.hasAttribute('controls')).toBe(true)
  })

  it('ProviderDetail supports adding custom models with an explicit output modality and shows modality badges', async () => {
    const user = userEvent.setup()
    const requestBodies: Array<{ path: string; method?: string; body?: unknown }> = []
    const mockClient = {
      createMutationIntent: () => ({ idempotencyKey: 'idem-1' }),
      request: vi.fn().mockImplementation(async (opts: { path: string; method?: string; body?: unknown; parse: (v: unknown) => unknown }) => {
        requestBodies.push({ path: opts.path, method: opts.method, body: opts.body })
        if (opts.path === '/v1/providers/openai' && !opts.method) {
          return opts.parse({ provider: 'openai', enabled: true })
        }
        if (opts.path === '/v1/providers/openai/keys' && !opts.method) {
          return opts.parse([])
        }
        if (opts.path === '/v1/providers/openai/models' && opts.method === 'POST') {
          return opts.parse({
            provider: 'openai',
            model_id: 'custom-flux-pro',
            display_name: 'custom-flux-pro',
            context_window: null,
            capabilities: [],
            input_modalities: ['text'],
            output_modalities: ['image'],
            pricing: null,
            is_favorite: true,
            is_custom: true,
            refreshed_at: null,
            route_kind: 'model',
          })
        }
        if (opts.path === '/v1/providers/openai/models' && !opts.method) {
          return opts.parse({
            items: [
              {
                provider: 'openai',
                model_id: 'custom-flux-pro',
                display_name: 'custom-flux-pro',
                context_window: null,
                capabilities: [],
                input_modalities: ['text'],
                output_modalities: ['image'],
                pricing: null,
                is_favorite: true,
                is_custom: true,
                refreshed_at: null,
                route_kind: 'model',
              },
            ],
          })
        }
        return opts.parse({})
      }),
    } as unknown as ApiClient

    render(
      <ProviderDetail
        provider="openai"
        client={mockClient}
        bootstrap={{ status: 'ready', csrfToken: 'csrf-123' }}
        onClose={() => undefined}
      />,
    )

    await waitFor(() => {
      expect(screen.getByLabelText(/Adicionar modelo manualmente/i)).toBeInTheDocument()
    })

    const modalitySelect = screen.getByLabelText(/Tipo de saída do modelo/i)
    await user.selectOptions(modalitySelect, 'image')
    await user.type(screen.getByLabelText(/Adicionar modelo manualmente/i), 'custom-flux-pro')
    await user.click(screen.getByRole('button', { name: /Adicionar modelo/i }))

    await waitFor(() => {
      const postCall = requestBodies.find((r) => r.path === '/v1/providers/openai/models' && r.method === 'POST')
      expect(postCall).toBeDefined()
      expect(postCall?.body).toEqual({
        model_id: 'custom-flux-pro',
        output_modalities: ['image'],
      })
    })

    await waitFor(() => {
      expect(screen.getByText('Imagem', { selector: '.provider-panel__model-badge' })).toBeInTheDocument()
    })
  })
})

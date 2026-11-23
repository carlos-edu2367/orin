import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BtwCard } from '../BtwCard'
import type { BtwSessionState } from '../useBtwSession'

function makeState(overrides: Partial<BtwSessionState> = {}): BtwSessionState {
  return {
    btwId: 'btw_1',
    question: 'Qual tabela guarda os pagamentos?',
    answer: 'A tabela se chama `billing_invoices`.',
    thinking: '',
    status: 'completed',
    provider: 'anthropic',
    modelId: 'anthropic/claude-sonnet-5-5',
    effort: 'low',
    activeTurnIncluded: true,
    contextMessages: 6,
    durationMs: 1200,
    usage: { inputTokens: 200, outputTokens: 42, cachedInputTokens: 120 },
    waiting: null,
    error: null,
    minimized: false,
    expanded: false,
    ...overrides,
  }
}

describe('BtwCard', () => {
  it('renders completed answer with metadata, active-turn badge, and quick actions', async () => {
    const copyText = vi.fn().mockResolvedValue(undefined)
    const onInsert = vi.fn()
    const onFork = vi.fn().mockResolvedValue(undefined)
    const onToggleMinimized = vi.fn()
    const onToggleExpanded = vi.fn()
    const onDismiss = vi.fn()

    render(
      <BtwCard
        state={makeState()}
        onStop={vi.fn()}
        onRetry={vi.fn()}
        onDismiss={onDismiss}
        onToggleMinimized={onToggleMinimized}
        onToggleExpanded={onToggleExpanded}
        onInsertIntoComposer={onInsert}
        onContinueInFork={onFork}
        copyText={copyText}
      />,
    )

    expect(screen.getByTestId('btw-card')).toBeInTheDocument()
    expect(screen.getByText(/inclui turno ativo/i)).toBeInTheDocument()
    expect(screen.getByText(/claude-sonnet-5-5 · 1\.2s · 42 tok/)).toBeInTheDocument()
    expect(screen.getByText('Qual tabela guarda os pagamentos?')).toBeInTheDocument()
    expect(screen.getByText('billing_invoices')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Copiar' }))
    await waitFor(() => expect(copyText).toHaveBeenCalledWith('A tabela se chama `billing_invoices`.'))
    expect(screen.getByRole('button', { name: 'Copiado' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Inserir no composer' }))
    expect(onInsert).toHaveBeenCalledWith('A tabela se chama `billing_invoices`.')

    fireEvent.click(screen.getByRole('button', { name: 'Continuar em /fork' }))
    await waitFor(() => expect(onFork).toHaveBeenCalledWith('Qual tabela guarda os pagamentos?', 'A tabela se chama `billing_invoices`.'))

    fireEvent.click(screen.getByRole('button', { name: /Expandir altura/i }))
    expect(onToggleExpanded).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: /Minimizar \/btw/i }))
    expect(onToggleMinimized).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: /Fechar \/btw/i }))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('renders streaming stop button, waiting status, error retry, and minimized pill', () => {
    const onStop = vi.fn()
    const onRetry = vi.fn()
    const onToggleMinimized = vi.fn()

    const { rerender } = render(
      <BtwCard
        state={makeState({
          status: 'waiting',
          answer: '',
          waiting: { position: 2, limit: 2, reason: 'limit' },
        })}
        onStop={onStop}
        onRetry={onRetry}
        onDismiss={vi.fn()}
        onToggleMinimized={onToggleMinimized}
        onToggleExpanded={vi.fn()}
        onInsertIntoComposer={vi.fn()}
        onContinueInFork={vi.fn()}
      />,
    )

    expect(screen.getByText(/Aguardando slot de CLI \(posição 2\)/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Parar /btw' }))
    expect(onStop).toHaveBeenCalledTimes(1)

    rerender(
      <BtwCard
        state={makeState({
          status: 'error',
          answer: '',
          error: { code: 'network_error', message: 'Falha temporária.', retryable: true },
        })}
        onStop={onStop}
        onRetry={onRetry}
        onDismiss={vi.fn()}
        onToggleMinimized={onToggleMinimized}
        onToggleExpanded={vi.fn()}
        onInsertIntoComposer={vi.fn()}
        onContinueInFork={vi.fn()}
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Falha temporária.')
    const retryButtons = screen.getAllByRole('button', { name: 'Tentar novamente' })
    fireEvent.click(retryButtons[0])
    expect(onRetry).toHaveBeenCalledTimes(1)

    rerender(
      <BtwCard
        state={makeState({ minimized: true })}
        onStop={onStop}
        onRetry={onRetry}
        onDismiss={vi.fn()}
        onToggleMinimized={onToggleMinimized}
        onToggleExpanded={vi.fn()}
        onInsertIntoComposer={vi.fn()}
        onContinueInFork={vi.fn()}
      />,
    )
    expect(screen.getByTestId('btw-minimized-pill')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Reabrir resposta paralela/i }))
    expect(onToggleMinimized).toHaveBeenCalledTimes(1)
  })

  it('prevents duplicate /fork requests on rapid double-click and opens external links in a new tab', async () => {
    const deferred: { resolve: (() => void) | null } = { resolve: null }
    const onFork = vi.fn().mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          deferred.resolve = resolve
        }),
    )

    render(
      <BtwCard
        state={makeState({
          answer: 'Veja a [documentação oficial](https://example.com/docs) para detalhes.',
        })}
        onStop={vi.fn()}
        onRetry={vi.fn()}
        onDismiss={vi.fn()}
        onToggleMinimized={vi.fn()}
        onToggleExpanded={vi.fn()}
        onInsertIntoComposer={vi.fn()}
        onContinueInFork={onFork}
      />,
    )

    const externalLink = screen.getByRole('link', { name: 'documentação oficial' })
    expect(externalLink).toHaveAttribute('href', 'https://example.com/docs')
    expect(externalLink).toHaveAttribute('target', '_blank')
    expect(externalLink).toHaveAttribute('rel', 'noopener noreferrer')

    const forkBtn = screen.getByRole('button', { name: 'Continuar em /fork' })
    fireEvent.click(forkBtn)
    fireEvent.click(forkBtn)
    expect(onFork).toHaveBeenCalledTimes(1)

    deferred.resolve?.()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continuar em /fork' })).not.toBeDisabled())
  })
})

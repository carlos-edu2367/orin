import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { Composer } from '../Composer'
import { MemoryLearnedCard } from '../MemoryLearnedCard'
import { QueuePanel } from '../QueuePanel'
import { WorkspaceFileCard } from '../WorkspaceFileCard'
import type { ConversationActivityEvent } from '../activityTypes'

describe('Phase 3 Chat Widgets (Composer, QueuePanel, MemoryLearnedCard, WorkspaceFileCard)', () => {
  describe('Composer (ref-01 & ref-03)', () => {
    it('renders the "Automático ⌄" mode pill that toggles between Automático and Orin Code while preserving legacy testids', async () => {
      const user = userEvent.setup()
      const onModeChange = vi.fn()
      const onSwitchModeChange = vi.fn()

      render(
        <MemoryRouter>
          <Composer
            value=""
            onChange={vi.fn()}
            onSubmit={vi.fn()}
            onModeChange={onModeChange}
            modeSwitch={{ mode: 'chat', onModeChange: onSwitchModeChange }}
            sessionContext={{
              mode: 'chat',
              permissionMode: 'ask_permissions',
              onPermissionModeChange: vi.fn(),
              workspaceState: {
                kind: 'local',
                path: 'C:/projetos/orin',
                folderName: 'orin',
                scope: 'chat',
                projectName: null,
              },
            }}
          />
        </MemoryRouter>,
      )

      const autoPill = screen.getByTestId('composer-auto-mode-pill')
      expect(autoPill).toHaveTextContent(/Automático\s*⌄/)

      // Legacy testids are preserved
      expect(screen.getByTestId('home-mode-toggle')).toBeInTheDocument()
      expect(screen.getByTestId('home-mode-chat')).toBeInTheDocument()
      expect(screen.getByTestId('home-mode-code')).toBeInTheDocument()
      expect(screen.getByTestId('code-permission-mode-pill')).toBeInTheDocument()
      expect(screen.getByTestId('session-context-trigger')).toBeInTheDocument()

      await user.click(autoPill)
      expect(autoPill).toHaveTextContent(/Orin Code\s*⌄/)
      expect(onModeChange).toHaveBeenCalledWith('code')
      expect(onSwitchModeChange).toHaveBeenCalledWith('code')

      await user.click(autoPill)
      expect(autoPill).toHaveTextContent(/Automático\s*⌄/)
      expect(onModeChange).toHaveBeenLastCalledWith('chat')
      expect(onSwitchModeChange).toHaveBeenLastCalledWith('chat')
    })

    it('renders circular voice dictation button and paper-plane send icon while preserving live voice toggle', async () => {
      const user = userEvent.setup()
      const onToggleLiveVoice = vi.fn()
      const voiceEventSpy = vi.fn()
      window.addEventListener('orin:open-friend-voice', voiceEventSpy)

      try {
        const { rerender } = render(
          <Composer
            value="Olá Orin"
            onChange={vi.fn()}
            onSubmit={vi.fn()}
            onToggleLiveVoice={onToggleLiveVoice}
          />,
        )

        const dictationBtn = screen.getByTestId('composer-voice-dictation-button')
        const liveToggleBtn = screen.getByTestId('composer-live-voice-toggle')
        const sendBtn = screen.getByRole('button', { name: 'Enviar mensagem' })
        const sendIcon = screen.getByTestId('composer-send-icon')

        expect(dictationBtn).toBeInTheDocument()
        expect(liveToggleBtn).toBeInTheDocument()
        expect(sendBtn).toContainElement(sendIcon)
        expect(sendIcon.tagName.toLowerCase()).toBe('svg')

        await user.click(dictationBtn)
        expect(onToggleLiveVoice).toHaveBeenCalledTimes(1)

        // Without onToggleLiveVoice, clicking dispatches orin:open-friend-voice
        rerender(<Composer value="Olá Orin" onChange={vi.fn()} onSubmit={vi.fn()} />)
        await user.click(screen.getByTestId('composer-voice-dictation-button'))
        expect(voiceEventSpy).toHaveBeenCalledTimes(1)
      } finally {
        window.removeEventListener('orin:open-friend-voice', voiceEventSpy)
      }
    })
  })

  describe('QueuePanel (ref-03)', () => {
    const item = { queueId: 'q1', content: 'priorize ações de baixo custo', createdAt: null }

    it('renders compact-queue-bar with "Na sequência:", edit button, send now, and remove buttons', () => {
      const onEdit = vi.fn()
      const onSendNow = vi.fn()
      const onRemove = vi.fn()

      render(
        <QueuePanel
          items={[item]}
          onEdit={onEdit}
          onSendNow={onSendNow}
          onRemove={onRemove}
        />,
      )

      const bar = screen.getByTestId('compact-queue-bar')
      expect(bar).toHaveTextContent('Na sequência:')
      expect(bar).toHaveTextContent('priorize ações de baixo custo')

      const editBtn = screen.getByTestId('queue-edit-button-q1')
      expect(editBtn).toHaveAttribute('aria-label', 'Editar da fila: priorize ações de baixo custo')
      fireEvent.click(editBtn)
      expect(onEdit).toHaveBeenCalledWith(item)

      const sendNowBtn = screen.getByRole('button', { name: /Enviar agora/i })
      fireEvent.click(sendNowBtn)
      expect(onSendNow).toHaveBeenCalledWith(item)

      const removeBtn = screen.getByRole('button', { name: 'Remover da fila: priorize ações de baixo custo' })
      fireEvent.click(removeBtn)
      expect(onRemove).toHaveBeenCalledWith('q1')
    })

    it('disables Editar, Enviar agora, and Remover when busy={true} (Red-Team race condition protection)', () => {
      const onEdit = vi.fn()
      const onSendNow = vi.fn()
      const onRemove = vi.fn()

      render(
        <QueuePanel
          items={[item]}
          busy={true}
          onEdit={onEdit}
          onSendNow={onSendNow}
          onRemove={onRemove}
        />,
      )

      const editBtn = screen.getByTestId('queue-edit-button-q1')
      const sendNowBtn = screen.getByRole('button', { name: /Enviar agora/i })
      const removeBtn = screen.getByRole('button', { name: 'Remover da fila: priorize ações de baixo custo' })

      expect(editBtn).toBeDisabled()
      expect(sendNowBtn).toBeDisabled()
      expect(removeBtn).toBeDisabled()

      fireEvent.click(editBtn)
      fireEvent.click(sendNowBtn)
      fireEvent.click(removeBtn)

      expect(onEdit).not.toHaveBeenCalled()
      expect(onSendNow).not.toHaveBeenCalled()
      expect(onRemove).not.toHaveBeenCalled()
    })
  })

  describe('MemoryLearnedCard (ref-03)', () => {
    it('displays "Memória atualizada ·" prefix with event summary and Desfazer button, switching to "Memória descartada" when undone and preventing double-undo race conditions', async () => {
      const originalFetch = globalThis.fetch
      const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
        new Response(null, { status: 204 }),
      )
      globalThis.fetch = fetchMock

      const memoryEvent: ConversationActivityEvent = {
        eventId: 'evt-mem-1',
        cursor: 'a.1',
        type: 'memory.learned',
        kind: 'lifecycle',
        state: 'completed',
        agentId: 'agent:main',
        summary: 'Prefere respostas curtas',
        memoryId: 'mem-123',
        memoryScope: 'user',
      }

      try {
        render(<MemoryLearnedCard event={memoryEvent} />)

        expect(screen.getByTestId('memory-learned-prefix')).toHaveTextContent('Memória atualizada ·')
        expect(screen.getByText('Prefere respostas curtas')).toBeInTheDocument()

        const undoBtn = screen.getByRole('button', { name: 'Desfazer' })
        // Double click rapidly (Red-Team race condition check)
        fireEvent.click(undoBtn)
        fireEvent.click(undoBtn)
        expect(fetchMock).toHaveBeenCalledTimes(1)

        await waitFor(() => {
          expect(screen.getByText('Memória descartada')).toBeInTheDocument()
        })
        expect(screen.queryByTestId('memory-learned-prefix')).not.toBeInTheDocument()
      } finally {
        globalThis.fetch = originalFetch
      }
    })
  })

  describe('WorkspaceFileCard (ref-03)', () => {
    it('renders article.workspace-file-card with data-deliverable="true" and "Abrir ↗" action label', () => {
      const onPreview = vi.fn()
      const ref = { conversationId: 'chat_1', path: 'docs/Plano de ação.md', name: 'Plano de ação.md' }

      const { container } = render(<WorkspaceFileCard reference={ref} onPreview={onPreview} />)

      const article = container.querySelector('article.workspace-file-card')
      expect(article).not.toBeNull()
      expect(article).toHaveAttribute('data-deliverable', 'true')

      const openPreviewBtn = screen.getByRole('button', { name: 'Visualizar Plano de ação.md' })
      expect(openPreviewBtn).toHaveTextContent('Abrir ↗')
      fireEvent.click(openPreviewBtn)
      expect(onPreview).toHaveBeenCalledWith(ref)
    })
  })
})

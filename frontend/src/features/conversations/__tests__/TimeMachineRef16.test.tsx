import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RewindPicker } from '../RewindPicker'

describe('RewindPicker — Time Machine 3-Column Layout (ref-16.png)', () => {
  const messages = [
    { messageId: 'm1', content: 'Antes das alterações', createdAt: 'Hoje, 14:05' },
    { messageId: 'm2', content: 'Página inicial criada', createdAt: 'Hoje, 14:20' },
    { messageId: 'm3', content: 'Ajustes na navegação', createdAt: 'Agora' },
  ]

  const history = [
    {
      messageId: 'm2',
      turnId: 't2',
      kind: 'message',
      createdAt: 'Hoje, 14:20',
      rewoundAt: null,
      files: ['src/App.tsx', 'src/components/Header.tsx', 'src/styles.css'],
    },
    {
      messageId: 'm3',
      turnId: 't3',
      kind: 'message',
      createdAt: 'Agora',
      rewoundAt: null,
      files: ['src/App.tsx', 'src/nav.tsx'],
    },
  ]

  it('renders ref-16 header with breadcrumb, back button, headline, and accessible dialog title without a Router', () => {
    const onClose = vi.fn()
    render(
      <RewindPicker
        messages={messages}
        history={history}
        projectName="Auryly"
        initialMessageId="m2"
        onRewind={vi.fn()}
        onClose={onClose}
      />,
    )

    expect(screen.getByRole('dialog', { name: 'Voltar a uma mensagem' })).toBeInTheDocument()
    expect(screen.getByTestId('history-breadcrumb')).toHaveTextContent('Auryly / Histórico')
    expect(screen.getByTestId('history-headline')).toHaveTextContent('Volte a um ponto anterior')
    expect(screen.getByText('Revise o que mudou antes de restaurar.')).toBeInTheDocument()

    const backButton = screen.getByTestId('history-back-button')
    expect(backButton).toHaveTextContent('← Voltar ao projeto')
    fireEvent.click(backButton)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('renders Column 1 (history-timeline-column) with checkpoints, timestamps, node indicators, and file counts', () => {
    render(
      <RewindPicker
        messages={messages}
        history={history}
        projectName="Auryly"
        initialMessageId="m2"
        onRewind={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    const timelineCol = screen.getByTestId('history-timeline-column')
    expect(within(timelineCol).getByText('Histórico do projeto')).toBeInTheDocument()

    const options = screen.getAllByRole('option')
    expect(options).toHaveLength(3)

    // Newest first: m3 (index 0), m2 (index 1, selected), m1 (index 2)
    expect(options[0]).toHaveAttribute('id', 'rewind-m3')
    expect(options[0]).toHaveAttribute('aria-selected', 'false')
    expect(options[0]).toHaveTextContent('○')
    expect(options[0]).toHaveTextContent('Agora')
    expect(options[0]).toHaveTextContent('Ajustes na navegação')
    expect(options[0]).toHaveTextContent('2 arquivos')

    expect(options[1]).toHaveAttribute('id', 'rewind-m2')
    expect(options[1]).toHaveAttribute('aria-selected', 'true')
    expect(options[1]).toHaveTextContent('◉')
    expect(options[1]).toHaveTextContent('Hoje, 14:20')
    expect(options[1]).toHaveTextContent('Página inicial criada')
    expect(options[1]).toHaveTextContent('3 arquivos')

    expect(options[2]).toHaveAttribute('id', 'rewind-m1')
    expect(options[2]).toHaveAttribute('aria-selected', 'false')
    expect(options[2]).toHaveTextContent('○')
    expect(options[2]).toHaveTextContent('Hoje, 14:05')
    expect(options[2]).toHaveTextContent('Antes das alterações')
    expect(options[2]).toHaveTextContent('Estado inicial')
  })

  it('renders Column 2 (history-diff-column) with accordion file cards, Modificado badges, and unified diff lines', () => {
    render(
      <RewindPicker
        messages={messages}
        history={history}
        projectName="Auryly"
        initialMessageId="m2"
        filePatches={{
          'src/App.tsx': [
            {
              oldStart: 12,
              oldLines: 4,
              newStart: 12,
              newLines: 4,
              lines: [
                '   return (',
                '     <div className="app">',
                '-      <OldHome />',
                '+      <Home />',
                '     </div>',
              ],
            },
          ],
        }}
        onRewind={vi.fn()}
        onClose={vi.fn()}
      />,
    )

    const diffCol = screen.getByTestId('history-diff-column')
    expect(within(diffCol).getByText('Página inicial criada')).toBeInTheDocument()
    expect(within(diffCol).getByText(/4 arquivos neste ponto/)).toBeInTheDocument()

    const fileCards = within(diffCol).getAllByTestId('history-file-card')
    expect(fileCards).toHaveLength(4)
    expect(within(fileCards[0]).getByText('src/App.tsx')).toBeInTheDocument()
    expect(within(fileCards[0]).getByText('Modificado')).toBeInTheDocument()
    expect(within(fileCards[0]).getByText(/-\s+<OldHome \/>/)).toBeInTheDocument()
    expect(within(fileCards[0]).getByText(/\+\s+<Home \/>/)).toBeInTheDocument()

    // Synthesized diff also renders for files without explicit filePatches entry
    expect(within(fileCards[1]).getByText('src/components/Header.tsx')).toBeInTheDocument()
    expect(within(fileCards[1]).getByText('Modificado')).toBeInTheDocument()
    expect(within(fileCards[1]).getByText(/<NavigationOld \/>/)).toBeInTheDocument()

    // Accordion toggle collapses and re-expands the file diff
    const toggleBtn = within(fileCards[0]).getByRole('button')
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(toggleBtn)
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'false')
    expect(within(fileCards[0]).queryByText(/-\s+<OldHome \/>/)).toBeNull()
    fireEvent.click(toggleBtn)
    expect(toggleBtn).toHaveAttribute('aria-expanded', 'true')
    expect(within(fileCards[0]).getByText(/-\s+<OldHome \/>/)).toBeInTheDocument()
  })

  it('renders Column 3 (history-confirm-column) with restoration summary, safety notice, and primary restore action', () => {
    const onRewind = vi.fn()
    const onClose = vi.fn()
    const { rerender } = render(
      <RewindPicker
        messages={messages}
        history={history}
        projectName="Auryly"
        initialMessageId="m2"
        onRewind={onRewind}
        onClose={onClose}
      />,
    )

    const confirmCol = screen.getByTestId('history-confirm-column')
    expect(within(confirmCol).getByText('O que será restaurado')).toBeInTheDocument()
    expect(within(confirmCol).getByText('Arquivos da pasta do projeto')).toBeInTheDocument()
    expect(within(confirmCol).getByText('Todos os arquivos serão restaurados para o estado deste ponto.')).toBeInTheDocument()
    expect(within(confirmCol).getByText('Conversa até este ponto')).toBeInTheDocument()
    expect(within(confirmCol).getByText('O histórico da conversa também será restaurado.')).toBeInTheDocument()
    expect(within(confirmCol).getByText('Publicações e ações em serviços externos não são desfeitas.')).toBeInTheDocument()

    const restoreBtn = screen.getByTestId('history-restore-point-button')
    expect(restoreBtn).toHaveTextContent('Restaurar este ponto')
    fireEvent.click(restoreBtn)
    expect(onRewind).toHaveBeenCalledWith('m2', 'both')

    // When no files changed, Restaurar este ponto restores conversation only
    rerender(
      <RewindPicker
        messages={messages}
        history={[]}
        projectName="Auryly"
        initialMessageId="m2"
        onRewind={onRewind}
        onClose={onClose}
      />,
    )
    fireEvent.click(screen.getByTestId('history-restore-point-button'))
    expect(onRewind).toHaveBeenLastCalledWith('m2', 'conversation')
  })

  it('does not use forbidden dark-only utility classes (text-white*, bg-black*, border-white*)', () => {
    const source = readFileSync(resolve(__dirname, '../RewindPicker.tsx'), 'utf-8')
    expect(source).not.toMatch(/\b(text-white|bg-black|border-white)\b/)
  })
})

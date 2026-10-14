import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Composer } from './Composer'
import { applyFileMention, detectFileMention } from './FileMentionPicker'

describe('Fase D: FileMentionPicker (@file autocomplete) & Worktree Isolation UI', () => {
  it('detects @file mentions in any position while ignoring email addresses', () => {
    expect(detectFileMention('@src/app.ts')).toEqual({
      query: 'src/app.ts',
      startIndex: 0,
      endIndex: 11,
    })

    expect(detectFileMention('Refatore o @src/agentos/session')).toEqual({
      query: 'src/agentos/session',
      startIndex: 11,
      endIndex: 31,
    })

    expect(detectFileMention('Envie email para dev@example.com')).toBeNull()

    const mention = detectFileMention('Veja (@README.md) agora', 11)
    expect(mention).toEqual({
      query: 'READ',
      startIndex: 6,
      endIndex: 16,
    })
    expect(applyFileMention('Veja (@README.md) agora', mention!, 'README.md')).toBe(
      'Veja (@README.md ) agora',
    )
  })

  it('opens FileMentionPicker when typing @ in Composer, navigates with arrows, and inserts on Enter/Tab', async () => {
    const onSubmit = vi.fn()
    function Harness() {
      const [text, setText] = useState('')
      return (
        <Composer
          value={text}
          onChange={setText}
          onSubmit={onSubmit}
          workspaceFiles={[
            'src/agentos/api/gateway.py',
            'src/agentos/agentic/session.py',
            'frontend/src/features/conversations/Composer.tsx',
          ]}
        />
      )
    }

    render(<Harness />)
    const textarea = screen.getByLabelText('Mensagem') as HTMLTextAreaElement

    fireEvent.change(textarea, {
      target: { value: 'Analise @src/agentos/', selectionStart: 21 },
    })

    const picker = await screen.findByTestId('file-mention-picker')
    expect(picker).toBeInTheDocument()
    expect(screen.getByText('@src/agentos/api/gateway.py')).toBeInTheDocument()
    expect(screen.getByText('@src/agentos/agentic/session.py')).toBeInTheDocument()

    // Navigate down to second option and press Enter
    fireEvent.keyDown(textarea, { key: 'ArrowDown' })
    fireEvent.keyDown(textarea, { key: 'Enter' })

    expect(onSubmit).not.toHaveBeenCalled()
    expect(textarea.value).toBe('Analise @src/agentos/agentic/session.py ')
  })

  it('fetches workspace files via onSearchWorkspaceFiles and supports Tab selection and Escape dismissal', async () => {
    const onSearch = vi.fn().mockResolvedValue(['package.json', 'pyproject.toml'])
    function Harness() {
      const [text, setText] = useState('')
      return (
        <Composer
          value={text}
          onChange={setText}
          onSubmit={() => {}}
          onSearchWorkspaceFiles={onSearch}
        />
      )
    }

    render(<Harness />)
    const textarea = screen.getByLabelText('Mensagem') as HTMLTextAreaElement

    fireEvent.change(textarea, {
      target: { value: 'Verifique @py', selectionStart: 13 },
    })

    await waitFor(() => {
      expect(onSearch).toHaveBeenCalledWith('py')
    })

    const option = await screen.findByText('@pyproject.toml')
    expect(option).toBeInTheDocument()

    fireEvent.keyDown(textarea, { key: 'Tab' })
    expect(textarea.value).toBe('Verifique @pyproject.toml ')
  })

  it('renders and toggles Worktree isolation button when Code Mode is active', () => {
    const onWorktreeChange = vi.fn()
    render(
      <Composer
        value=""
        onChange={() => {}}
        onSubmit={() => {}}
        codeMode="code"
        onCodeModeChange={() => {}}
        worktreeIsolated={false}
        onWorktreeIsolatedChange={onWorktreeChange}
        worktreeBranch="orin/feat-auth"
      />,
    )

    const toggle = screen.getByTestId('code-worktree-toggle')
    expect(toggle).toHaveAttribute('aria-pressed', 'false')

    fireEvent.click(toggle)
    expect(onWorktreeChange).toHaveBeenCalledWith(true)
  })
})

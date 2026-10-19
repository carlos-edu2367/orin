import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { Composer } from '../../src/features/conversations/Composer'

const COMMANDS = [
  { command_id: 'demo:daily', slug: 'daily', plugin_id: 'demo', description: 'Nota diária', argument_hint: '[data]', qualified: false },
]

const SKILLS = [
  { id: 'safe-review', name: 'Safe review', description: 'Revisa alterações antes de aprová-las.', version: '1.0.0', tags: [], source: 'system', available: true },
]

describe('Composer command picker', () => {
  it('opens the picker when / starts an empty composer', async () => {
    render(<Composer value="/" onChange={() => {}} onSubmit={() => {}} commands={COMMANDS} />)

    expect(await screen.findByRole('listbox', { name: /Comandos e skills/ })).toBeInTheDocument()
  })

  it('does not open the picker for a slash inside existing text', () => {
    render(<Composer value="veja /usr/local" onChange={() => {}} onSubmit={() => {}} commands={COMMANDS} />)

    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('keeps Enter-to-send working when the picker is closed', async () => {
    const onSubmit = vi.fn()
    render(<Composer value="olá" onChange={() => {}} onSubmit={onSubmit} commands={COMMANDS} />)

    await userEvent.type(screen.getByLabelText('Mensagem'), '{Enter}')

    expect(onSubmit).toHaveBeenCalled()
  })

  it('does not send when Enter picks a command', async () => {
    const onSubmit = vi.fn()
    const onChange = vi.fn()
    render(<Composer value="/dai" onChange={onChange} onSubmit={onSubmit} commands={COMMANDS} />)

    await userEvent.keyboard('{Enter}')

    expect(onSubmit).not.toHaveBeenCalled()
    expect(onChange).toHaveBeenCalledWith('/daily ')
  })

  it('adds a selected skill as a slash invocation', async () => {
    const onChange = vi.fn()
    render(<Composer value="/safe" onChange={onChange} onSubmit={() => {}} skills={SKILLS} />)

    await userEvent.keyboard('{Enter}')

    expect(onChange).toHaveBeenCalledWith('/safe-review ')
  })

  it('lets the user explicitly toggle Code mode with an announced pressed state', async () => {
    const onCodeModeChange = vi.fn()
    const user = userEvent.setup()
    render(<Composer value="implemente isto" onChange={() => {}} onSubmit={() => {}} codeMode="auto" onCodeModeChange={onCodeModeChange} />)

    const toggle = screen.getByRole('button', { name: 'Ativar Modo Code' })
    expect(toggle).toHaveAttribute('aria-pressed', 'false')
    await user.click(toggle)

    expect(onCodeModeChange).toHaveBeenCalledWith('code')
  })

  it('hides the Chat/Code mode toggle mid-conversation when hideCodeModeToggle or conversationMode is set', () => {
    render(
      <Composer
        value=""
        onChange={() => {}}
        onSubmit={() => {}}
        codeMode="code"
        conversationMode="code"
        hideCodeModeToggle={true}
        onCodeModeChange={() => {}}
      />,
    )

    expect(screen.queryByRole('button', { name: /Modo Code/i })).not.toBeInTheDocument()
  })

  it('renders TodoBar directly above the Composer surface when todos are provided', () => {
    const { container } = render(
      <Composer
        value=""
        onChange={() => {}}
        onSubmit={() => {}}
        conversationMode="code"
        todos={[
          { content: 'Ler TurnTimeline.tsx', status: 'completed', activeForm: 'Lendo TurnTimeline.tsx' },
          { content: 'Integrar TodoBar no Composer', status: 'in_progress', activeForm: 'Integrando TodoBar no Composer' },
        ]}
      />,
    )

    const todoBar = screen.getByTestId('todo-bar')
    const surface = container.querySelector('.composer__surface')!
    expect(todoBar).toBeInTheDocument()
    expect(todoBar.compareDocumentPosition(surface) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('renders localized permission mode pill in both code and chat modes and cycles through all 4 modes including full_autonomy', async () => {
    const onPermissionChange = vi.fn()
    const user = userEvent.setup()
    render(
      <Composer
        value=""
        onChange={() => {}}
        onSubmit={() => {}}
        conversationMode="code"
        hideCodeModeToggle={true}
        onCodePermissionModeChange={onPermissionChange}
      />,
    )

    const pill = screen.getByTestId('code-permission-mode-pill')
    expect(pill).toHaveTextContent('⏵⏵ Aceitar edições')
    expect(pill).toHaveAttribute('title', 'Modo de permissão: Aceitar edições (Shift+Tab para alternar)')

    const textarea = screen.getByLabelText('Mensagem')
    textarea.focus()

    await user.keyboard('{Shift>}{Tab}{/Shift}')
    expect(pill).toHaveTextContent('⏸ Modo Plano')
    expect(onPermissionChange).toHaveBeenLastCalledWith('plan_mode')

    await user.keyboard('{Shift>}{Tab}{/Shift}')
    expect(pill).toHaveTextContent('🔒 Pedir permissão')
    expect(onPermissionChange).toHaveBeenLastCalledWith('ask_permissions')

    await user.keyboard('{Shift>}{Tab}{/Shift}')
    expect(pill).toHaveTextContent('⚡ Sem restrições')
    expect(onPermissionChange).toHaveBeenLastCalledWith('full_autonomy')

    await user.keyboard('{Shift>}{Tab}{/Shift}')
    expect(pill).toHaveTextContent('⏵⏵ Aceitar edições')
    expect(onPermissionChange).toHaveBeenLastCalledWith('accept_edits')

    await user.click(pill)
    expect(pill).toHaveTextContent('⏸ Modo Plano')
    expect(onPermissionChange).toHaveBeenLastCalledWith('plan_mode')
  })

  it('keeps the Send button visually dimmed (opacity 0.32) when disabled and full opacity when enabled', async () => {
    const { rerender } = render(<Composer value="" onChange={() => {}} onSubmit={() => {}} />)
    const sendButton = screen.getByRole('button', { name: 'Enviar mensagem' })
    expect(sendButton).toBeDisabled()
    expect(sendButton.style.opacity).toBe('0.32')

    rerender(<Composer value="Pronto para enviar" onChange={() => {}} onSubmit={() => {}} />)
    expect(sendButton).not.toBeDisabled()
    await vi.waitFor(() => {
      expect(sendButton.style.opacity).toBe('1')
    })
  })

  it('calls onStop when Escape is pressed inside the textarea while running and no picker is open', async () => {
    const onStop = vi.fn()
    const user = userEvent.setup()
    render(<Composer value="em andamento" onChange={() => {}} onSubmit={() => {}} running={true} onStop={onStop} />)

    const textarea = screen.getByLabelText('Mensagem')
    textarea.focus()
    await user.keyboard('{Escape}')

    expect(onStop).toHaveBeenCalledTimes(1)
  })
})


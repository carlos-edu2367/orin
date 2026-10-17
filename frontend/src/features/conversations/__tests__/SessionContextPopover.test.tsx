import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ModelPicker } from '../../../components/ModelPicker'
import { Composer } from '../Composer'
import { SessionContextPopover } from '../SessionContextPopover'

describe('SessionContextPopover', () => {
  it('renders a compact summary chip and opens unified popover with workspace, permission, worktree, and project controls', async () => {
    const user = userEvent.setup()
    const onPermissionChange = vi.fn()
    const onWorktreeChange = vi.fn()
    const onMoveToProject = vi.fn()
    const onCreateProjectFromSession = vi.fn()

    render(
      <MemoryRouter>
        <SessionContextPopover
          mode="code"
          permissionMode="ask_permissions"
          onPermissionModeChange={onPermissionChange}
          worktreeIsolated={false}
          onWorktreeIsolatedChange={onWorktreeChange}
          worktreeBranch={null}
          workspaceState={{
            kind: 'local',
            path: 'C:/projetos/orin',
            folderName: 'orin',
            scope: 'chat',
            projectName: null,
          }}
          onInspectWorkspace={vi.fn()}
          onAttachWorkspace={vi.fn()}
          onDetachWorkspace={vi.fn()}
          onWorkspaceChange={vi.fn()}
          projects={[{ project_id: 'proj_1', name: 'Orin Core', description: '' }]}
          activeProjectId={null}
          activeProjectName={null}
          onMoveToProject={onMoveToProject}
          onCreateProjectFromSession={onCreateProjectFromSession}
        />
      </MemoryRouter>,
    )

    // Summary pill shows folder name and permission mode
    expect(screen.getByTestId('session-context-trigger')).toHaveTextContent('orin')
    expect(screen.getByTestId('code-permission-mode-pill')).toHaveTextContent('🔒 Pedir permissão')

    // Change permission mode via hidden select
    fireEvent.change(screen.getByTestId('code-permission-mode-select'), {
      target: { value: 'plan_mode' },
    })
    expect(onPermissionChange).toHaveBeenCalledWith('plan_mode')

    // Open popover
    await user.click(screen.getByTestId('session-context-trigger'))
    expect(screen.getByRole('dialog', { name: /Contexto da sessão/i })).toBeInTheDocument()

    // Change permission mode inside popover
    await user.click(screen.getByRole('button', { name: /Aceitar edições/i }))
    expect(onPermissionChange).toHaveBeenCalledWith('accept_edits')

    // Toggle worktree inside popover
    await user.click(screen.getByTestId('code-worktree-toggle'))
    expect(onWorktreeChange).toHaveBeenCalledWith(true)

    // Move to project inside popover
    await user.click(screen.getByRole('button', { name: 'Orin Core' }))
    expect(onMoveToProject).toHaveBeenCalledWith('proj_1')

    // Create a new project from the session form
    const form = screen.getByTestId('create-project-from-session-form')
    const nameInput = form.querySelector('input[name="project-name"]') as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'Pagamentos Core' } })
    fireEvent.submit(form)

    await waitFor(() => {
      expect(onCreateProjectFromSession).toHaveBeenCalledWith({
        name: 'Pagamentos Core',
        description: '',
      })
    })
  })

  it('shows active project badge and allows removing session from project', async () => {
    const user = userEvent.setup()
    const onMoveToProject = vi.fn()

    render(
      <MemoryRouter>
        <SessionContextPopover
          mode="chat"
          workspaceState={{
            kind: 'managed',
            path: null,
            folderName: null,
            scope: 'project',
            projectName: 'Orin Core',
          }}
          projects={[{ project_id: 'proj_1', name: 'Orin Core', description: '' }]}
          activeProjectId="proj_1"
          activeProjectName="Orin Core"
          onMoveToProject={onMoveToProject}
        />
      </MemoryRouter>,
    )

    await user.click(screen.getByTestId('session-context-trigger'))
    const removeBtn = screen.getByTestId('remove-from-project-button')
    await user.click(removeBtn)
    expect(onMoveToProject).toHaveBeenCalledWith(null)
  })

  it('closes on Escape and calls stopPropagation so parent listeners do not fire (adversarial)', async () => {
    const user = userEvent.setup()
    const parentKeyDown = vi.fn()

    render(
      <MemoryRouter>
        <div onKeyDown={parentKeyDown}>
          <SessionContextPopover
            mode="code"
            permissionMode="ask_permissions"
            onPermissionModeChange={vi.fn()}
            workspaceState={{
              kind: 'local',
              path: 'C:/projetos/orin',
              folderName: 'orin',
              scope: 'chat',
              projectName: null,
            }}
          />
        </div>
      </MemoryRouter>,
    )

    await user.click(screen.getByTestId('session-context-trigger'))
    expect(screen.getByRole('dialog', { name: /Contexto da sessão/i })).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: /Contexto da sessão/i })).not.toBeInTheDocument()
    expect(parentKeyDown).not.toHaveBeenCalled()
  })

  it('integrates modeSwitch and sessionContext inside Composer and stops Escape propagation in unified ModelPicker', async () => {
    const user = userEvent.setup()
    const onModeChange = vi.fn()
    const onPermissionChange = vi.fn()
    const parentKeyDown = vi.fn()

    render(
      <MemoryRouter>
        <div onKeyDown={parentKeyDown}>
          <ModelPicker
            variant="unified"
            providers={['openrouter', 'ollama']}
            provider="openrouter"
            onProviderChange={vi.fn()}
            models={[
              {
                provider: 'openrouter',
                model_id: 'claude-sonnet-4-5',
                display_name: 'Claude Sonnet 4.5',
                context_window: 200000,
                capabilities: ['tools'],
                input_modalities: ['text'],
                output_modalities: ['text'],
                pricing: null,
                is_favorite: true,
                is_custom: false,
                refreshed_at: null,
                route_kind: 'model',
              },
            ]}
            modelId="claude-sonnet-4-5"
            onModelChange={vi.fn()}
          />
          <Composer
            value=""
            onChange={vi.fn()}
            onSubmit={vi.fn()}
            modeSwitch={{ mode: 'code', onModeChange }}
            sessionContext={{
              mode: 'code',
              permissionMode: 'accept_edits',
              onPermissionModeChange: onPermissionChange,
              workspaceState: {
                kind: 'local',
                path: 'C:/projetos/orin',
                folderName: 'orin',
                scope: 'chat',
                projectName: null,
              },
            }}
          />
        </div>
      </MemoryRouter>,
    )

    expect(screen.getByTestId('home-mode-code')).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByTestId('home-mode-chat'))
    expect(onModeChange).toHaveBeenCalledWith('chat')

    // Shift+Tab in Composer textarea cycles permission mode and notifies sessionContext
    const textarea = screen.getByLabelText('Mensagem')
    textarea.focus()
    await user.keyboard('{Shift>}{Tab}{/Shift}')
    expect(onPermissionChange).toHaveBeenCalledWith('plan_mode')

    // Open ModelPicker and press Escape -> closes without propagating to parent
    await user.click(screen.getByRole('button', { name: /Claude Sonnet 4\.5/i }))
    expect(screen.getByRole('listbox', { name: /Modelos disponíveis/i })).toBeInTheDocument()
    parentKeyDown.mockClear()
    await user.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('listbox', { name: /Modelos disponíveis/i })).not.toBeInTheDocument()
    })
    expect(parentKeyDown).not.toHaveBeenCalled()
  })
})

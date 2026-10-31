import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ModelPicker } from '../../../components/ModelPicker'
import { Composer } from '../Composer'
import { SessionContextPopover } from '../SessionContextPopover'

describe('SessionContextPopover', () => {
  it('renders a compact summary chip and opens unified popover with workspace, permission, worktree, and project controls without emojis', async () => {
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

    // Summary pill shows folder name and permission mode without AI-slop emojis
    const trigger = screen.getByTestId('session-context-trigger')
    const permissionPill = screen.getByTestId('code-permission-mode-pill')
    expect(trigger).toHaveTextContent('orin')
    expect(trigger.textContent).not.toMatch(/[📁🔒⚡⏸⏵⑂]/u)
    expect(trigger.querySelector('svg')).not.toBeNull()
    expect(permissionPill).toHaveTextContent('Pedir permissão')
    expect(permissionPill.textContent).not.toMatch(/[📁🔒⚡⏸⏵⑂]/u)
    expect(permissionPill.querySelector('svg')).not.toBeNull()

    // Change permission mode via hidden select
    fireEvent.change(screen.getByTestId('code-permission-mode-select'), {
      target: { value: 'plan_mode' },
    })
    expect(onPermissionChange).toHaveBeenCalledWith('plan_mode')

    // Open popover
    await user.click(trigger)
    const dialog = screen.getByRole('dialog', { name: /Contexto da sessão/i })
    expect(dialog).toBeInTheDocument()
    expect(dialog.textContent).not.toMatch(/[📁🔒⚡⏸⏵⑂]/u)

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

  it('shows active project badge, allows removing session from project, and renders permission mode selector in chat mode', async () => {
    const user = userEvent.setup()
    const onMoveToProject = vi.fn()
    const onPermissionChange = vi.fn()

    render(
      <MemoryRouter>
        <SessionContextPopover
          mode="chat"
          permissionMode="ask_permissions"
          onPermissionModeChange={onPermissionChange}
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

    // Permission mode pill is visible and clickable even in chat mode
    const pill = screen.getByTestId('code-permission-mode-pill')
    expect(pill).toHaveTextContent('Pedir permissão')
    expect(pill.textContent).not.toMatch(/[📁🔒⚡⏸⏵⑂]/u)
    await user.click(pill)
    expect(onPermissionChange).toHaveBeenCalledWith('full_autonomy')

    await user.click(screen.getByTestId('session-context-trigger'))
    // Popover shows permission mode options including Sem restrições in chat mode
    await user.click(screen.getByRole('button', { name: /Sem restrições/i }))
    expect(onPermissionChange).toHaveBeenCalledWith('full_autonomy')

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

  it('constrains popover maxHeight to available space above trigger inside .home__stage so top is never clipped, and flips downward when top space is tight', async () => {
    const user = userEvent.setup()

    const { container } = render(
      <MemoryRouter>
        <section className="home__stage">
          <SessionContextPopover
            mode="code"
            permissionMode="plan_mode"
            onPermissionModeChange={vi.fn()}
            workspaceState={{
              kind: 'managed',
              path: null,
              folderName: null,
              scope: 'chat',
              projectName: null,
            }}
          />
        </section>
      </MemoryRouter>,
    )

    const stage = container.querySelector('.home__stage') as HTMLElement
    const popoverRoot = container.querySelector('.session-context-popover') as HTMLElement

    vi.spyOn(stage, 'getBoundingClientRect').mockReturnValue({
      top: 48,
      bottom: 760,
      left: 0,
      right: 1280,
      width: 1280,
      height: 712,
      x: 0,
      y: 48,
      toJSON: () => ({}),
    })

    // Case 1: Trigger is at y=420 (only ~350px above inside .home__stage)
    vi.spyOn(popoverRoot, 'getBoundingClientRect').mockReturnValue({
      top: 420,
      bottom: 448,
      left: 300,
      right: 520,
      width: 220,
      height: 28,
      x: 300,
      y: 420,
      toJSON: () => ({}),
    })

    await user.click(screen.getByTestId('session-context-trigger'))
    const dialog = screen.getByTestId('session-context-dialog')
    expect(dialog.getAttribute('data-placement')).toBe('top')
    const maxHeightPx = Number.parseInt(dialog.style.maxHeight, 10)
    expect(maxHeightPx).toBeGreaterThanOrEqual(200)
    // Must fit strictly inside (420 - 48 - margin) so the top of the popover is never clipped by .home__stage
    expect(maxHeightPx).toBeLessThanOrEqual(360)

    // Case 2: Trigger is very close to the top of .home__stage (y=180), more room below -> flips downward
    vi.spyOn(popoverRoot, 'getBoundingClientRect').mockReturnValue({
      top: 180,
      bottom: 208,
      left: 300,
      right: 520,
      width: 220,
      height: 28,
      x: 300,
      y: 180,
      toJSON: () => ({}),
    })
    fireEvent(window, new Event('resize'))

    expect(dialog.getAttribute('data-placement')).toBe('bottom')

    // Case 3: Mobile viewport (width=390) with trigger at x=40 -> clamps left offset so panel never overflows left or right edge
    const prevInnerWidth = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 390 })
    vi.spyOn(stage, 'getBoundingClientRect').mockReturnValue({
      top: 48,
      bottom: 844,
      left: 0,
      right: 390,
      width: 390,
      height: 796,
      x: 0,
      y: 48,
      toJSON: () => ({}),
    })
    vi.spyOn(popoverRoot, 'getBoundingClientRect').mockReturnValue({
      top: 480,
      bottom: 540,
      left: 40,
      right: 268,
      width: 228,
      height: 60,
      x: 40,
      y: 480,
      toJSON: () => ({}),
    })
    fireEvent(window, new Event('resize'))
    expect(dialog.style.left).toBe('-24px')
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: prevInnerWidth })
  })
})

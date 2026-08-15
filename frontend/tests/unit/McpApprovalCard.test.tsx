import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { McpApprovalCard } from '../../src/features/conversations/McpApprovalCard'

function server(overrides: Partial<Parameters<typeof McpApprovalCard>[0]['server']> = {}) {
  return {
    server_id: 's1', display_name: 'GitHub', transport: 'stdio',
    secret_names: ['GITHUB_PERSONAL_ACCESS_TOKEN'], catalog_id: 'github', ...overrides,
  }
}

describe('McpApprovalCard', () => {
  it('renders the server name, transport and what it will be able to do', () => {
    render(<McpApprovalCard active server={server()} onApprove={vi.fn()} onDecline={vi.fn()} />)

    expect(screen.getByText('GitHub')).toBeInTheDocument()
    expect(screen.getByText(/stdio/i)).toBeInTheDocument()
  })

  it('renders one password field per required secret, none echoed as text', () => {
    render(<McpApprovalCard active server={server({ secret_names: ['TOKEN_A', 'TOKEN_B'] })} onApprove={vi.fn()} onDecline={vi.fn()} />)

    const fieldA = screen.getByLabelText('TOKEN_A')
    const fieldB = screen.getByLabelText('TOKEN_B')
    expect(fieldA).toHaveAttribute('type', 'password')
    expect(fieldA).toHaveAttribute('autoComplete', 'off')
    expect(fieldB).toHaveAttribute('type', 'password')
  })

  it('disables Conectar while a required field is empty', () => {
    render(<McpApprovalCard active server={server({ secret_names: ['TOKEN'] })} onApprove={vi.fn()} onDecline={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Conectar' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('TOKEN'), { target: { value: 'ghp_x' } })
    expect(screen.getByRole('button', { name: 'Conectar' })).toBeEnabled()
  })

  it('calls onApprove with the typed values and never renders the typed value as plain text', async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined)
    render(<McpApprovalCard active server={server({ secret_names: ['TOKEN'] })} onApprove={onApprove} onDecline={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('TOKEN'), { target: { value: 'ghp_super_secret' } })
    fireEvent.click(screen.getByRole('button', { name: 'Conectar' }))

    await waitFor(() => expect(onApprove).toHaveBeenCalledWith({ TOKEN: 'ghp_super_secret' }))
    expect(document.body.textContent).not.toContain('ghp_super_secret')
  })

  it('a server needing no secret can be connected immediately', async () => {
    const onApprove = vi.fn().mockResolvedValue(undefined)
    render(<McpApprovalCard active server={server({ secret_names: [], transport: 'http' })} onApprove={onApprove} onDecline={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Conectar' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: 'Conectar' }))
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith({}))
  })

  it('shows the failure message and keeps the typed values when the connection fails', async () => {
    const onApprove = vi.fn().mockRejectedValue(new Error('token rejected'))
    render(<McpApprovalCard active server={server({ secret_names: ['TOKEN'] })} onApprove={onApprove} onDecline={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('TOKEN'), { target: { value: 'ghp_x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Conectar' }))

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.getByLabelText('TOKEN')).toHaveValue('ghp_x')
  })

  it('calls onDecline without touching onApprove', async () => {
    const onApprove = vi.fn()
    const onDecline = vi.fn().mockResolvedValue(undefined)
    render(<McpApprovalCard active server={server()} onApprove={onApprove} onDecline={onDecline} />)

    fireEvent.click(screen.getByRole('button', { name: 'Recusar' }))

    await waitFor(() => expect(onDecline).toHaveBeenCalledOnce())
    expect(onApprove).not.toHaveBeenCalled()
  })

  it('renders a settled state once the turn is no longer waiting', () => {
    render(<McpApprovalCard active={false} server={server()} onApprove={vi.fn()} onDecline={vi.fn()} />)

    expect(screen.queryByRole('button', { name: 'Conectar' })).not.toBeInTheDocument()
    expect(screen.getByText('Resolvido · GitHub')).toBeInTheDocument()
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkspaceTrustCard } from '../WorkspaceTrustCard'
import { nextCodePermissionMode, normalizeCodePermissionMode, CODE_PERMISSION_MODES } from '../../../api/codeMode'
import type { ConversationActivityEvent } from '../activityTypes'

const event: ConversationActivityEvent = {
  eventId: 'e1', cursor: 'c1', type: 'workspace.trust_required', kind: 'lifecycle', state: 'completed',
  agentId: 'agent:main', summary: 'Esta pasta tem configurações desativadas até você confiar nela',
  workspaceTrust: { path: 'C:\repo', items: ['hooks', 'permissions.allow'] },
}

describe('WorkspaceTrustCard', () => {
  it('lists the disabled items and trusts on click', async () => {
    const trust = vi.fn().mockResolvedValue({ trusted: true, path: 'C:\repo' })
    render(<WorkspaceTrustCard event={event} conversationId="conv-1" trust={trust} />)
    expect(screen.getByText(/hooks/)).toBeTruthy()
    expect(screen.getByText(/permissions\.allow/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Confiar nesta pasta/ }))
    await waitFor(() => expect(trust).toHaveBeenCalledWith('conv-1'))
    expect(await screen.findByText(/próxima mensagem/)).toBeTruthy()
  })

  it('shows an error when trusting fails', async () => {
    const trust = vi.fn().mockRejectedValue(new Error('nope'))
    render(<WorkspaceTrustCard event={event} conversationId="conv-1" trust={trust} />)
    fireEvent.click(screen.getByRole('button', { name: /Confiar nesta pasta/ }))
    expect(await screen.findByRole('alert')).toBeTruthy()
  })
})

describe('permission modes', () => {
  it('knows auto and dont_ask and accepts Claude Code names', () => {
    expect(CODE_PERMISSION_MODES.map((item) => item.mode)).toContain('auto')
    expect(CODE_PERMISSION_MODES.map((item) => item.mode)).toContain('dont_ask')
    expect(normalizeCodePermissionMode('acceptEdits')).toBe('accept_edits')
    expect(normalizeCodePermissionMode('bypassPermissions')).toBe('full_autonomy')
    expect(normalizeCodePermissionMode('dontAsk')).toBe('dont_ask')
    expect(normalizeCodePermissionMode('auto')).toBe('auto')
    expect(nextCodePermissionMode('auto')).toBe('ask_permissions')
    expect(nextCodePermissionMode('dont_ask')).toBe('ask_permissions')
  })
})

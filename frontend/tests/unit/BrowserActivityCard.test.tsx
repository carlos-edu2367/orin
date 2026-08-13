import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BrowserActivityCard } from '../../src/features/conversations/BrowserActivityCard'
import type { ActivityGroup } from '../../src/features/conversations/activityTypes'

const group: ActivityGroup = {
  id: 'tool:agent:browser', kind: 'tool', state: 'completed', label: 'Abriu example.com', count: 1,
  agentId: 'agent:main', failed: false,
  events: [{
    eventId: 'event-1', cursor: 'a.1', type: 'tool.finished', kind: 'tool', state: 'completed', agentId: 'agent:main',
    toolName: 'browse_page', toolKind: 'browser', label: 'Example Domain', summary: 'Abriu example.com',
    screenshotPath: 'browser-captures/example.png',
  }],
}

describe('BrowserActivityCard', () => {
  it('renders a private workspace capture and opens the existing preview', () => {
    const onPreview = vi.fn()
    render(<BrowserActivityCard group={group} conversationId="conversation-1" onPreview={onPreview} />)

    expect(screen.getByRole('img', { name: /captura privada/i })).toHaveAttribute(
      'src', '/v1/conversations/conversation-1/files/browser-captures/example.png?disposition=inline',
    )
    fireEvent.click(screen.getByRole('button', { name: /abrir captura/i }))
    expect(onPreview).toHaveBeenCalledWith({ conversationId: 'conversation-1', path: 'browser-captures/example.png', name: 'Example Domain' })
  })
})

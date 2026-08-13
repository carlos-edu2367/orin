import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MessageAttachments } from '../../src/features/conversations/MessageAttachments'

describe('MessageAttachments', () => {
  it('renders one card per attachment', () => {
    render(<MessageAttachments conversationId="chat_1" items={[
      { path: 'uploads/nota.pdf', original_name: 'nota.pdf', media_type: 'application/pdf', kind: 'pdf', bytes: 2048 },
    ]} />)
    expect(screen.getByText('nota.pdf')).toBeInTheDocument()
  })

  it('renders nothing when there are no attachments', () => {
    const { container } = render(<MessageAttachments conversationId="chat_1" items={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})

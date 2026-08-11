import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkspaceFileCard, WorkspaceFilePreview, type WorkspaceFileReference } from '../../src/features/conversations/WorkspaceFileCard'

const reference: WorkspaceFileReference = { conversationId: 'chat_abc', path: 'reports/index.html' }

describe('WorkspaceFileCard', () => {
  it('keeps preview state outside the card and reports the selected file', () => {
    const onPreview = vi.fn()
    render(<WorkspaceFileCard reference={reference} onPreview={onPreview} />)

    fireEvent.click(screen.getByRole('button', { name: 'Visualizar index.html' }))
    expect(onPreview).toHaveBeenCalledWith(reference)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders a full-height preview and closes on Escape', () => {
    const onClose = vi.fn()
    render(<WorkspaceFilePreview reference={reference} onClose={onClose} />)

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByTitle('Preview de index.html')).toHaveAttribute('sandbox', 'allow-scripts')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

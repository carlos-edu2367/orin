import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MessageCommandChip } from '../../src/features/conversations/MessageCommandChip'

describe('MessageCommandChip', () => {
  it('shows the command and its arguments', () => {
    render(<MessageCommandChip command={{ command_id: 'demo:daily', slug: 'daily', arguments: 'amanhã' }} />)

    expect(screen.getByText('/daily')).toBeInTheDocument()
    expect(screen.getByText('amanhã')).toBeInTheDocument()
  })

  it('shows only the command when no arguments were given', () => {
    render(<MessageCommandChip command={{ command_id: 'demo:daily', slug: 'daily', arguments: '' }} />)

    expect(screen.getByText('/daily')).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent(/^\/daily$/)
  })
})

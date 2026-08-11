import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MarkdownMessage } from '../../src/features/conversations/MarkdownMessage'

describe('MarkdownMessage', () => {
  it('renders assistant Markdown structure including GFM and fenced code', () => {
    const { container } = render(
      <MarkdownMessage
        content={'## Arquivos\n\n**Criados**\n\n- [x] hello.txt\n\n```ts\nconst answer = 42\n```'}
      />,
    )

    expect(screen.getByRole('heading', { level: 2, name: 'Arquivos' })).toBeInTheDocument()
    expect(screen.getByText('Criados')).toBeInTheDocument()
    expect(container.querySelector('strong')).toHaveTextContent('Criados')
    expect(container.querySelector('input[type="checkbox"]')).toBeChecked()
    expect(container.querySelector('pre code')).toHaveTextContent('const answer = 42')
  })

  it('does not render raw HTML from the assistant content', () => {
    const { container } = render(<MarkdownMessage content={'<span data-testid="unsafe">conteúdo</span>'} />)

    expect(container.querySelector('[data-testid="unsafe"]')).not.toBeInTheDocument()
  })

  it('turns a workspace reference into a preview and download card', () => {
    render(<MarkdownMessage conversationId="chat_abc" content={'[Abrir](workspace://reports/index.html)'} />)

    expect(screen.getByRole('button', { name: 'Visualizar index.html' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Baixar index.html' })).toHaveAttribute('href', '/v1/conversations/chat_abc/files/reports/index.html?disposition=attachment')
  })
})

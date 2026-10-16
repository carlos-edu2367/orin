import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MarkdownMessage } from '../../src/features/conversations/MarkdownMessage'

describe('MarkdownMessage', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

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

  it('renders ordered and unordered lists with semantic list items', () => {
    const { container } = render(
      <MarkdownMessage
        content={'- Primeiro item\n- Segundo item\n\n1. Passo um\n2. Passo dois'}
      />,
    )

    const ul = container.querySelector('ul')
    const ol = container.querySelector('ol')
    expect(ul).toBeInTheDocument()
    expect(ol).toBeInTheDocument()
    expect(ul?.querySelectorAll('li')).toHaveLength(2)
    expect(ol?.querySelectorAll('li')).toHaveLength(2)
    expect(screen.getByText('Primeiro item')).toBeInTheDocument()
    expect(screen.getByText('Passo dois')).toBeInTheDocument()
  })

  it('displays the detected language header and copies code to clipboard with temporary feedback', async () => {
    vi.useFakeTimers()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })

    render(
      <MarkdownMessage
        content={'```python\ndef soma(a, b):\n    return a + b\n```'}
      />,
    )

    expect(screen.getByTestId('markdown-code-language')).toHaveTextContent('python')
    const copyButton = screen.getByRole('button', { name: 'Copiar código' })
    expect(copyButton).toHaveTextContent('Copiar')

    await act(async () => {
      fireEvent.click(copyButton)
    })

    expect(writeText).toHaveBeenCalledWith('def soma(a, b):\n    return a + b')
    expect(copyButton).toHaveTextContent('Copiado!')

    act(() => {
      vi.advanceTimersByTime(1500)
    })

    expect(copyButton).toHaveTextContent('Copiar')
  })

  it('falls back to "código" header label when fenced code has no language tag', () => {
    render(<MarkdownMessage content={'```\nlinha simples\n```'} />)

    expect(screen.getByTestId('markdown-code-language')).toHaveTextContent('código')
    expect(screen.getByRole('button', { name: 'Copiar código' })).toBeInTheDocument()
  })

  it('does not render raw HTML from the assistant content', () => {
    const { container } = render(<MarkdownMessage content={'<span data-testid="unsafe">conteúdo</span>'} />)

    expect(container.querySelector('[data-testid="unsafe"]')).not.toBeInTheDocument()
  })

  it('turns a workspace reference into an inline workspace file pill', () => {
    render(<MarkdownMessage conversationId="chat_abc" content={'[Abrir](workspace://reports/index.html)'} />)

    const pill = screen.getByRole('button', { name: 'Visualizar index.html' })
    expect(pill).toBeInTheDocument()
    expect(pill).toHaveAttribute('data-testid', 'inline-workspace-file-pill')
    expect(pill).toHaveAttribute('data-workspace-path', 'reports/index.html')
  })
})

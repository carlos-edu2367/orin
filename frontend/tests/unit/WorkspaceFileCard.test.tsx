import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { WorkspaceFileCard, WorkspaceFilePreview, previewKindFor, type WorkspaceFileReference } from '../../src/features/conversations/WorkspaceFileCard'

const reference: WorkspaceFileReference = { conversationId: 'chat_abc', path: 'reports/index.html' }

describe('WorkspaceFileCard', () => {
  const originalFetch = globalThis.fetch

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('keeps preview state outside the card and reports the selected file', () => {
    const onPreview = vi.fn()
    render(<WorkspaceFileCard reference={reference} onPreview={onPreview} />)

    fireEvent.click(screen.getByRole('button', { name: 'Visualizar index.html' }))
    expect(onPreview).toHaveBeenCalledWith(reference)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders markdown and JSON natively instead of delegating their layout to the browser', async () => {
    globalThis.fetch = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response('# Relatório\n\n- pronto'))
      .mockResolvedValueOnce(new Response('{"total":42}'))

    const markdown = render(<WorkspaceFilePreview reference={{ conversationId: 'chat_abc', path: 'reports/readme.md' }} onClose={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Relatório' })).toBeInTheDocument()
    expect(screen.queryByTitle('Preview de readme.md')).not.toBeInTheDocument()
    markdown.unmount()

    render(<WorkspaceFilePreview reference={{ conversationId: 'chat_abc', path: 'reports/orcamento.json' }} onClose={() => {}} />)
    expect(await screen.findByLabelText('Conteúdo JSON')).toHaveTextContent('"total": 42')
    expect(screen.queryByTitle('Preview de orcamento.json')).not.toBeInTheDocument()
  })

  it('presents source files in a readable code surface', async () => {
    globalThis.fetch = vi.fn<typeof fetch>().mockResolvedValue(new Response('def total(items):\n    return sum(items)'))

    render(<WorkspaceFilePreview reference={{ conversationId: 'chat_abc', path: 'scripts/total.py' }} onClose={() => {}} />)

    expect(await screen.findByLabelText('Código Python')).toHaveTextContent('def total(items):')
    expect(screen.queryByTitle('Preview de total.py')).not.toBeInTheDocument()
  })

  it('uses dedicated browser viewers only for images and PDFs', () => {
    const image = render(<WorkspaceFilePreview reference={{ conversationId: 'chat_abc', path: 'reports/foto.png' }} onClose={() => {}} />)
    expect(screen.getByRole('img', { name: 'Preview de foto.png' })).toBeInTheDocument()
    image.unmount()

    render(<WorkspaceFilePreview reference={{ conversationId: 'chat_abc', path: 'reports/contrato.pdf' }} onClose={() => {}} />)
    expect(screen.getByTitle('Preview de contrato.pdf')).toHaveAttribute('sandbox', 'allow-scripts')
  })

  it('keeps unsupported files explicit and lets the person use the local app', () => {
    render(<WorkspaceFilePreview reference={{ conversationId: 'chat_abc', path: 'reports/planilha.xlsx' }} onClose={() => {}} />)
    expect(screen.getByText('Prévia indisponível neste navegador')).toBeInTheDocument()
    expect(screen.queryByTitle('Preview de planilha.xlsx')).not.toBeInTheDocument()
  })

  it('closes on Escape', () => {
    const onClose = vi.fn()
    render(<WorkspaceFilePreview reference={{ conversationId: 'chat_abc', path: 'reports/contrato.pdf' }} onClose={onClose} />)

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('classifies the supported visual and text families without trusting the browser MIME viewer', () => {
    expect(previewKindFor('a.md')).toBe('markdown')
    expect(previewKindFor('a.json')).toBe('json')
    expect(previewKindFor('a.py')).toBe('code')
    expect(previewKindFor('a.png')).toBe('image')
    expect(previewKindFor('a.pdf')).toBe('pdf')
    expect(previewKindFor('a.docx')).toBe('unsupported')
  })
})

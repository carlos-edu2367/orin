import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import process from 'node:process'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  CodeWorkbenchPanel,
  sanitizePreviewUrl,
  type CodeWorkbenchFileItem,
  type CodeWorkbenchTerminalEntry,
} from '../CodeWorkbenchPanel'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const SAMPLE_FILES: CodeWorkbenchFileItem[] = [
  {
    path: 'src/pages/TrilhaHype.tsx',
    status: 'modified',
    addedLines: 42,
    removedLines: 5,
    structuredPatch: [
      {
        oldStart: 10,
        oldLines: 3,
        newStart: 10,
        newLines: 4,
        lines: [
          ' export function TrilhaHype() {',
          '-  return <div>Rascunho</div>',
          '+  return <main>Transforme ideias em projetos com IA</main>',
          '+  // layout responsivo',
          ' }',
        ],
      },
    ],
  },
  {
    path: 'src/styles/trilha.css',
    status: 'added',
    addedLines: 28,
    removedLines: 0,
  },
  {
    path: 'README.md',
    status: 'modified',
    addedLines: 4,
    removedLines: 1,
  },
]

const SAMPLE_TERMINAL: CodeWorkbenchTerminalEntry[] = [
  {
    id: 'cmd-1',
    command: 'npm run build',
    output: '✓ built in 1.42s',
    status: 'completed',
  },
  {
    id: 'cmd-2',
    command: 'npm run dev -- --port 3000',
    output: 'Local: http://localhost:3000/trilhas/ferramentas-do-hype',
    status: 'running',
  },
]

describe('CodeWorkbenchPanel (ref-08.png — Orin Code Split Workbench)', () => {
  it('starts on Prévia tab, renders iframe when local server URL is detected in terminal (or empty state when none), and does not mount file paths while on Prévia', () => {
    render(
      <CodeWorkbenchPanel
        files={SAMPLE_FILES}
        terminalEntries={SAMPLE_TERMINAL}
        running={true}
        elapsedLabel="2 min"
      />,
    )

    const panel = screen.getByTestId('code-workbench-panel')
    expect(panel).toHaveClass('code-workbench')

    const tablist = screen.getByTestId('code-workbench-tabs')
    expect(tablist).toHaveAttribute('role', 'tablist')

    const previewTab = screen.getByTestId('workbench-tab-preview')
    const filesTab = screen.getByTestId('workbench-tab-files')
    const terminalTab = screen.getByTestId('workbench-tab-terminal')

    expect(previewTab).toHaveAttribute('aria-selected', 'true')
    expect(filesTab).toHaveAttribute('aria-selected', 'false')
    expect(terminalTab).toHaveAttribute('aria-selected', 'false')

    // Conditional mounting guard: files and terminal entries must NOT be in the DOM on Prévia tab
    expect(screen.queryByText('README.md')).not.toBeInTheDocument()
    expect(screen.queryByText('src/pages/TrilhaHype.tsx')).not.toBeInTheDocument()
    expect(screen.queryByTestId('workbench-files-view')).not.toBeInTheDocument()
    expect(screen.queryByTestId('workbench-terminal-view')).not.toBeInTheDocument()

    // Preview stage content: auto-detected from SAMPLE_TERMINAL
    const stage = screen.getByTestId('workbench-preview-stage')
    expect(stage).toHaveAttribute('data-viewport', 'desktop')
    const iframe = within(stage).getByTestId('workbench-preview-iframe')
    expect(iframe).toHaveAttribute('src', 'http://localhost:3000/trilhas/ferramentas-do-hype')

    // When no URL is provided or detected, renders clean empty state
    cleanup()
    render(<CodeWorkbenchPanel files={SAMPLE_FILES} terminalEntries={[]} />)
    expect(screen.getByTestId('workbench-preview-empty')).toHaveTextContent(
      'Nenhuma prévia ativa no momento. Informe uma URL http(s) acima ou inicie um servidor local no terminal.',
    )
    expect(screen.queryByTestId('workbench-preview-iframe')).not.toBeInTheDocument()
  })

  it('toggles viewport between desktop and mobile and reloads the preview stage', () => {
    render(<CodeWorkbenchPanel files={SAMPLE_FILES} />)

    const stage = screen.getByTestId('workbench-preview-stage')
    const desktopBtn = screen.getByTestId('workbench-viewport-desktop')
    const mobileBtn = screen.getByTestId('workbench-viewport-mobile')
    const reloadBtn = screen.getByTestId('workbench-preview-reload')

    expect(stage).toHaveAttribute('data-viewport', 'desktop')
    expect(desktopBtn).toHaveAttribute('aria-pressed', 'true')
    expect(mobileBtn).toHaveAttribute('aria-pressed', 'false')

    fireEvent.click(mobileBtn)
    expect(stage).toHaveAttribute('data-viewport', 'mobile')
    expect(mobileBtn).toHaveAttribute('aria-pressed', 'true')
    expect(desktopBtn).toHaveAttribute('aria-pressed', 'false')

    fireEvent.click(desktopBtn)
    expect(stage).toHaveAttribute('data-viewport', 'desktop')

    expect(stage).toHaveAttribute('data-reload-count', '0')
    fireEvent.click(reloadBtn)
    expect(stage).toHaveAttribute('data-reload-count', '1')
  })

  it('enforces Red-Team URL XSS guard in sanitizePreviewUrl and in the preview toolbar', () => {
    expect(sanitizePreviewUrl('http://localhost:3000/trilhas/ferramentas-do-hype')).toBe(
      'http://localhost:3000/trilhas/ferramentas-do-hype',
    )
    expect(sanitizePreviewUrl('  https://orin.example.com/preview  ')).toBe(
      'https://orin.example.com/preview',
    )
    expect(sanitizePreviewUrl('javascript:alert(1)')).toBeNull()
    expect(sanitizePreviewUrl('JAVASCRIPT:alert(document.cookie)')).toBeNull()
    expect(sanitizePreviewUrl('data:text/html,<script>alert(1)</script>')).toBeNull()
    expect(sanitizePreviewUrl('vbscript:msgbox(1)')).toBeNull()
    expect(sanitizePreviewUrl('file:///C:/Windows/win.ini')).toBeNull()
    expect(sanitizePreviewUrl('http://admin:secret@localhost:3000/')).toBeNull()
    expect(sanitizePreviewUrl('http://localhost:3000/\u0000evil')).toBeNull()
    expect(sanitizePreviewUrl('')).toBeNull()
    expect(sanitizePreviewUrl('not a url')).toBeNull()

    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)

    render(
      <CodeWorkbenchPanel
        files={SAMPLE_FILES}
        initialPreviewUrl="javascript:alert('xss-initial')"
      />,
    )

    const urlInput = screen.getByTestId('workbench-preview-url-input') as HTMLInputElement
    // Unsafe initialPreviewUrl must fall back to empty string and render empty state
    expect(urlInput.value).toBe('')
    expect(screen.getByTestId('workbench-preview-empty')).toBeInTheDocument()

    // Attempt XSS via javascript: protocol
    fireEvent.change(urlInput, { target: { value: 'javascript:alert(1)' } })
    fireEvent.submit(urlInput.closest('form') ?? urlInput)

    const errorAlert = screen.getByTestId('workbench-preview-url-error')
    expect(errorAlert).toHaveTextContent('URL inválida. Use http:// ou https://.')

    // Clicking open external while URL is unsafe must not call window.open
    fireEvent.click(screen.getByTestId('workbench-preview-open-external'))
    expect(openSpy).not.toHaveBeenCalled()

    // Attempt XSS via data: protocol
    fireEvent.change(urlInput, { target: { value: 'data:text/html,<script>alert(1)</script>' } })
    fireEvent.click(screen.getByTestId('workbench-preview-open-external'))
    expect(screen.getByTestId('workbench-preview-url-error')).toBeInTheDocument()
    expect(openSpy).not.toHaveBeenCalled()

    // Valid https URL clears error and opens safely with noopener,noreferrer
    fireEvent.change(urlInput, { target: { value: 'https://auryly.example.com/trilhas' } })
    fireEvent.submit(urlInput.closest('form') ?? urlInput)
    expect(screen.queryByTestId('workbench-preview-url-error')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('workbench-preview-open-external'))
    expect(openSpy).toHaveBeenCalledWith(
      'https://auryly.example.com/trilhas',
      '_blank',
      'noopener,noreferrer',
    )
  })

  it('switches to Arquivos tab, filters files via workbench-files-search, and renders StructuredDiffView', () => {
    const onSelectFile = vi.fn()
    render(
      <CodeWorkbenchPanel
        files={SAMPLE_FILES}
        onSelectFile={onSelectFile}
      />,
    )

    fireEvent.click(screen.getByTestId('workbench-tab-files'))

    const filesView = screen.getByTestId('workbench-files-view')
    expect(filesView).toBeInTheDocument()
    expect(screen.queryByTestId('workspace-files-search')).not.toBeInTheDocument()

    const searchInput = screen.getByTestId('workbench-files-search')
    expect(screen.getByText('src/pages/TrilhaHype.tsx')).toBeInTheDocument()
    expect(screen.getByText('src/styles/trilha.css')).toBeInTheDocument()
    expect(screen.getByText('README.md')).toBeInTheDocument()

    // First file with structuredPatch shows StructuredDiffView
    expect(screen.getByLabelText('Visualização de diff unificado')).toBeInTheDocument()
    expect(screen.getByText(/\+.*return <main>Transforme ideias em projetos com IA<\/main>/)).toBeInTheDocument()

    // Select second file (without structuredPatch)
    fireEvent.click(screen.getByTestId('workbench-file-item-src/styles/trilha.css'))
    expect(onSelectFile).toHaveBeenCalledWith('src/styles/trilha.css')
    expect(screen.queryByLabelText('Visualização de diff unificado')).not.toBeInTheDocument()

    // Filter files by search query
    fireEvent.change(searchInput, { target: { value: 'TrilhaHype' } })
    expect(screen.getByText('src/pages/TrilhaHype.tsx')).toBeInTheDocument()
    expect(screen.queryByText('README.md')).not.toBeInTheDocument()
  })

  it('switches to Terminal tab and renders terminal entries or empty state', () => {
    const { rerender } = render(
      <CodeWorkbenchPanel
        files={SAMPLE_FILES}
        terminalEntries={SAMPLE_TERMINAL}
      />,
    )

    fireEvent.click(screen.getByTestId('workbench-tab-terminal'))
    const terminalView = screen.getByTestId('workbench-terminal-view')
    expect(terminalView).toBeInTheDocument()
    expect(within(terminalView).getByText(/npm run build/)).toBeInTheDocument()
    expect(within(terminalView).getByText(/built in 1\.42s/)).toBeInTheDocument()
    expect(within(terminalView).getByText(/npm run dev -- --port 3000/)).toBeInTheDocument()

    rerender(<CodeWorkbenchPanel files={SAMPLE_FILES} terminalEntries={[]} />)
    expect(screen.getByText('Nenhum comando executado ainda.')).toBeInTheDocument()
  })

  it('renders status footer with running state, elapsed label, and files button that switches to Arquivos tab', () => {
    const onTabChange = vi.fn()
    render(
      <CodeWorkbenchPanel
        files={SAMPLE_FILES}
        running={true}
        elapsedLabel="2 min"
        onTabChange={onTabChange}
      />,
    )

    const footer = screen.getByTestId('workbench-status-footer')
    expect(footer).toHaveTextContent('Em andamento · 2 min')

    const filesBtn = screen.getByTestId('workbench-footer-files-button')
    expect(filesBtn).toHaveTextContent('3 arquivos alterados')

    fireEvent.click(filesBtn)
    expect(onTabChange).toHaveBeenCalledWith('files')
    expect(screen.getByTestId('workbench-files-view')).toBeInTheDocument()
  })

  it('renders close button (code-workbench-close) only when onClose is provided and invokes onClose on click', () => {
    const onClose = vi.fn()
    const { rerender } = render(<CodeWorkbenchPanel files={SAMPLE_FILES} />)
    expect(screen.queryByTestId('code-workbench-close')).toBeNull()

    rerender(<CodeWorkbenchPanel files={SAMPLE_FILES} onClose={onClose} />)
    const closeBtn = screen.getByTestId('code-workbench-close')
    expect(closeBtn).toHaveClass('code-workbench__close-btn')
    expect(closeBtn).toHaveAttribute('aria-label', 'Recolher bancada')

    fireEvent.click(closeBtn)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('contains no prohibited dark-only utility classes in CodeWorkbenchPanel.tsx', () => {
    const source = readFileSync(
      resolve(process.cwd(), 'src/features/conversations/CodeWorkbenchPanel.tsx'),
      'utf-8',
    )
    expect(source).not.toMatch(/\btext-white\b|\bbg-black\b|\bborder-white\b/)
  })
})

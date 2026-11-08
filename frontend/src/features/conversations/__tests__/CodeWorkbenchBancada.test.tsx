import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import process from 'node:process'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AUTO_RELOAD_DEBOUNCE_MS, CodeWorkbenchPanel, type CodeWorkbenchFileItem } from '../CodeWorkbenchPanel'
import type { ConversationActivityEvent } from '../activityTypes'
import {
  buildWorkbenchTree,
  fileIconFor,
  normalizeWorkbenchPath,
} from '../workbenchTreeModel'
import { deriveFileChangeToken, derivePreviewFromEvents, isLikelyHmrUrl } from '../workbenchPreview'

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

const FILES: CodeWorkbenchFileItem[] = [
  { path: 'src/pages/Zeta.tsx', status: 'modified', addedLines: 3, removedLines: 1 },
  { path: 'src/pages/alpha.tsx', status: 'added' },
  { path: 'src/App.tsx', status: 'modified' },
  { path: 'README.md', status: 'modified' },
  { path: 'docs/guide/intro.md', status: 'inspected' },
  { path: 'a.json', status: 'modified' },
  { path: 'Bravo/index.ts', status: 'modified' },
]

function renderFiles(props: Partial<Parameters<typeof CodeWorkbenchPanel>[0]> = {}) {
  render(<CodeWorkbenchPanel files={FILES} activeTab="files" rootName="orin" {...props} />)
  return screen.getByRole('tree')
}

function treeLabels(): string[] {
  return Array.from(document.querySelectorAll('.wb-tree__label')).map((el) => el.textContent ?? '')
}

function event(partial: Partial<ConversationActivityEvent> & { eventId: string }): ConversationActivityEvent {
  return {
    cursor: partial.eventId,
    type: 'tool.finished',
    kind: 'tool',
    state: 'completed',
    agentId: 'agent:main',
    summary: '',
    ...partial,
  } as ConversationActivityEvent
}

describe('workbench tree model', () => {
  it('normalizes Windows separators, ./ prefixes and absolute paths inside the workspace', () => {
    expect(normalizeWorkbenchPath('.\\src\\App.tsx')).toBe('src/App.tsx')
    expect(normalizeWorkbenchPath('C:\\Repo\\Orin\\src\\a.ts', 'c:/repo/orin')).toBe('src/a.ts')
    expect(normalizeWorkbenchPath('C:/Repo/Orin//src/a.ts', 'C:\\Repo\\Orin\\')).toBe('src/a.ts')
    expect(normalizeWorkbenchPath('D:/other/a.ts', 'C:/Repo/Orin')).toBe('D:/other/a.ts')
    expect(normalizeWorkbenchPath('src/a.ts', 'C:/Repo/Orin')).toBe('src/a.ts')
  })

  it('sorts folders before files, case-insensitively, and compacts single-child folders', () => {
    const tree = buildWorkbenchTree(FILES)
    expect(tree.map((node) => node.name)).toEqual(['Bravo', 'docs/guide', 'src', 'a.json', 'README.md'])
    const src = tree.find((node) => node.name === 'src')
    expect(src?.kind).toBe('folder')
    if (src?.kind !== 'folder') return
    expect(src.children.map((node) => node.name)).toEqual(['pages', 'App.tsx'])
    const pages = src.children[0]
    if (pages.kind !== 'folder') throw new Error('pages should be a folder')
    expect(pages.children.map((node) => node.name)).toEqual(['alpha.tsx', 'Zeta.tsx'])
    expect(tree.find((node) => node.name === 'docs/guide')).toMatchObject({ id: 'docs/guide', changed: false })
  })

  it('maps file types to small text badges', () => {
    expect(fileIconFor('App.tsx')).toEqual({ label: 'TS', tone: 'tsx' })
    expect(fileIconFor('main.js').label).toBe('JS')
    expect(fileIconFor('package.json').label).toBe('{}')
    expect(fileIconFor('index.css').label).toBe('#')
    expect(fileIconFor('README.md').label).toBe('MD')
    expect(fileIconFor('app.py').label).toBe('PY')
    expect(fileIconFor('LICENSE').tone).toBe('default')
  })
})

describe('Bancada explorer (VS Code-like tree)', () => {
  it('renders an ARIA tree with folders first, levels, expanded state and the root header', () => {
    const tree = renderFiles()
    expect(screen.getByTestId('workbench-explorer-root')).toHaveTextContent('orin')
    expect(treeLabels()).toEqual(['Bravo', 'index.ts', 'docs/guide', 'intro.md', 'src', 'pages', 'alpha.tsx', 'Zeta.tsx', 'App.tsx', 'a.json', 'README.md'])

    const src = screen.getByTestId('workbench-folder-item-src')
    expect(src).toHaveAttribute('role', 'treeitem')
    expect(src).toHaveAttribute('aria-level', '1')
    expect(src).toHaveAttribute('aria-expanded', 'true')
    expect(within(src).getAllByRole('group').length).toBeGreaterThan(0)
    const zeta = screen.getByTestId('workbench-file-item-src/pages/Zeta.tsx')
    expect(zeta).toHaveAttribute('aria-level', '3')
    expect(zeta).not.toHaveAttribute('aria-expanded')
    // First file is selected by default and shown in the viewer.
    expect(screen.getByTestId('workbench-file-item-src/pages/Zeta.tsx')).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByTestId('workbench-file-detail')).toHaveTextContent('Diff · src/pages/Zeta.tsx')
    expect(tree).toHaveAttribute('aria-label', 'Arquivos de orin')
  })

  it('collapses/expands folders lazily (children unmounted when closed) and collapse-all closes everything', () => {
    renderFiles()
    const src = screen.getByTestId('workbench-folder-item-src')
    fireEvent.click(within(src).getAllByText('src')[0])
    expect(src).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('workbench-file-item-src/App.tsx')).not.toBeInTheDocument()
    expect(src.querySelector('.wb-tree__chevron')).not.toHaveClass('is-open')

    fireEvent.click(within(src).getAllByText('src')[0])
    expect(screen.getByTestId('workbench-file-item-src/App.tsx')).toBeInTheDocument()
    expect(src.querySelector('.wb-tree__chevron')).toHaveClass('is-open')

    fireEvent.click(screen.getByTestId('workbench-explorer-collapse'))
    expect(treeLabels()).toEqual(['Bravo', 'docs/guide', 'src', 'a.json', 'README.md'])
  })

  it('opens a file in the viewer on click and on Enter', () => {
    const onSelectFile = vi.fn()
    renderFiles({ onSelectFile })
    fireEvent.click(screen.getByTestId('workbench-file-item-src/App.tsx'))
    expect(onSelectFile).toHaveBeenLastCalledWith('src/App.tsx')
    expect(screen.getByTestId('workbench-file-detail')).toHaveTextContent('Diff · src/App.tsx')
    expect(screen.getByTestId('workbench-file-item-src/App.tsx')).toHaveAttribute('aria-selected', 'true')
  })

  it('supports keyboard navigation: arrows, left/right on folders, Home/End and Enter', () => {
    const onSelectFile = vi.fn()
    const tree = renderFiles({ onSelectFile })
    const focused = () => document.activeElement as HTMLElement

    // Roving tabindex starts on the selected file.
    const zeta = screen.getByTestId('workbench-file-item-src/pages/Zeta.tsx')
    expect(zeta).toHaveAttribute('tabindex', '0')
    zeta.focus()

    fireEvent.keyDown(tree, { key: 'Home' })
    expect(focused()).toBe(screen.getByTestId('workbench-folder-item-Bravo'))
    fireEvent.keyDown(tree, { key: 'ArrowDown' })
    expect(focused()).toBe(screen.getByTestId('workbench-file-item-Bravo/index.ts'))
    fireEvent.keyDown(tree, { key: 'ArrowLeft' })
    expect(focused()).toBe(screen.getByTestId('workbench-folder-item-Bravo'))
    fireEvent.keyDown(tree, { key: 'ArrowLeft' })
    expect(screen.getByTestId('workbench-folder-item-Bravo')).toHaveAttribute('aria-expanded', 'false')
    fireEvent.keyDown(tree, { key: 'ArrowRight' })
    expect(screen.getByTestId('workbench-folder-item-Bravo')).toHaveAttribute('aria-expanded', 'true')
    fireEvent.keyDown(tree, { key: 'ArrowRight' })
    expect(focused()).toBe(screen.getByTestId('workbench-file-item-Bravo/index.ts'))
    fireEvent.keyDown(tree, { key: 'Enter' })
    expect(onSelectFile).toHaveBeenLastCalledWith('Bravo/index.ts')

    fireEvent.keyDown(tree, { key: 'End' })
    expect(focused()).toBe(screen.getByTestId('workbench-file-item-README.md'))
    fireEvent.keyDown(tree, { key: 'ArrowUp' })
    expect(focused()).toBe(screen.getByTestId('workbench-file-item-a.json'))
    expect(focused().querySelector('.wb-tree__row')).toHaveClass('is-focused')
  })

  it('filters by path and keeps matching ancestors expanded', () => {
    renderFiles()
    fireEvent.click(screen.getByTestId('workbench-explorer-collapse'))
    fireEvent.change(screen.getByTestId('workbench-files-search'), { target: { value: 'alpha' } })
    expect(treeLabels()).toEqual(['src', 'pages', 'alpha.tsx'])
    fireEvent.change(screen.getByTestId('workbench-files-search'), { target: { value: 'nothing-here' } })
    expect(screen.queryByRole('tree')).not.toBeInTheDocument()
    expect(screen.getByTestId('workbench-files-view')).toHaveTextContent('Nenhum arquivo encontrado para o filtro.')
  })

  it('renders single-line rows with ellipsis classes and status decorations (no overlapping/wrapping)', () => {
    renderFiles()
    const row = screen.getByTestId('workbench-file-item-src/pages/Zeta.tsx').querySelector('.wb-tree__row') as HTMLElement
    expect(row).toHaveClass('wb-tree__row', 'is-modified')
    expect(row.querySelector('.wb-tree__label')).toHaveTextContent('Zeta.tsx')
    expect(row.querySelector('.wb-file-icon')).toHaveTextContent('TS')
    expect(row.querySelector('.wb-tree__decoration')).toHaveTextContent('M')
    expect(row).toHaveAttribute('title', 'src/pages/Zeta.tsx · Modificado · +3 −1')
    expect(screen.getByTestId('workbench-file-item-src/pages/alpha.tsx').querySelector('.wb-tree__decoration')).toHaveTextContent('A')

    const css = readFileSync(resolve(process.cwd(), 'src/features/conversations/codeWorkbench.css'), 'utf-8')
    const rule = (selector: string) => {
      const start = css.indexOf(`${selector} {`)
      expect(start).toBeGreaterThanOrEqual(0)
      return css.slice(start, css.indexOf('}', start))
    }
    expect(rule('.wb-tree__row')).toMatch(/height: var\(--wb-row-h\)/)
    expect(rule('.wb-tree__row')).toMatch(/white-space: nowrap/)
    expect(rule('.wb-tree__label')).toMatch(/text-overflow: ellipsis/)
    expect(rule('.wb-tree__label')).toMatch(/min-width: 0/)
    expect(css).toMatch(/--wb-row-h: 22px/)
    expect(css).toMatch(/\.wb-tree__chevron\.is-open svg \{\s*transform: rotate\(90deg\)/)
  })
})

describe('Bancada preview driven by the agent', () => {
  it('shows the URL from PreviewStart/PreviewOpen and replaces a user-typed URL when the agent opens a new one', () => {
    const { rerender } = render(
      <CodeWorkbenchPanel files={[]} preview={{ status: 'running', url: 'http://localhost:5173/', name: 'vite', hmr: true, token: 'ev:1' }} />,
    )
    expect(screen.getByTestId('workbench-preview-iframe')).toHaveAttribute('src', 'http://localhost:5173/')
    fireEvent.change(screen.getByTestId('workbench-preview-url-input'), { target: { value: 'http://localhost:5173/about' } })
    expect(screen.getByTestId('workbench-preview-iframe')).toHaveAttribute('src', 'http://localhost:5173/about')

    rerender(
      <CodeWorkbenchPanel files={[]} preview={{ status: 'running', url: 'http://127.0.0.1:8000/app', name: '127.0.0.1:8000', token: 'ev:2' }} />,
    )
    expect(screen.getByTestId('workbench-preview-iframe')).toHaveAttribute('src', 'http://127.0.0.1:8000/app')
    expect(screen.getByTestId('workbench-preview-url-input')).toHaveValue('http://127.0.0.1:8000/app')
  })

  it('never loads an unsafe agent URL', () => {
    render(<CodeWorkbenchPanel files={[]} preview={{ status: 'running', url: 'javascript:alert(1)', token: 'ev:1' }} />)
    expect(screen.queryByTestId('workbench-preview-iframe')).not.toBeInTheDocument()
    expect(screen.getByTestId('workbench-preview-empty')).toBeInTheDocument()
  })

  it('renders starting, failed (with retry) and empty states', () => {
    const onRetryPreview = vi.fn()
    const { rerender } = render(
      <CodeWorkbenchPanel files={[]} preview={{ status: 'starting', name: 'Vite dev', token: 'ev:1' }} />,
    )
    expect(screen.getByTestId('workbench-preview-starting')).toHaveTextContent('Iniciando servidor de prévia · Vite dev')
    expect(screen.queryByTestId('workbench-preview-iframe')).not.toBeInTheDocument()

    rerender(
      <CodeWorkbenchPanel
        files={[]}
        onRetryPreview={onRetryPreview}
        preview={{ status: 'failed', name: 'Vite dev', error: 'port 5173 already in use', token: 'ev:2' }}
      />,
    )
    expect(screen.getByTestId('workbench-preview-failed')).toHaveTextContent('port 5173 already in use')
    fireEvent.click(screen.getByTestId('workbench-preview-retry'))
    expect(onRetryPreview).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('workbench-preview-failed')).not.toBeInTheDocument()
    expect(screen.getByTestId('workbench-preview-empty')).toHaveTextContent('Nenhuma prévia ativa')
  })

  it('starts the preview via POST /v1/conversations/{id}/preview/start from the empty state and moves to starting', async () => {
    let resolveFetch!: (value: Response) => void
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve
        }),
    )
    try {
      render(<CodeWorkbenchPanel files={[]} conversationId="conv-preview-1" />)
      const startBtn = screen.getByRole('button', { name: /iniciar prévia/i })
      fireEvent.click(startBtn)

      expect(fetchSpy).toHaveBeenCalledWith(
        '/v1/conversations/conv-preview-1/preview/start',
        expect.objectContaining({ method: 'POST' }),
      )
      expect(screen.getByTestId('workbench-preview-starting')).toBeInTheDocument()

      await act(async () => {
        resolveFetch(
          new Response(JSON.stringify({ status: 'running', url: 'http://127.0.0.1:5173' }), {
            status: 202,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      })
    } finally {
      fetchSpy.mockRestore()
    }
  })

  it('auto-reloads (debounced) after file changes when the server has no HMR, and skips it for HMR servers', () => {
    vi.useFakeTimers()
    const preview = { status: 'running' as const, url: 'http://127.0.0.1:8000/', name: 'http.server', hmr: false, token: 'ev:1' }
    const { rerender } = render(<CodeWorkbenchPanel files={[]} preview={preview} fileChangeToken="w:1" />)
    const stage = screen.getByTestId('workbench-preview-stage')
    expect(stage).toHaveAttribute('data-auto-reload', 'on')
    expect(stage).toHaveAttribute('data-reload-count', '0')

    rerender(<CodeWorkbenchPanel files={[]} preview={preview} fileChangeToken="w:2" />)
    act(() => {
      vi.advanceTimersByTime(AUTO_RELOAD_DEBOUNCE_MS - 100)
    })
    rerender(<CodeWorkbenchPanel files={[]} preview={preview} fileChangeToken="w:3" />)
    act(() => {
      vi.advanceTimersByTime(AUTO_RELOAD_DEBOUNCE_MS - 100)
    })
    expect(stage).toHaveAttribute('data-reload-count', '0')
    act(() => {
      vi.advanceTimersByTime(200)
    })
    expect(stage).toHaveAttribute('data-reload-count', '1')
    expect(screen.getByTestId('workbench-preview-iframe')).toHaveAttribute('src', 'http://127.0.0.1:8000/')

    // HMR dev server: auto-reload defaults off, the toggle can turn it on.
    const hmrPreview = { ...preview, url: 'http://localhost:5173/', hmr: true, token: 'ev:2' }
    rerender(<CodeWorkbenchPanel files={[]} preview={hmrPreview} fileChangeToken="w:4" />)
    act(() => {
      vi.advanceTimersByTime(AUTO_RELOAD_DEBOUNCE_MS * 2)
    })
    expect(stage).toHaveAttribute('data-auto-reload', 'off')
    expect(stage).toHaveAttribute('data-reload-count', '1')
    fireEvent.click(screen.getByTestId('workbench-preview-autoreload'))
    expect(screen.getByTestId('workbench-preview-autoreload')).toHaveAttribute('aria-pressed', 'true')
    rerender(<CodeWorkbenchPanel files={[]} preview={hmrPreview} fileChangeToken="w:5" />)
    act(() => {
      vi.advanceTimersByTime(AUTO_RELOAD_DEBOUNCE_MS)
    })
    expect(stage).toHaveAttribute('data-reload-count', '2')
  })
})

describe('preview events → Bancada state', () => {
  it('derives starting → running from PreviewStart and running from PreviewOpen', () => {
    const started = event({ eventId: 'e1', type: 'tool.started', state: 'working', toolName: 'preview_start' })
    expect(derivePreviewFromEvents([started])).toMatchObject({ status: 'starting', token: 'e1' })
    const finished = event({
      eventId: 'e2',
      toolName: 'preview_start',
      status: 'succeeded',
      previewServer: { url: 'http://127.0.0.1:5173', name: 'Vite', status: 'running', hmr: true },
    })
    expect(derivePreviewFromEvents([started, finished])).toEqual({
      status: 'running', url: 'http://127.0.0.1:5173', name: 'Vite', hmr: true, token: 'e2',
    })
    const opened = event({
      eventId: 'e3',
      toolName: 'preview_open',
      status: 'succeeded',
      previewServer: { url: 'http://localhost:3000/', name: 'localhost:3000', status: 'running' },
    })
    expect(derivePreviewFromEvents([started, finished, opened])).toMatchObject({ status: 'running', url: 'http://localhost:3000/', token: 'e3' })
  })

  it('reports failures, exited servers and PreviewStop; ignores permission prompts', () => {
    const failed = event({ eventId: 'f1', toolName: 'preview_start', state: 'failed', status: 'failed', content: 'no launch configuration' })
    expect(derivePreviewFromEvents([failed])).toMatchObject({ status: 'failed', error: 'no launch configuration' })
    const exited = event({ eventId: 'f2', toolName: 'preview_start', previewServer: { url: 'http://127.0.0.1:5173', name: 'Vite', status: 'exited', exitCode: 1 } })
    expect(derivePreviewFromEvents([exited])).toMatchObject({ status: 'failed', error: expect.stringContaining('código 1') })
    const stopped = event({ eventId: 's1', toolName: 'preview_stop', previewServer: { name: 'Vite', status: 'stopped' } })
    expect(derivePreviewFromEvents([exited, stopped])).toBeNull()
    const prompt = event({
      eventId: 'p1',
      type: 'tool.permission_required',
      toolName: 'preview_start',
      toolPermission: { state: 'pending' } as unknown as ConversationActivityEvent['toolPermission'],
    })
    expect(derivePreviewFromEvents([prompt])).toBeUndefined()
  })

  it('tracks the latest successful write/edit for auto-reload', () => {
    const events = [
      event({ eventId: 'w1', toolName: 'write_file', status: 'succeeded' }),
      event({ eventId: 'w2', toolName: 'edit_file', status: 'failed', state: 'failed' }),
      event({ eventId: 'r1', toolName: 'read_file', status: 'succeeded' }),
    ]
    expect(deriveFileChangeToken(events)).toBe('w1')
    expect(deriveFileChangeToken([...events, event({ eventId: 'w3', toolName: 'edit_file' })])).toBe('w3')
    expect(deriveFileChangeToken([])).toBeNull()
  })

  it('treats the usual dev-server ports as HMR when the server did not say', () => {
    expect(isLikelyHmrUrl('http://localhost:5173/')).toBe(true)
    expect(isLikelyHmrUrl('http://localhost:3000/')).toBe(true)
    expect(isLikelyHmrUrl('http://127.0.0.1:8000/')).toBe(false)
    expect(isLikelyHmrUrl(null)).toBe(false)
  })
})

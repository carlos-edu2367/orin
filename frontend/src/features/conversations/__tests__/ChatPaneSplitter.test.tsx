import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider } from '../../../app/ThemeContext'
import { ChatPage } from '../ChatPage'
import { PaneSplitter } from '../PaneSplitter'
import codeWorkbenchCss from '../codeWorkbench.css?raw'
import {
  CHAT_PANE_DEFAULT_WIDTH,
  CHAT_PANE_MIN_WIDTH,
  CHAT_PANE_WIDTH_STORAGE_KEY,
  RAIL_COLLAPSED_WIDTH,
  RAIL_EXPANDED_WIDTH,
  WORKBENCH_MIN_WIDTH,
  readChatPaneWidth,
  useChatPaneWidth,
  useResizableSplit,
} from '../useChatPaneWidth'

const CONVERSATION_ID = 'conv_resizable_split'

function createActivity(
  sequence: number,
  type: string,
  summary: string,
  payload: Record<string, unknown> = {},
) {
  return {
    event_id: `ev:${sequence}`,
    event_type: type,
    sequence,
    summary,
    payload,
    occurred_at: '2026-11-07T10:00:00.000Z',
    turn_id: 'turn-1',
    execution_id: 'exe-1',
    agent_id: 'agent:main',
    parent_agent_id: null,
    cursor: `a.${sequence}`,
  }
}

function buildConversationSnapshot(mode: 'code' | 'chat' = 'code') {
  return {
    conversation_id: CONVERSATION_ID,
    title: 'Resizable Bancada Test',
    state: 'completed',
    mode,
    permission_mode: 'ask_permissions',
    provider: 'openai',
    model_id: 'gpt-5',
    messages: [
      {
        message_id: 'msg-1',
        role: 'user',
        content: 'Mostre a Bancada.',
        status: 'completed',
        retryable: false,
        turn_id: 'turn-1',
      },
      {
        message_id: 'msg-2',
        role: 'assistant',
        content: 'Pronto.',
        status: 'completed',
        retryable: false,
        turn_id: 'turn-1',
      },
    ],
    turns: [
      {
        turn_id: 'turn-1',
        state: 'completed',
        code_mode: mode === 'code' ? 'code' : 'chat',
        created_at: '2026-11-07T10:00:00.000Z',
        started_at: '2026-11-07T10:00:00.000Z',
        finished_at: '2026-11-07T10:05:00.000Z',
      },
    ],
    activities: [
      createActivity(1, 'tool.finished', 'Editou src/App.tsx', {
        tool_name: 'write_file',
        tool_kind: 'filesystem',
        status: 'succeeded',
        path: 'C:\\Projetos\\landing\\src\\App.tsx',
        added_lines: 4,
        removed_lines: 0,
      }),
    ],
    activity_cursor: 'a.1',
    workspace: {
      kind: 'local',
      path: 'C:/Projetos/landing',
      folder_name: 'landing',
      scope: 'chat',
      project_name: null,
    },
  }
}

function installMockFetch(mode: 'code' | 'chat' = 'code') {
  globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const url = typeof input === 'string' ? input : String(input)
    if (url.includes('/events?')) {
      return new Response('event: heartbeat\ndata: {"cursor":"a.1"}\n\n', {
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    if (url.endsWith('/v1/projects/sidebar') || url.endsWith('/v1/conversations')) {
      return new Response(JSON.stringify({ items: [] }), {
        headers: { 'Content-Type': 'application/json' },
      })
    }
    if (
      url.includes(`/v1/conversations/${CONVERSATION_ID}`) &&
      !url.includes(`/v1/conversations/${CONVERSATION_ID}/`)
    ) {
      return new Response(JSON.stringify(buildConversationSnapshot(mode)), {
        headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response('{}', { headers: { 'Content-Type': 'application/json' } })
  })
}

function renderChatPage() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[`/chats/${CONVERSATION_ID}`]}>
        <Routes>
          <Route path="/chats/:conversationId" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  )
}

describe('useResizableSplit / useChatPaneWidth hook', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('uses default width (420px) when localStorage is empty and ignores invalid values', () => {
    const { result: emptyResult } = renderHook(() =>
      useResizableSplit({ containerWidth: 1440, railWidth: RAIL_EXPANDED_WIDTH }),
    )
    expect(emptyResult.current.width).toBe(CHAT_PANE_DEFAULT_WIDTH)
    expect(emptyResult.current.preferredWidth).toBeNull()

    for (const invalid of ['abc', '', 'NaN', '-100', '0', 'Infinity']) {
      localStorage.setItem(CHAT_PANE_WIDTH_STORAGE_KEY, invalid)
      expect(readChatPaneWidth()).toBeNull()
      const { result } = renderHook(() =>
        useResizableSplit({ containerWidth: 1440, railWidth: RAIL_EXPANDED_WIDTH }),
      )
      expect(result.current.width).toBe(CHAT_PANE_DEFAULT_WIDTH)
      expect(result.current.preferredWidth).toBeNull()
    }
  })

  it('reads and writes valid widths to localStorage and clamps to [min, max]', () => {
    localStorage.setItem(CHAT_PANE_WIDTH_STORAGE_KEY, '510')
    // container 1440 - rail 264 - workbenchMin 360 = maxWidth 816
    const { result } = renderHook(() =>
      useChatPaneWidth({ containerWidth: 1440, railWidth: RAIL_EXPANDED_WIDTH }),
    )
    expect(result.current.maxWidth).toBe(1440 - RAIL_EXPANDED_WIDTH - WORKBENCH_MIN_WIDTH)
    expect(result.current.width).toBe(510)
    expect(result.current.preferredWidth).toBe(510)

    // Clamp below minimum (320)
    act(() => {
      result.current.commitWidth(180)
    })
    expect(result.current.width).toBe(CHAT_PANE_MIN_WIDTH)
    expect(localStorage.getItem(CHAT_PANE_WIDTH_STORAGE_KEY)).toBe(String(CHAT_PANE_MIN_WIDTH))

    // Clamp above maximum (816)
    act(() => {
      result.current.commitWidth(1200)
    })
    expect(result.current.width).toBe(816)
    expect(localStorage.getItem(CHAT_PANE_WIDTH_STORAGE_KEY)).toBe('816')
  })

  it('readjusts effective width when container shrinks without losing the saved preference, and resets cleanly', () => {
    localStorage.setItem(CHAT_PANE_WIDTH_STORAGE_KEY, '600')
    const { result, rerender } = renderHook(
      ({ containerWidth, railWidth }) => useResizableSplit({ containerWidth, railWidth }),
      {
        initialProps: { containerWidth: 1440, railWidth: RAIL_EXPANDED_WIDTH },
      },
    )

    expect(result.current.width).toBe(600)
    expect(result.current.preferredWidth).toBe(600)

    // Window shrinks to 1100px -> maxWidth = 1100 - 264 - 360 = 476px
    rerender({ containerWidth: 1100, railWidth: RAIL_EXPANDED_WIDTH })
    expect(result.current.maxWidth).toBe(476)
    expect(result.current.width).toBe(476)
    // Saved preference in state and localStorage is preserved!
    expect(result.current.preferredWidth).toBe(600)
    expect(localStorage.getItem(CHAT_PANE_WIDTH_STORAGE_KEY)).toBe('600')

    // Collapsing rail (64px) increases maxWidth: 1100 - 64 - 360 = 676px -> width recovers to 600px
    rerender({ containerWidth: 1100, railWidth: RAIL_COLLAPSED_WIDTH })
    expect(result.current.maxWidth).toBe(676)
    expect(result.current.width).toBe(600)

    // Reset clears saved preference and restores default width (420px)
    act(() => {
      result.current.resetWidth()
    })
    expect(result.current.preferredWidth).toBeNull()
    expect(result.current.width).toBe(CHAT_PANE_DEFAULT_WIDTH)
    expect(localStorage.getItem(CHAT_PANE_WIDTH_STORAGE_KEY)).toBeNull()
  })
})

describe('PaneSplitter component', () => {
  afterEach(() => {
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
    vi.restoreAllMocks()
  })

  it('renders correct ARIA separator attributes', () => {
    render(
      <PaneSplitter
        value={420}
        min={320}
        max={780}
        onChange={vi.fn()}
        onCommit={vi.fn()}
        onReset={vi.fn()}
      />,
    )

    const splitter = screen.getByRole('separator', { name: 'Redimensionar chat e Bancada' })
    expect(splitter).toHaveAttribute('aria-orientation', 'vertical')
    expect(splitter).toHaveAttribute('aria-valuenow', '420')
    expect(splitter).toHaveAttribute('aria-valuemin', '320')
    expect(splitter).toHaveAttribute('aria-valuemax', '780')
    expect(splitter).toHaveAttribute('tabindex', '0')
  })

  it('adjusts width with ArrowLeft/ArrowRight (±16px), Shift+Arrows (±64px), Home/End, and resets on Enter or double-click', () => {
    const onChange = vi.fn()
    const onCommit = vi.fn()
    const onReset = vi.fn()

    const { rerender } = render(
      <PaneSplitter
        value={420}
        min={320}
        max={780}
        onChange={onChange}
        onCommit={onCommit}
        onReset={onReset}
      />,
    )

    const splitter = screen.getByRole('separator', { name: 'Redimensionar chat e Bancada' })

    fireEvent.keyDown(splitter, { key: 'ArrowRight' })
    expect(onChange).toHaveBeenLastCalledWith(436)
    expect(onCommit).toHaveBeenLastCalledWith(436)

    fireEvent.keyDown(splitter, { key: 'ArrowLeft' })
    expect(onChange).toHaveBeenLastCalledWith(404)
    expect(onCommit).toHaveBeenLastCalledWith(404)

    fireEvent.keyDown(splitter, { key: 'ArrowRight', shiftKey: true })
    expect(onChange).toHaveBeenLastCalledWith(484)
    expect(onCommit).toHaveBeenLastCalledWith(484)

    fireEvent.keyDown(splitter, { key: 'ArrowLeft', shiftKey: true })
    expect(onChange).toHaveBeenLastCalledWith(356)
    expect(onCommit).toHaveBeenLastCalledWith(356)

    fireEvent.keyDown(splitter, { key: 'Home' })
    expect(onChange).toHaveBeenLastCalledWith(320)
    expect(onCommit).toHaveBeenLastCalledWith(320)

    fireEvent.keyDown(splitter, { key: 'End' })
    expect(onChange).toHaveBeenLastCalledWith(780)
    expect(onCommit).toHaveBeenLastCalledWith(780)

    // Clamp at max
    rerender(
      <PaneSplitter
        value={770}
        min={320}
        max={780}
        onChange={onChange}
        onCommit={onCommit}
        onReset={onReset}
      />,
    )
    fireEvent.keyDown(splitter, { key: 'ArrowRight', shiftKey: true })
    expect(onChange).toHaveBeenLastCalledWith(780)

    // Enter resets
    fireEvent.keyDown(splitter, { key: 'Enter' })
    expect(onReset).toHaveBeenCalledTimes(1)

    // Double-click resets
    fireEvent.doubleClick(splitter)
    expect(onReset).toHaveBeenCalledTimes(2)
  })

  it('updates width during pointerdown/pointermove and only commits on pointerup', () => {
    const onChange = vi.fn()
    const onCommit = vi.fn()
    const onReset = vi.fn()
    const onResizingChange = vi.fn()

    render(
      <PaneSplitter
        value={420}
        min={320}
        max={800}
        onChange={onChange}
        onCommit={onCommit}
        onReset={onReset}
        onResizingChange={onResizingChange}
      />,
    )

    const splitter = screen.getByRole('separator', { name: 'Redimensionar chat e Bancada' })

    fireEvent.pointerDown(splitter, { clientX: 400, button: 0, pointerId: 1 })
    expect(onResizingChange).toHaveBeenLastCalledWith(true)
    expect(document.body.style.userSelect).toBe('none')
    expect(document.body.style.cursor).toBe('col-resize')
    expect(onCommit).not.toHaveBeenCalled()

    // Move +95px -> 420 + 95 = 515px
    fireEvent.pointerMove(splitter, { clientX: 495, pointerId: 1 })
    expect(onChange).toHaveBeenCalledWith(515)
    // Not persisted yet while dragging!
    expect(onCommit).not.toHaveBeenCalled()

    // Release pointer at clientX 520 (+120px -> 540px)
    fireEvent.pointerUp(splitter, { clientX: 520, pointerId: 1 })
    expect(onChange).toHaveBeenLastCalledWith(540)
    expect(onCommit).toHaveBeenCalledTimes(1)
    expect(onCommit).toHaveBeenCalledWith(540)
    expect(onResizingChange).toHaveBeenLastCalledWith(false)
    expect(document.body.style.userSelect).toBe('')
    expect(document.body.style.cursor).toBe('')
  })

  it('captures pointer via setPointerCapture and coalesces rapid pointermove events via requestAnimationFrame', () => {
    const onChange = vi.fn()
    const onCommit = vi.fn()
    let rafCallback: ((time: number) => void) | null = null
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      rafCallback = cb
      return 42
    })
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {
      rafCallback = null
    })

    render(
      <PaneSplitter
        value={420}
        min={320}
        max={800}
        onChange={onChange}
        onCommit={onCommit}
        onReset={vi.fn()}
      />,
    )

    const splitter = screen.getByRole('separator', { name: 'Redimensionar chat e Bancada' })
    const setCaptureSpy = vi.fn()
    const releaseCaptureSpy = vi.fn()
    splitter.setPointerCapture = setCaptureSpy
    splitter.releasePointerCapture = releaseCaptureSpy

    fireEvent.pointerDown(splitter, { clientX: 400, button: 0, pointerId: 7 })
    expect(setCaptureSpy).toHaveBeenCalledWith(7)

    // First move in frame flushes leading edge (420 + 30 = 450) and schedules RAF
    fireEvent.pointerMove(splitter, { clientX: 430, pointerId: 7 })
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenLastCalledWith(450)
    expect(rafCallback).not.toBeNull()

    // Subsequent moves within the same frame are coalesced until RAF fires
    fireEvent.pointerMove(splitter, { clientX: 460, pointerId: 7 })
    fireEvent.pointerMove(splitter, { clientX: 490, pointerId: 7 })
    expect(onChange).toHaveBeenCalledTimes(1)

    // Flush animation frame -> applies latest coalesced value (420 + 90 = 510)
    act(() => {
      rafCallback?.(16.6)
    })
    expect(onChange).toHaveBeenCalledTimes(2)
    expect(onChange).toHaveBeenLastCalledWith(510)
    expect(onCommit).not.toHaveBeenCalled()

    fireEvent.pointerUp(splitter, { clientX: 490, pointerId: 7 })
    expect(releaseCaptureSpy).toHaveBeenCalledWith(7)
    expect(onCommit).toHaveBeenCalledWith(510)
  })
})

describe('ChatPage + Bancada resizable split integration', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    localStorage.clear()
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('renders the separator and applies --chat-pane-width on <main> only when in code mode with Bancada open', async () => {
    installMockFetch('code')
    const { container } = renderChatPage()

    const toggleBtn = await screen.findByTestId('code-workbench-toggle')
    const mainEl = container.querySelector('main.chat') as HTMLElement
    expect(mainEl).toBeTruthy()

    // Closed initially: no separator and no --chat-pane-width CSS variable
    expect(screen.queryByRole('separator', { name: 'Redimensionar chat e Bancada' })).toBeNull()
    expect(mainEl.style.getPropertyValue('--chat-pane-width')).toBe('')

    // Open Bancada
    fireEvent.click(toggleBtn)

    const splitter = await screen.findByRole('separator', { name: 'Redimensionar chat e Bancada' })
    expect(splitter).toBeInTheDocument()
    expect(mainEl).toHaveClass('chat', 'chat--code-split')
    expect(mainEl.style.getPropertyValue('--chat-pane-width')).toBe('420px')

    // Drag separator from 400 to 500 (+100px -> 520px)
    fireEvent.pointerDown(splitter, { clientX: 400, button: 0, pointerId: 1 })
    expect(mainEl).toHaveClass('chat--resizing')

    fireEvent.pointerMove(splitter, { clientX: 500, pointerId: 1 })
    expect(mainEl.style.getPropertyValue('--chat-pane-width')).toBe('520px')
    // Still dragging: localStorage not written yet
    expect(localStorage.getItem(CHAT_PANE_WIDTH_STORAGE_KEY)).toBeNull()

    fireEvent.pointerUp(splitter, { clientX: 500, pointerId: 1 })
    expect(mainEl).not.toHaveClass('chat--resizing')
    expect(mainEl.style.getPropertyValue('--chat-pane-width')).toBe('520px')
    expect(localStorage.getItem(CHAT_PANE_WIDTH_STORAGE_KEY)).toBe('520')

    // Double-click resets to 420px and clears localStorage
    fireEvent.doubleClick(splitter)
    await waitFor(() => {
      expect(mainEl.style.getPropertyValue('--chat-pane-width')).toBe('420px')
    })
    expect(localStorage.getItem(CHAT_PANE_WIDTH_STORAGE_KEY)).toBeNull()

    // Closing Bancada removes the separator and the CSS variable
    fireEvent.click(toggleBtn)
    await waitFor(() => {
      expect(screen.queryByRole('separator', { name: 'Redimensionar chat e Bancada' })).toBeNull()
    })
    expect(mainEl).not.toHaveClass('chat--code-split')
    expect(mainEl.style.getPropertyValue('--chat-pane-width')).toBe('')
  })

  it('readjusts effective --chat-pane-width on window resize without losing saved preference and does not render in non-code mode', async () => {
    localStorage.setItem(CHAT_PANE_WIDTH_STORAGE_KEY, '600')
    installMockFetch('code')
    const { container, unmount } = renderChatPage()

    const toggleBtn = await screen.findByTestId('code-workbench-toggle')
    fireEvent.click(toggleBtn)

    const splitter = await screen.findByRole('separator', { name: 'Redimensionar chat e Bancada' })
    const mainEl = container.querySelector('main.chat') as HTMLElement
    expect(mainEl.style.getPropertyValue('--chat-pane-width')).toBe('600px')

    // Shrink window to 1080px -> maxWidth = 1080 - 264 - 360 = 456px
    const originalInnerWidth = window.innerWidth
    try {
      Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 1080 })
      act(() => {
        window.dispatchEvent(new Event('resize'))
      })
      expect(mainEl.style.getPropertyValue('--chat-pane-width')).toBe('456px')
      expect(splitter).toHaveAttribute('aria-valuemax', '456')
      expect(splitter).toHaveAttribute('aria-valuenow', '456')
      // Saved preference remains 600 in localStorage
      expect(localStorage.getItem(CHAT_PANE_WIDTH_STORAGE_KEY)).toBe('600')
    } finally {
      Object.defineProperty(window, 'innerWidth', {
        configurable: true,
        writable: true,
        value: originalInnerWidth,
      })
    }

    unmount()

    // Non-code mode: no separator and no --chat-pane-width
    installMockFetch('chat')
    const { container: chatContainer } = renderChatPage()
    await screen.findByText('Mostre a Bancada.')
    const chatMain = chatContainer.querySelector('main.chat') as HTMLElement
    expect(chatMain).not.toHaveClass('chat--code-split')
    expect(chatMain.style.getPropertyValue('--chat-pane-width')).toBe('')
    expect(screen.queryByRole('separator', { name: 'Redimensionar chat e Bancada' })).toBeNull()
  })

  it('defines CSS rules for --chat-pane-width grid columns, iframe pointer-events lock during drag, and reduced motion', () => {
    expect(codeWorkbenchCss).toContain('grid-template-columns: 264px var(--chat-pane-width, 420px) minmax(0, 1fr)')
    expect(codeWorkbenchCss).toContain('grid-template-columns: 64px var(--chat-pane-width, 420px) minmax(0, 1fr)')
    expect(codeWorkbenchCss).toContain('.chat.chat--resizing iframe')
    expect(codeWorkbenchCss).toContain('pointer-events: none')
    expect(codeWorkbenchCss).toContain('prefers-reduced-motion: reduce')
  })
})

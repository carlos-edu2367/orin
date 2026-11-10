import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type JSX,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { clampChatPaneWidth } from './useChatPaneWidth'
import './codeWorkbench.css'

// Ensure PointerEvent preserves MouseEvent coordinates (clientX/clientY/button) in JSDOM test environments.
if (typeof window !== 'undefined' && typeof window.PointerEvent === 'undefined' && typeof window.MouseEvent !== 'undefined') {
  class PolyfillPointerEvent extends window.MouseEvent {
    readonly pointerId: number
    readonly pointerType: string
    readonly isPrimary: boolean

    constructor(
      type: string,
      params: ConstructorParameters<typeof MouseEvent>[1] & { pointerId?: number; pointerType?: string; isPrimary?: boolean } = {},
    ) {
      super(type, params)
      this.pointerId = params.pointerId ?? 1
      this.pointerType = params.pointerType ?? 'mouse'
      this.isPrimary = params.isPrimary ?? true
    }
  }
  ;(window as unknown as { PointerEvent: typeof PolyfillPointerEvent }).PointerEvent = PolyfillPointerEvent
}

const KEYBOARD_STEP_PX = 16
const KEYBOARD_LARGE_STEP_PX = 64

export type PaneSplitterProps = {
  value: number
  min: number
  max: number
  ariaLabel?: string
  className?: string
  disabled?: boolean
  /** Called during drag (preview) and on keyboard step changes */
  onChange: (nextWidth: number) => void
  /** Called when the new width should be persisted (pointerup or keyboard step) */
  onCommit?: (finalWidth: number) => void
  /** Called on Enter or double-click to reset to default width and clear saved preference */
  onReset: () => void
  /** Notifies parent when pointer drag begins or ends */
  onResizingChange?: (resizing: boolean) => void
  'data-testid'?: string
}

type DragSession = {
  pointerId: number
  startX: number
  startValue: number
  latestValue: number
  lastFlushedValue: number
  moved: boolean
}

export function PaneSplitter({
  value,
  min,
  max,
  ariaLabel = 'Redimensionar chat e Bancada',
  className,
  disabled = false,
  onChange,
  onCommit,
  onReset,
  onResizingChange,
  'data-testid': testId = 'chat-workbench-splitter',
}: PaneSplitterProps): JSX.Element {
  const splitterRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<DragSession | null>(null)
  const rafRef = useRef<number | null>(null)
  const [isDragging, setIsDragging] = useState(false)

  const onChangeRef = useRef(onChange)
  const onCommitRef = useRef(onCommit)
  const onResizingChangeRef = useRef(onResizingChange)
  const boundsRef = useRef({ min, max })

  // Latest props for the drag handlers, synced after render (refs are not read during render).
  useLayoutEffect(() => {
    onChangeRef.current = onChange
    onCommitRef.current = onCommit
    onResizingChangeRef.current = onResizingChange
    boundsRef.current = { min, max }
  })

  const clearGlobalDragStyles = useCallback(() => {
    if (typeof document === 'undefined') return
    document.body.style.userSelect = ''
    document.body.style.cursor = ''
    document.documentElement.classList.remove('is-pane-resizing')
  }, [])

  const applyGlobalDragStyles = useCallback(() => {
    if (typeof document === 'undefined') return
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
    document.documentElement.classList.add('is-pane-resizing')
  }, [])

  const cancelPendingRaf = useCallback(() => {
    if (rafRef.current !== null && typeof cancelAnimationFrame === 'function') {
      cancelAnimationFrame(rafRef.current)
    }
    rafRef.current = null
  }, [])

  const scheduleFrameFlush = useCallback((nextValue: number) => {
    const session = dragRef.current
    if (!session) return
    session.latestValue = nextValue

    if (rafRef.current === null) {
      // Leading edge: apply immediately for responsive feel & deterministic tests,
      // then coalesce any remaining moves within the same animation frame.
      if (session.lastFlushedValue !== nextValue) {
        session.lastFlushedValue = nextValue
        onChangeRef.current(nextValue)
      }
      if (typeof requestAnimationFrame === 'function') {
        rafRef.current = requestAnimationFrame(() => {
          rafRef.current = null
          const active = dragRef.current
          if (!active) return
          if (active.lastFlushedValue !== active.latestValue) {
            active.lastFlushedValue = active.latestValue
            onChangeRef.current(active.latestValue)
          }
        })
      }
    }
  }, [])

  const finishDrag = useCallback(
    (clientX: number | null, commit: boolean) => {
      const session = dragRef.current
      if (!session) return
      dragRef.current = null
      cancelPendingRaf()

      const { min: currentMin, max: currentMax } = boundsRef.current
      let finalValue = session.latestValue
      if (typeof clientX === 'number' && Number.isFinite(clientX) && (clientX !== 0 || !session.moved)) {
        const delta = clientX - session.startX
        finalValue = clampChatPaneWidth(session.startValue + delta, currentMin, currentMax)
      }

      const el = splitterRef.current
      if (el && typeof el.releasePointerCapture === 'function') {
        try {
          el.releasePointerCapture(session.pointerId)
        } catch {
          // Ignore if pointer was not captured in synthetic/test environments
        }
      }

      clearGlobalDragStyles()
      setIsDragging(false)
      onResizingChangeRef.current?.(false)

      if (commit) {
        if (session.lastFlushedValue !== finalValue) {
          onChangeRef.current(finalValue)
        }
        onCommitRef.current?.(finalValue)
      }
    },
    [cancelPendingRaf, clearGlobalDragStyles],
  )

  const handlePointerMoveInternal = useCallback(
    (clientX: number) => {
      const session = dragRef.current
      if (!session) return
      const { min: currentMin, max: currentMax } = boundsRef.current
      const delta = clientX - session.startX
      session.moved = true
      const nextValue = clampChatPaneWidth(session.startValue + delta, currentMin, currentMax)
      scheduleFrameFlush(nextValue)
    },
    [scheduleFrameFlush],
  )

  useEffect(() => {
    if (!isDragging || typeof window === 'undefined') return

    const onWindowPointerMove = (event: PointerEvent) => {
      const session = dragRef.current
      if (!session) return
      handlePointerMoveInternal(event.clientX)
    }

    const onWindowPointerUp = (event: PointerEvent) => {
      const session = dragRef.current
      if (!session) return
      finishDrag(event.clientX, true)
    }

    const onWindowPointerCancel = () => {
      finishDrag(null, false)
    }

    window.addEventListener('pointermove', onWindowPointerMove)
    window.addEventListener('pointerup', onWindowPointerUp)
    window.addEventListener('pointercancel', onWindowPointerCancel)

    return () => {
      window.removeEventListener('pointermove', onWindowPointerMove)
      window.removeEventListener('pointerup', onWindowPointerUp)
      window.removeEventListener('pointercancel', onWindowPointerCancel)
    }
  }, [finishDrag, handlePointerMoveInternal, isDragging])

  useEffect(() => {
    return () => {
      cancelPendingRaf()
      clearGlobalDragStyles()
    }
  }, [cancelPendingRaf, clearGlobalDragStyles])

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (disabled) return
    if (event.button !== undefined && event.button !== 0) return

    event.preventDefault()

    const pointerId = event.pointerId ?? 1
    if (typeof event.currentTarget.setPointerCapture === 'function') {
      try {
        event.currentTarget.setPointerCapture(pointerId)
      } catch {
        // Ignore in synthetic environments where pointerId is not active
      }
    }

    const initialValue = clampChatPaneWidth(value, min, max)
    dragRef.current = {
      pointerId,
      startX: event.clientX,
      startValue: initialValue,
      latestValue: initialValue,
      lastFlushedValue: initialValue,
      moved: false,
    }

    applyGlobalDragStyles()
    setIsDragging(true)
    onResizingChange?.(true)
  }

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return
    handlePointerMoveInternal(event.clientX)
  }

  const handlePointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return
    finishDrag(event.clientX, true)
  }

  const handlePointerCancel = () => {
    if (!dragRef.current) return
    finishDrag(null, false)
  }

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (disabled) return

    const step = event.shiftKey ? KEYBOARD_LARGE_STEP_PX : KEYBOARD_STEP_PX

    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      const next = clampChatPaneWidth(value - step, min, max)
      onChange(next)
      onCommit?.(next)
      return
    }

    if (event.key === 'ArrowRight') {
      event.preventDefault()
      const next = clampChatPaneWidth(value + step, min, max)
      onChange(next)
      onCommit?.(next)
      return
    }

    if (event.key === 'Home') {
      event.preventDefault()
      const next = clampChatPaneWidth(min, min, max)
      onChange(next)
      onCommit?.(next)
      return
    }

    if (event.key === 'End') {
      event.preventDefault()
      const next = clampChatPaneWidth(max, min, max)
      onChange(next)
      onCommit?.(next)
      return
    }

    if (event.key === 'Enter') {
      event.preventDefault()
      onReset()
    }
  }

  const handleDoubleClick = () => {
    if (disabled) return
    onReset()
  }

  const rootClassName = [
    'pane-splitter',
    isDragging ? 'is-dragging' : '',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      ref={splitterRef}
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      aria-valuenow={Math.round(value)}
      aria-valuemin={Math.round(min)}
      aria-valuemax={Math.round(max)}
      tabIndex={disabled ? -1 : 0}
      data-testid={testId}
      className={rootClassName}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerCancel}
      onKeyDown={handleKeyDown}
      onDoubleClick={handleDoubleClick}
    />
  )
}

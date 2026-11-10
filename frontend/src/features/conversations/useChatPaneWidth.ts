import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from 'react'
import { readSidebarCollapsed } from '../../app/WorkspaceShell'

export const CHAT_PANE_WIDTH_STORAGE_KEY = 'orin.layout.chatPaneWidth'
export const CHAT_PANE_DEFAULT_WIDTH = 420
export const CHAT_PANE_MIN_WIDTH = 320
export const WORKBENCH_MIN_WIDTH = 360
export const RAIL_EXPANDED_WIDTH = 264
export const RAIL_COLLAPSED_WIDTH = 64
export const CHAT_PANE_CSS_VAR = '--chat-pane-width'

const DEFAULT_FALLBACK_CONTAINER_WIDTH = 1440
const JSDOM_DEFAULT_INNER_WIDTH = 1024

export function readChatPaneWidth(storageKey: string = CHAT_PANE_WIDTH_STORAGE_KEY): number | null {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return null
    const raw = window.localStorage.getItem(storageKey)
    if (raw === null || raw.trim() === '') return null
    const parsed = Number(raw)
    if (!Number.isFinite(parsed) || parsed <= 0) return null
    return Math.round(parsed)
  } catch {
    return null
  }
}

export function writeChatPaneWidth(
  width: number,
  storageKey: string = CHAT_PANE_WIDTH_STORAGE_KEY,
): void {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    if (!Number.isFinite(width) || width <= 0) return
    window.localStorage.setItem(storageKey, String(Math.round(width)))
  } catch {
    // Ignore storage errors in restricted environments or when quota is exceeded
  }
}

export function clearChatPaneWidth(storageKey: string = CHAT_PANE_WIDTH_STORAGE_KEY): void {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.removeItem(storageKey)
    }
  } catch {
    // Ignore storage errors in restricted environments
  }
}

export function clampChatPaneWidth(
  width: number,
  minWidth: number = CHAT_PANE_MIN_WIDTH,
  maxWidth: number = Number.POSITIVE_INFINITY,
): number {
  const safeMin = Math.max(0, Math.round(minWidth))
  const safeMax = Number.isFinite(maxWidth) ? Math.max(safeMin, Math.round(maxWidth)) : Number.POSITIVE_INFINITY
  if (!Number.isFinite(width)) return safeMin
  const rounded = Math.round(width)
  return Math.min(safeMax, Math.max(safeMin, rounded))
}

export function computeChatPaneMaxWidth(
  containerWidth: number,
  railWidth: number,
  workbenchMinWidth: number = WORKBENCH_MIN_WIDTH,
  minWidth: number = CHAT_PANE_MIN_WIDTH,
): number {
  const available = Math.round(containerWidth - railWidth - workbenchMinWidth)
  return Math.max(Math.round(minWidth), available)
}

function measureElementWidth(element: HTMLElement | null): number {
  if (!element) return 0
  const rectWidth = typeof element.getBoundingClientRect === 'function'
    ? element.getBoundingClientRect().width
    : 0
  if (rectWidth > 0) return rectWidth
  if (element.clientWidth > 0) return element.clientWidth
  return 0
}

function detectRailWidth(container: HTMLElement | null): number {
  if (container) {
    if (
      container.getAttribute('data-rail-collapsed') === 'true' ||
      container.querySelector('[data-rail-collapsed="true"]') !== null
    ) {
      return RAIL_COLLAPSED_WIDTH
    }
    if (
      container.getAttribute('data-rail-collapsed') === 'false' ||
      container.querySelector('[data-rail-collapsed="false"]') !== null
    ) {
      return RAIL_EXPANDED_WIDTH
    }
    const railElement = container.querySelector<HTMLElement>('.workspace-navigation')
    const measuredRail = measureElementWidth(railElement)
    if (measuredRail > 0) return measuredRail
  }
  return readSidebarCollapsed() ? RAIL_COLLAPSED_WIDTH : RAIL_EXPANDED_WIDTH
}

function detectContainerWidth(
  container: HTMLElement | null,
  observedWidth: number | null,
  windowResized: boolean,
): number {
  if (observedWidth !== null && observedWidth > 0) {
    return observedWidth
  }
  const measured = measureElementWidth(container)
  if (measured > 0) {
    return measured
  }
  if (typeof window !== 'undefined' && window.innerWidth > 0) {
    if (windowResized || window.innerWidth !== JSDOM_DEFAULT_INNER_WIDTH) {
      return window.innerWidth
    }
  }
  return DEFAULT_FALLBACK_CONTAINER_WIDTH
}

export type UseResizableSplitOptions = {
  storageKey?: string
  defaultWidth?: number
  minWidth?: number
  maxWidth?: number
  workbenchMinWidth?: number
  railWidth?: number
  containerWidth?: number
  containerRef?: RefObject<HTMLElement | null>
  enabled?: boolean
}

export type UseResizableSplitResult = {
  width: number
  preferredWidth: number | null
  minWidth: number
  maxWidth: number
  defaultWidth: number
  isResizing: boolean
  setIsResizing: (resizing: boolean) => void
  previewWidth: (nextWidth: number) => number
  commitWidth: (nextWidth: number) => number
  setWidth: (nextWidth: number, options?: { persist?: boolean }) => number
  resetWidth: () => void
  containerRef: RefObject<HTMLElement | null>
  style: CSSProperties | undefined
}

export function useResizableSplit(options: UseResizableSplitOptions = {}): UseResizableSplitResult {
  const {
    storageKey = CHAT_PANE_WIDTH_STORAGE_KEY,
    defaultWidth = CHAT_PANE_DEFAULT_WIDTH,
    minWidth = CHAT_PANE_MIN_WIDTH,
    maxWidth: explicitMaxWidth,
    workbenchMinWidth = WORKBENCH_MIN_WIDTH,
    railWidth: explicitRailWidth,
    containerWidth: explicitContainerWidth,
    containerRef: externalContainerRef,
    enabled = true,
  } = options

  const internalContainerRef = useRef<HTMLElement | null>(null)
  const containerRef = externalContainerRef ?? internalContainerRef

  const [savedWidth, setSavedWidth] = useState<number | null>(() => readChatPaneWidth(storageKey))
  const [draftWidth, setDraftWidth] = useState<number | null>(null)
  const [isResizing, setIsResizing] = useState(false)

  const observedWidthRef = useRef<number | null>(null)
  const windowResizedRef = useRef(false)

  const [measuredLayout, setMeasuredLayout] = useState<{
    containerWidth: number
    railWidth: number
  }>(() => ({
    // The element is measured in the effect below, after it mounts; the first render uses the window.
    containerWidth: explicitContainerWidth ?? detectContainerWidth(null, null, false),
    railWidth: explicitRailWidth ?? detectRailWidth(null),
  }))

  useEffect(() => {
    // Measure only while the split is on: outside it the width is unused, and observers would just add work.
    if (!enabled || explicitMaxWidth !== undefined) return

    const syncLayout = () => {
      const el = containerRef.current
      const nextContainerWidth =
        explicitContainerWidth ??
        detectContainerWidth(el, observedWidthRef.current, windowResizedRef.current)
      const nextRailWidth = explicitRailWidth ?? detectRailWidth(el)

      setMeasuredLayout((current) =>
        current.containerWidth === nextContainerWidth && current.railWidth === nextRailWidth
          ? current
          : { containerWidth: nextContainerWidth, railWidth: nextRailWidth },
      )
    }

    syncLayout()

    const el = containerRef.current
    let resizeObserver: ResizeObserver | null = null
    if (el && typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const boxWidth = entry.contentRect?.width ?? 0
          if (boxWidth > 0) {
            observedWidthRef.current = boxWidth
          }
        }
        syncLayout()
      })
      resizeObserver.observe(el)
    }

    let mutationObserver: MutationObserver | null = null
    if (el && typeof MutationObserver !== 'undefined') {
      mutationObserver = new MutationObserver(() => {
        syncLayout()
      })
      mutationObserver.observe(el, {
        attributes: true,
        attributeFilter: ['data-rail-collapsed'],
        subtree: true,
      })
    }

    const handleWindowResize = () => {
      windowResizedRef.current = true
      observedWidthRef.current = null
      syncLayout()
    }

    if (typeof window !== 'undefined') {
      window.addEventListener('resize', handleWindowResize)
    }

    return () => {
      resizeObserver?.disconnect()
      mutationObserver?.disconnect()
      if (typeof window !== 'undefined') {
        window.removeEventListener('resize', handleWindowResize)
      }
    }
  }, [containerRef, enabled, explicitContainerWidth, explicitMaxWidth, explicitRailWidth])

  const maxWidth = useMemo(() => {
    if (explicitMaxWidth !== undefined) {
      return Math.max(Math.round(minWidth), Math.round(explicitMaxWidth))
    }
    const effectiveContainerWidth = explicitContainerWidth ?? measuredLayout.containerWidth
    const effectiveRailWidth = explicitRailWidth ?? measuredLayout.railWidth
    return computeChatPaneMaxWidth(
      effectiveContainerWidth,
      effectiveRailWidth,
      workbenchMinWidth,
      minWidth,
    )
  }, [
    explicitContainerWidth,
    explicitMaxWidth,
    explicitRailWidth,
    measuredLayout.containerWidth,
    measuredLayout.railWidth,
    minWidth,
    workbenchMinWidth,
  ])

  const width = useMemo(() => {
    const target = draftWidth ?? savedWidth ?? defaultWidth
    return clampChatPaneWidth(target, minWidth, maxWidth)
  }, [defaultWidth, draftWidth, maxWidth, minWidth, savedWidth])

  const previewWidth = useCallback(
    (nextWidth: number): number => {
      const clamped = clampChatPaneWidth(nextWidth, minWidth, maxWidth)
      setDraftWidth(clamped)
      return clamped
    },
    [maxWidth, minWidth],
  )

  const commitWidth = useCallback(
    (nextWidth: number): number => {
      const clamped = clampChatPaneWidth(nextWidth, minWidth, maxWidth)
      setDraftWidth(null)
      setSavedWidth(clamped)
      writeChatPaneWidth(clamped, storageKey)
      return clamped
    },
    [maxWidth, minWidth, storageKey],
  )

  const setWidth = useCallback(
    (nextWidth: number, setOptions?: { persist?: boolean }): number => {
      if (setOptions?.persist === false) {
        return previewWidth(nextWidth)
      }
      return commitWidth(nextWidth)
    },
    [commitWidth, previewWidth],
  )

  const resetWidth = useCallback(() => {
    setDraftWidth(null)
    setSavedWidth(null)
    clearChatPaneWidth(storageKey)
  }, [storageKey])

  const style = useMemo<CSSProperties | undefined>(() => {
    if (!enabled) return undefined
    return { [CHAT_PANE_CSS_VAR]: `${width}px` } as CSSProperties
  }, [enabled, width])

  return {
    width,
    preferredWidth: savedWidth,
    minWidth,
    maxWidth,
    defaultWidth,
    isResizing,
    setIsResizing,
    previewWidth,
    commitWidth,
    setWidth,
    resetWidth,
    containerRef,
    style,
  }
}

export const useChatPaneWidth = useResizableSplit

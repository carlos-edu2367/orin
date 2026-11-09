import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import type { MessageAttachment } from '../../api/conversations'
import './stickyUserMessage.css'

/** A user message as the sticky card needs it. */
export type StickyUserMessageItem = { id: string; content: string; attachments: MessageAttachment[] }

/** Where a rendered user message sits, in the same coordinate space as the viewport top. */
export type MessageBox = { id: string; top: number; bottom: number }

/** Attribute ChatPage puts on each user bubble so the card can find it. */
export const STICKY_USER_MESSAGE_ATTR = 'data-sticky-user-id'

/**
 * Which user message the sticky card should show.
 *
 * The candidate is the last user message whose top has scrolled above the viewport top —
 * the turn being read below it belongs to that message. While any meaningful part of the
 * message itself is still on screen (`bottom > viewportTop + visibleSlack`) there is
 * nothing to pin, and with no message above the viewport there is nothing to show.
 */
export function selectStickyUserMessage(boxes: readonly MessageBox[], viewportTop: number, visibleSlack = 8): string | null {
  let candidate: MessageBox | null = null
  for (const box of boxes) {
    if (box.top < viewportTop && (candidate === null || box.top >= candidate.top)) candidate = box
  }
  if (!candidate) return null
  if (candidate.bottom > viewportTop + visibleSlack) return null
  return candidate.id
}

function prefersReducedMotion(): boolean {
  try {
    return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

function userMessageNodes(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(`[${STICKY_USER_MESSAGE_ATTR}]`))
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1).replace('.', ',')} MB`
}

const CLAMP_FALLBACK_CHARACTERS = 160

/**
 * A card pinned to the top of the conversation scroll with the user message whose answer is
 * being read, like the sticky prompt of Claude Code. It reads layout only inside a
 * rAF-throttled scroll handler and re-renders only when the pinned message changes, so a
 * streaming answer does not make it re-render on every token.
 */
export function StickyUserMessage({
  scrollRef,
  items,
  reducedMotion = false,
}: {
  scrollRef: RefObject<HTMLElement | null>
  items: readonly StickyUserMessageItem[]
  reducedMotion?: boolean
}) {
  const [activeId, setActiveId] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(false)
  const textRef = useRef<HTMLSpanElement>(null)
  const itemsKey = items.map((item) => item.id).join('\u0000')

  useEffect(() => {
    const container = scrollRef.current
    if (!container) return
    let frame: number | null = null
    let pending = false
    const measure = () => {
      frame = null
      pending = false
      const viewportTop = container.getBoundingClientRect().top
      const boxes = userMessageNodes(container).map((node) => {
        const rect = node.getBoundingClientRect()
        return { id: node.getAttribute(STICKY_USER_MESSAGE_ATTR) ?? '', top: rect.top, bottom: rect.bottom }
      })
      const next = selectStickyUserMessage(boxes, viewportTop)
      setActiveId((current) => (current === next ? current : next))
    }
    const schedule = () => {
      if (pending) return
      pending = true
      frame = window.requestAnimationFrame(measure)
    }
    measure()
    container.addEventListener('scroll', schedule, { passive: true })
    window.addEventListener('resize', schedule)
    return () => {
      container.removeEventListener('scroll', schedule)
      window.removeEventListener('resize', schedule)
      if (frame !== null) window.cancelAnimationFrame(frame)
    }
  }, [scrollRef, itemsKey])

  const [shownId, setShownId] = useState<string | null>(activeId)
  if (shownId !== activeId) {
    setShownId(activeId)
    setExpanded(false)
  }

  const item = activeId ? items.find((candidate) => candidate.id === activeId) ?? null : null
  const content = item?.content.trim() ?? ''

  useLayoutEffect(() => {
    const node = textRef.current
    if (!node || expanded) return
    const measured = node.clientHeight > 0
      ? node.scrollHeight > node.clientHeight + 1
      : content.length > CLAMP_FALLBACK_CHARACTERS || content.split('\n').length > 2
    setOverflowing(measured)
  }, [content, expanded])

  const scrollToOriginal = useCallback(() => {
    const container = scrollRef.current
    if (!container || !activeId) return
    const selection = typeof window.getSelection === 'function' ? window.getSelection()?.toString() : ''
    if (selection) return
    const target = userMessageNodes(container).find((node) => node.getAttribute(STICKY_USER_MESSAGE_ATTR) === activeId)
    if (!target) return
    const top = container.scrollTop + target.getBoundingClientRect().top - container.getBoundingClientRect().top - 12
    const behavior: 'auto' | 'smooth' = reducedMotion || prefersReducedMotion() ? 'auto' : 'smooth'
    if (typeof container.scrollTo === 'function') container.scrollTo({ top: Math.max(0, top), behavior })
    else container.scrollTop = Math.max(0, top)
  }, [activeId, reducedMotion, scrollRef])

  if (!item) return null

  return (
    <div className="sticky-user-message__anchor">
      <div
        className="sticky-user-message"
        data-testid="sticky-user-message"
        data-message-id={item.id}
        data-expanded={expanded ? 'true' : 'false'}
      >
        {item.attachments.length > 0 && (
          <div className="sticky-user-message__chips">
            {item.attachments.map((attachment) => (
              <span key={attachment.path} className="sticky-user-message__chip" title={attachment.original_name}>
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M9.5 2.2H4.6A1.4 1.4 0 0 0 3.2 3.6v8.8a1.4 1.4 0 0 0 1.4 1.4h6.8a1.4 1.4 0 0 0 1.4-1.4V5.5L9.5 2.2Z" />
                  <path d="M9.2 2.2v3.4h3.6" />
                </svg>
                <span className="sticky-user-message__chip-name">{attachment.original_name}</span>
                {formatBytes(attachment.bytes) && (
                  <span className="sticky-user-message__chip-meta">{formatBytes(attachment.bytes)}</span>
                )}
              </span>
            ))}
          </div>
        )}
        <div className="sticky-user-message__row">
          <div
            role="button"
            tabIndex={0}
            className="sticky-user-message__body"
            title="Ir para a mensagem"
            aria-label="Ir para a sua mensagem na conversa"
            onClick={scrollToOriginal}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault()
                scrollToOriginal()
              }
            }}
          >
            {content && (
              <span
                ref={textRef}
                className="sticky-user-message__text"
                data-overflowing={overflowing && !expanded ? 'true' : 'false'}
              >
                {content}
              </span>
            )}
          </div>
          {(overflowing || expanded) && (
            <button
              type="button"
              className="sticky-user-message__toggle"
              aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? 'Mostrar menos' : 'Mostrar mais'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

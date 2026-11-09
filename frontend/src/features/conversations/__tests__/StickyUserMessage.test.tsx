import { act, fireEvent, render, screen } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  STICKY_USER_MESSAGE_ATTR,
  StickyUserMessage,
  selectStickyUserMessage,
  type StickyUserMessageItem,
} from '../StickyUserMessage'

describe('selectStickyUserMessage', () => {
  const boxes = [
    { id: 'u1', top: -900, bottom: -860 },
    { id: 'u2', top: -300, bottom: -260 },
    { id: 'u3', top: 400, bottom: 440 },
  ]

  it('shows nothing when no user message is above the viewport', () => {
    expect(selectStickyUserMessage(boxes, -1000)).toBeNull()
    expect(selectStickyUserMessage([], 0)).toBeNull()
  })

  it('picks the last user message whose top scrolled above the viewport top', () => {
    expect(selectStickyUserMessage(boxes, 0)).toBe('u2')
    expect(selectStickyUserMessage(boxes, -500)).toBe('u1')
  })

  it('switches to the next message once it reaches the top', () => {
    expect(selectStickyUserMessage(boxes, 450)).toBe('u3')
  })

  it('hides while the chosen message itself is still visible', () => {
    expect(selectStickyUserMessage([{ id: 'u1', top: -10, bottom: 60 }], 0)).toBeNull()
    expect(selectStickyUserMessage([{ id: 'u1', top: -60, bottom: 4 }], 0)).toBe('u1')
  })

  it('does not depend on the order of the boxes', () => {
    expect(selectStickyUserMessage([...boxes].reverse(), 0)).toBe('u2')
  })
})

type Layout = Record<string, { top: number; bottom: number }>

function rect(top: number, bottom: number): DOMRect {
  return { top, bottom, left: 0, right: 100, width: 100, height: bottom - top, x: 0, y: top, toJSON: () => ({}) } as DOMRect
}

function Harness({ items, layout }: { items: StickyUserMessageItem[]; layout: { current: Layout } }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  return (
    <div
      data-testid="scroll"
      ref={(node) => {
        scrollRef.current = node
        if (node) node.getBoundingClientRect = () => rect(0, 600)
      }}
    >
      <StickyUserMessage scrollRef={scrollRef} items={items} reducedMotion />
      {items.map((item) => (
        <article
          key={item.id}
          {...{ [STICKY_USER_MESSAGE_ATTR]: item.id }}
          ref={(node) => {
            if (node) node.getBoundingClientRect = () => rect(layout.current[item.id]?.top ?? 0, layout.current[item.id]?.bottom ?? 0)
          }}
        >
          {item.content}
        </article>
      ))}
    </div>
  )
}

describe('StickyUserMessage', () => {
  const items: StickyUserMessageItem[] = [
    { id: 'u1', content: 'Primeira pergunta curta', attachments: [] },
    {
      id: 'u2',
      content: 'Segunda pergunta '.repeat(20),
      attachments: [{ path: 'uploads/a.png', original_name: 'tela.png', media_type: 'image/png', kind: 'image', bytes: 2048 }],
    },
  ]

  beforeEach(() => {
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      callback(0)
      return 1
    })
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('appears for the message above the viewport, switches, and hides when it is visible again', () => {
    const layout = { current: { u1: { top: 20, bottom: 60 }, u2: { top: 800, bottom: 860 } } as Layout }
    render(<Harness items={items} layout={layout} />)
    expect(screen.queryByTestId('sticky-user-message')).toBeNull()

    layout.current = { u1: { top: -300, bottom: -260 }, u2: { top: 400, bottom: 460 } }
    act(() => { fireEvent.scroll(screen.getByTestId('scroll')) })
    const card = screen.getByTestId('sticky-user-message')
    expect(card).toHaveAttribute('data-message-id', 'u1')
    expect(card).toHaveTextContent('Primeira pergunta curta')
    expect(screen.queryByRole('button', { name: 'Mostrar mais' })).toBeNull()

    layout.current = { u1: { top: -1300, bottom: -1260 }, u2: { top: -200, bottom: -100 } }
    act(() => { fireEvent.scroll(screen.getByTestId('scroll')) })
    expect(screen.getByTestId('sticky-user-message')).toHaveAttribute('data-message-id', 'u2')
    expect(screen.getByText('tela.png')).toBeInTheDocument()
    expect(screen.getByText('2 KB')).toBeInTheDocument()

    layout.current = { u1: { top: 10, bottom: 50 }, u2: { top: 500, bottom: 560 } }
    act(() => { fireEvent.scroll(screen.getByTestId('scroll')) })
    expect(screen.queryByTestId('sticky-user-message')).toBeNull()
  })

  it('expands and collapses long text, and scrolls to the original message on click', () => {
    const layout = { current: { u1: { top: -1300, bottom: -1260 }, u2: { top: -200, bottom: -100 } } as Layout }
    render(<Harness items={items} layout={layout} />)
    const scroll = screen.getByTestId('scroll')
    const scrollTo = vi.fn()
    scroll.scrollTo = scrollTo as unknown as typeof scroll.scrollTo

    const more = screen.getByRole('button', { name: 'Mostrar mais' })
    fireEvent.click(more)
    expect(screen.getByTestId('sticky-user-message')).toHaveAttribute('data-expanded', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Mostrar menos' }))
    expect(screen.getByTestId('sticky-user-message')).toHaveAttribute('data-expanded', 'false')

    fireEvent.click(screen.getByRole('button', { name: 'Ir para a sua mensagem na conversa' }))
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'auto' })
  })
})

import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { parseConversationActivityEvent } from '../../../api/conversations'
import { PushNotificationToast, isFreshNotice } from '../PushNotificationToast'

describe('notification.pushed events', () => {
  it('carry the bounded message', () => {
    const event = parseConversationActivityEvent({
      event_id: 'evt-notice-1', event_type: 'notification.pushed', turn_id: 'turn-1', agent_id: 'agent:main',
      summary: 'Notificação: Build finished', occurred_at: '2026-10-19T12:00:00Z',
      payload: { message: 'Build finished', label: 'Build finished' },
    }, '7')
    expect(event.type).toBe('notification.pushed')
    expect(event.kind).toBe('lifecycle')
    expect(event.notificationMessage).toBe('Build finished')
  })

  it('ignore a message on any other event', () => {
    const event = parseConversationActivityEvent({
      event_id: 'evt-other-1', event_type: 'turn.completed', agent_id: 'agent:main', summary: '', payload: { message: 'x' },
    }, '8')
    expect(event.notificationMessage).toBeUndefined()
  })
})

describe('PushNotificationToast', () => {
  afterEach(() => vi.useRealTimers())

  it('shows the message politely and closes on demand', () => {
    const onDismiss = vi.fn()
    render(<PushNotificationToast notice={{ id: 'n1', message: 'Build finished' }} onDismiss={onDismiss} />)
    const toast = screen.getByRole('status')
    expect(toast).toHaveTextContent('Build finished')
    expect(toast).toHaveAttribute('aria-live', 'polite')
    fireEvent.click(screen.getByRole('button', { name: 'Fechar notificação' }))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('closes itself after its duration', () => {
    vi.useFakeTimers()
    const onDismiss = vi.fn()
    render(<PushNotificationToast notice={{ id: 'n1', message: 'x' }} onDismiss={onDismiss} durationMs={1000} />)
    act(() => { vi.advanceTimersByTime(999) })
    expect(onDismiss).not.toHaveBeenCalled()
    act(() => { vi.advanceTimersByTime(1) })
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('keeps its timer when the page re-renders with a new callback', () => {
    vi.useFakeTimers()
    const first = vi.fn()
    const second = vi.fn()
    const notice = { id: 'n1', message: 'x' }
    const { rerender } = render(<PushNotificationToast notice={notice} onDismiss={first} durationMs={1000} />)
    act(() => { vi.advanceTimersByTime(600) })
    rerender(<PushNotificationToast notice={notice} onDismiss={second} durationMs={1000} />)
    act(() => { vi.advanceTimersByTime(400) })
    expect(first).not.toHaveBeenCalled()
    expect(second).toHaveBeenCalledTimes(1)
  })

  it('renders nothing without a notice', () => {
    const { container } = render(<PushNotificationToast notice={null} onDismiss={() => undefined} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('treats markup in the message as text', () => {
    render(<PushNotificationToast notice={{ id: 'n1', message: '<img src=x onerror=alert(1)>' }} onDismiss={() => undefined} />)
    expect(screen.getByRole('status')).toHaveTextContent('<img src=x onerror=alert(1)>')
    expect(document.querySelector('.push-toast img')).toBeNull()
  })

  it('only fresh events pop up', () => {
    const now = Date.parse('2026-10-19T12:00:00Z')
    expect(isFreshNotice('2026-10-19T11:59:30Z', now)).toBe(true)
    expect(isFreshNotice('2026-10-19T11:50:00Z', now)).toBe(false)
    expect(isFreshNotice(undefined, now)).toBe(false)
    expect(isFreshNotice('not a date', now)).toBe(false)
  })
})

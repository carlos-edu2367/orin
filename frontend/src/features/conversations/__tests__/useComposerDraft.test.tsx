import { act, render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  MAX_DRAFT_CHARACTERS,
  conversationDraftKey,
  newSessionDraftKey,
  pruneDrafts,
  readDraft,
  useComposerDraft,
  writeDraft,
} from '../useComposerDraft'

function Harness({ draftKey, initial = '', skipRestore = false, onClear }: {
  draftKey: string | null
  initial?: string
  skipRestore?: boolean
  onClear?: (clear: () => void) => void
}) {
  const [value, setValue] = useState(initial)
  const { clearDraft } = useComposerDraft(draftKey, value, setValue, { skipRestore })
  onClear?.(clearDraft)
  return <textarea aria-label="draft" value={value} onChange={(event) => setValue(event.target.value)} />
}

const input = () => screen.getByLabelText('draft') as HTMLTextAreaElement

describe('useComposerDraft', () => {
  beforeEach(() => {
    window.localStorage.clear()
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('builds stable keys per context', () => {
    expect(newSessionDraftKey()).toBe('orin.draft.new:none')
    expect(newSessionDraftKey('p1')).toBe('orin.draft.new:p1')
    expect(conversationDraftKey('c1')).toBe('orin.draft.conv:c1')
  })

  it('writes debounced while typing and restores on remount', () => {
    const key = newSessionDraftKey()
    const first = render(<Harness draftKey={key} />)
    fireEvent.change(input(), { target: { value: 'meio prompt' } })
    expect(readDraft(key)).toBeNull()
    act(() => { vi.advanceTimersByTime(300) })
    expect(readDraft(key)).toBe('meio prompt')
    first.unmount()

    render(<Harness draftKey={key} />)
    expect(input().value).toBe('meio prompt')
  })

  it('writes immediately on unmount, before the debounce fires', () => {
    const key = newSessionDraftKey()
    const view = render(<Harness draftKey={key} />)
    fireEvent.change(input(), { target: { value: 'saindo rápido' } })
    view.unmount()
    expect(readDraft(key)).toBe('saindo rápido')
  })

  it('keeps one draft per key when the key changes', () => {
    const view = render(<Harness draftKey={conversationDraftKey('a')} />)
    fireEvent.change(input(), { target: { value: 'texto de A' } })
    view.rerender(<Harness draftKey={conversationDraftKey('b')} />)
    expect(input().value).toBe('')
    expect(readDraft(conversationDraftKey('a'))).toBe('texto de A')
    fireEvent.change(input(), { target: { value: 'texto de B' } })
    view.rerender(<Harness draftKey={conversationDraftKey('a')} />)
    expect(input().value).toBe('texto de A')
    expect(readDraft(conversationDraftKey('b'))).toBe('texto de B')
  })

  it('emptying the text (a successful send) forgets the draft; putting it back (a failed send) keeps it', () => {
    const key = conversationDraftKey('c')
    const view = render(<Harness draftKey={key} />)
    fireEvent.change(input(), { target: { value: 'enviar isto' } })
    act(() => { vi.advanceTimersByTime(300) })
    fireEvent.change(input(), { target: { value: '' } })
    fireEvent.change(input(), { target: { value: 'enviar isto' } })
    view.unmount()
    expect(readDraft(key)).toBe('enviar isto')

    const again = render(<Harness draftKey={key} />)
    fireEvent.change(input(), { target: { value: '' } })
    act(() => { vi.advanceTimersByTime(300) })
    again.unmount()
    expect(readDraft(key)).toBeNull()
  })

  it('clearDraft forgets the draft even though the text is still on screen at unmount', () => {
    const key = newSessionDraftKey()
    let clear: () => void = () => undefined
    const view = render(<Harness draftKey={key} onClear={(fn) => { clear = fn }} />)
    fireEvent.change(input(), { target: { value: 'nova sessão' } })
    act(() => { clear() })
    act(() => { vi.advanceTimersByTime(1000) })
    expect(readDraft(key)).toBeNull()
    view.unmount()
    expect(readDraft(key)).toBeNull()
  })

  it('skipRestore keeps an explicit initial value instead of the stored draft', () => {
    const key = newSessionDraftKey()
    writeDraft(key, 'antigo')
    render(<Harness draftKey={key} initial="do link" skipRestore />)
    expect(input().value).toBe('do link')
  })

  it('caps the stored size and ignores storage failures', () => {
    const key = newSessionDraftKey()
    writeDraft(key, 'x'.repeat(MAX_DRAFT_CHARACTERS + 50))
    expect(readDraft(key)?.length).toBe(MAX_DRAFT_CHARACTERS)

    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    expect(() => writeDraft(key, 'y')).not.toThrow()
    expect(readDraft(key)).toBeNull()
    expect(() => render(<Harness draftKey={key} />)).not.toThrow()
    setItem.mockRestore()
    getItem.mockRestore()
  })

  it('prunes old drafts and keeps the newest ones', () => {
    const now = 1_000_000_000_000
    writeDraft('orin.draft.conv:old', 'velho', now - 40 * 24 * 60 * 60 * 1000)
    writeDraft('orin.draft.conv:new1', 'n1', now - 1000)
    writeDraft('orin.draft.conv:new2', 'n2', now - 10)
    window.localStorage.setItem('unrelated', 'keep')
    pruneDrafts(now, 30 * 24 * 60 * 60 * 1000, 1)
    expect(readDraft('orin.draft.conv:old')).toBeNull()
    expect(readDraft('orin.draft.conv:new1')).toBeNull()
    expect(readDraft('orin.draft.conv:new2')).toBe('n2')
    expect(window.localStorage.getItem('unrelated')).toBe('keep')
  })
})

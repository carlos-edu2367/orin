import { afterEach, describe, expect, it, vi } from 'vitest'
import { pendingMessageId } from '../pendingMessageId'

describe('pendingMessageId', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('returns distinct ids matching pending-<n>-<timestamp> even under a frozen clock', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(1700000000000))
    const first = pendingMessageId()
    const second = pendingMessageId()

    const match1 = first.match(/^pending-(\d+)-(\d+)$/)
    const match2 = second.match(/^pending-(\d+)-(\d+)$/)
    expect(match1).not.toBeNull()
    expect(match2).not.toBeNull()
    expect(Number(match2![1])).toBe(Number(match1![1]) + 1)
    expect(match1![2]).toBe('1700000000000')
    expect(match2![2]).toBe('1700000000000')
    expect(first).not.toBe(second)
  })

  it('never collides when clock moves backwards or returns non-integer/negative/NaN values', () => {
    const spy = vi
      .spyOn(Date, 'now')
      .mockReturnValueOnce(1700000005000)
      .mockReturnValueOnce(1700000001000)
      .mockReturnValueOnce(1700000001000.75)
      .mockReturnValueOnce(-500)
      .mockReturnValueOnce(Number.NaN)

    const seen = new Set<string>()
    for (let i = 0; i < 5; i += 1) {
      const id = pendingMessageId()
      expect(id).toMatch(/^pending-\d+-\d+$/)
      expect(seen.has(id)).toBe(false)
      seen.add(id)
    }
    expect(spy).toHaveBeenCalledTimes(5)
  })
})

import { describe, expect, it, vi } from 'vitest'
import { pendingMessageId } from '../pendingMessageId'

describe('pendingMessageId', () => {
  it('returns distinct ids starting with pending- even within the same millisecond', () => {
    const spy = vi.spyOn(Date, 'now').mockReturnValue(1700000000000)
    try {
      const first = pendingMessageId()
      const second = pendingMessageId()
      expect(first.startsWith('pending-')).toBe(true)
      expect(second.startsWith('pending-')).toBe(true)
      expect(first).not.toBe(second)
    } finally {
      spy.mockRestore()
    }
  })
})

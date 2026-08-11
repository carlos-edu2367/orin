import { createElement, createRef, useEffect } from 'react'
import { act, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  getPerformanceProfile,
  usePerformanceProfile,
  type PerformanceProfile,
} from '../../src/features/agents/usePerformanceProfile'

// jsdom does not implement matchMedia or IntersectionObserver; both are reset after
// every test so a mock from one test can never leak into the next (mirrors the
// window.matchMedia cleanup already used by agentGraphProjection.test.ts).
afterEach(() => {
  // @ts-expect-error restoring the undefined default, not a typed jsdom API
  delete window.matchMedia
  // @ts-expect-error restoring the undefined default, not a typed jsdom API
  delete window.IntersectionObserver
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true })
})

function mockReducedMotion(initial: boolean) {
  let current = initial
  const listeners = new Set<() => void>()
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    get matches() {
      return current && query.includes('reduce')
    },
    media: query,
    onchange: null,
    addEventListener: (_: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
  return {
    set: (matches: boolean) => {
      current = matches
      listeners.forEach((listener) => listener())
    },
  }
}

type ObserverCallback = (entries: Array<{ isIntersecting: boolean }>) => void

function mockIntersectionObserver() {
  let callback: ObserverCallback = () => {}
  class FakeIntersectionObserver {
    constructor(cb: ObserverCallback) {
      callback = cb
    }
    observe() {}
    disconnect() {}
    unobserve() {}
  }
  // @ts-expect-error test-only global stand-in for the browser API
  window.IntersectionObserver = FakeIntersectionObserver
  return {
    setIntersecting: (isIntersecting: boolean) => callback([{ isIntersecting }]),
  }
}

function mockDevicePixelRatio(ratio: number) {
  Object.defineProperty(window, 'devicePixelRatio', { value: ratio, configurable: true })
}

function setTabVisible(visible: boolean) {
  Object.defineProperty(document, 'visibilityState', { value: visible ? 'visible' : 'hidden', configurable: true })
}

describe('getPerformanceProfile', () => {
  it('always returns "static" when reducedMotion is true, regardless of visible or devicePixelRatio', () => {
    expect(getPerformanceProfile({ reducedMotion: true, visible: true, devicePixelRatio: 1 })).toBe('static')
    expect(getPerformanceProfile({ reducedMotion: true, visible: false, devicePixelRatio: 1 })).toBe('static')
    expect(getPerformanceProfile({ reducedMotion: true, visible: true, devicePixelRatio: 4 })).toBe('static')
    expect(getPerformanceProfile({ reducedMotion: true, visible: false, devicePixelRatio: 4 })).toBe('static')
  })

  it('returns "reduced" when visible is false and reducedMotion is false', () => {
    expect(getPerformanceProfile({ reducedMotion: false, visible: false, devicePixelRatio: 1 })).toBe('reduced')
    expect(getPerformanceProfile({ reducedMotion: false, visible: false, devicePixelRatio: 2 })).toBe('reduced')
  })

  it('returns "full" only for a visible canvas, no reduced-motion preference, and a devicePixelRatio at or below the documented threshold of 3', () => {
    expect(getPerformanceProfile({ reducedMotion: false, visible: true, devicePixelRatio: 1 })).toBe('full')
    expect(getPerformanceProfile({ reducedMotion: false, visible: true, devicePixelRatio: 2 })).toBe('full')
    expect(getPerformanceProfile({ reducedMotion: false, visible: true, devicePixelRatio: 3 })).toBe('full')
  })

  it('downgrades to "reduced" above the devicePixelRatio threshold, treating a very high DPR as a signal of a low-power device', () => {
    expect(getPerformanceProfile({ reducedMotion: false, visible: true, devicePixelRatio: 3.01 })).toBe('reduced')
    expect(getPerformanceProfile({ reducedMotion: false, visible: true, devicePixelRatio: 4 })).toBe('reduced')
  })

  it('is pure and deterministic for the same input', () => {
    const input = Object.freeze({ reducedMotion: false, visible: true, devicePixelRatio: 2 })
    const first = getPerformanceProfile(input)
    const second = getPerformanceProfile(input)
    expect(first).toBe(second)
    expect(input.devicePixelRatio).toBe(2)
  })
})

function ProfileProbe({ targetRef, onProfile }: { targetRef: ReturnType<typeof createRef<HTMLDivElement>>; onProfile: (profile: PerformanceProfile) => void }) {
  const profile = usePerformanceProfile(targetRef)
  useEffect(() => {
    onProfile(profile)
  }, [profile, onProfile])
  return createElement('div', { ref: targetRef })
}

describe('usePerformanceProfile', () => {
  it('reports "full" once the target is intersecting, the tab is visible, and there is no reduced-motion preference', () => {
    mockReducedMotion(false)
    const intersection = mockIntersectionObserver()
    mockDevicePixelRatio(2)
    setTabVisible(true)
    const targetRef = createRef<HTMLDivElement>()
    const profiles: PerformanceProfile[] = []

    render(createElement(ProfileProbe, { targetRef, onProfile: (profile) => profiles.push(profile) }))
    act(() => intersection.setIntersecting(true))

    expect(profiles.at(-1)).toBe('full')
  })

  it('reports "reduced" once the tab becomes hidden, without needing a reduced-motion preference', () => {
    mockReducedMotion(false)
    const intersection = mockIntersectionObserver()
    mockDevicePixelRatio(2)
    setTabVisible(true)
    const targetRef = createRef<HTMLDivElement>()
    const profiles: PerformanceProfile[] = []

    render(createElement(ProfileProbe, { targetRef, onProfile: (profile) => profiles.push(profile) }))
    act(() => intersection.setIntersecting(true))
    expect(profiles.at(-1)).toBe('full')

    act(() => {
      setTabVisible(false)
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(profiles.at(-1)).toBe('reduced')
  })

  it('reports "static" once the reduced-motion preference changes, even while fully visible', () => {
    const reducedMotion = mockReducedMotion(false)
    const intersection = mockIntersectionObserver()
    mockDevicePixelRatio(2)
    setTabVisible(true)
    const targetRef = createRef<HTMLDivElement>()
    const profiles: PerformanceProfile[] = []

    render(createElement(ProfileProbe, { targetRef, onProfile: (profile) => profiles.push(profile) }))
    act(() => intersection.setIntersecting(true))
    expect(profiles.at(-1)).toBe('full')

    act(() => reducedMotion.set(true))

    expect(profiles.at(-1)).toBe('static')
  })

  it('never mounts an IntersectionObserver-dependent "full" profile before the target has reported intersection', () => {
    mockReducedMotion(false)
    mockIntersectionObserver()
    mockDevicePixelRatio(2)
    setTabVisible(true)
    const targetRef = createRef<HTMLDivElement>()
    const profiles: PerformanceProfile[] = []

    render(createElement(ProfileProbe, { targetRef, onProfile: (profile) => profiles.push(profile) }))

    expect(profiles.at(-1)).not.toBe('full')
  })
})

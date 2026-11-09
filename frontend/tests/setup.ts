import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Motion measures page keyframes during mounted UI tests. jsdom exposes the
// method but deliberately throws, unlike a real browser where it is harmless.
Object.defineProperty(window, 'scrollTo', { configurable: true, value: () => undefined })

afterEach(() => {
  cleanup()
  // Composer drafts are persisted on unmount (useComposerDraft); one test's text must not
  // reappear in the next test's composer.
  try {
    const keys: string[] = []
    for (let index = 0; index < window.localStorage.length; index += 1) {
      const key = window.localStorage.key(index)
      if (key?.startsWith('orin.draft.')) keys.push(key)
    }
    for (const key of keys) window.localStorage.removeItem(key)
  } catch {
    // Storage unavailable in this environment: nothing to clean.
  }
})

import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Motion measures page keyframes during mounted UI tests. jsdom exposes the
// method but deliberately throws, unlike a real browser where it is harmless.
Object.defineProperty(window, 'scrollTo', { configurable: true, value: () => undefined })

afterEach(() => cleanup())

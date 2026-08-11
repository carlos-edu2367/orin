import { describe, expect, it } from 'vitest'
import styles from '../../src/styles/agentos.css?raw'

describe('chat header surface', () => {
  it('uses a translucent background with a light cross-browser backdrop blur', () => {
    expect(styles).toMatch(/\.chat__bar\s*\{[^}]*background:\s*rgb\(var\(--orin-ink-rgb\)\s*\/\s*\.62\)/)
    expect(styles).toMatch(/\.chat__bar\s*\{[^}]*backdrop-filter:\s*blur\(12px\)/)
    expect(styles).toMatch(/\.chat__bar\s*\{[^}]*-webkit-backdrop-filter:\s*blur\(12px\)/)
  })
})

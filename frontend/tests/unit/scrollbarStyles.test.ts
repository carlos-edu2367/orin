import { describe, expect, it } from 'vitest'
import stylesheet from '../../src/styles/index.css?raw'

describe('global scrollbar styling', () => {
  it('defines the shared dark scrollbar treatment for every scrollable surface', () => {
    expect(stylesheet).toMatch(/scrollbar-color:\s*rgb\(var\(--orin-text-rgb\)\s*\/\s*\.24\)\s*transparent/)
    expect(stylesheet).toMatch(/scrollbar-width:\s*thin/)
    expect(stylesheet).toMatch(/\*::-webkit-scrollbar\s*\{[^}]*width:\s*8px/)
    expect(stylesheet).toMatch(/\*::-webkit-scrollbar-thumb\s*\{[^}]*background:\s*rgb\(var\(--orin-text-rgb\)\s*\/\s*\.24\)/)
    expect(stylesheet).toMatch(/\*::-webkit-scrollbar-thumb:hover\s*\{[^}]*background:\s*var\(--orin-accent\)/)
  })
})

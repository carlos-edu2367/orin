import { describe, expect, it } from 'vitest'
import stylesheet from '../../src/styles/agentos.css?raw'

const css = stylesheet

describe('settings styles', () => {
  it('defines the responsive shell, grid, drawer and reduced-motion reveal', () => {
    expect(css).toContain('grid-template-columns: 216px')
    expect(css).toContain('repeat(auto-fill, minmax(200px, 1fr))')
    expect(css).toContain('.provider-card')
    expect(css).toContain('@media (prefers-reduced-motion: no-preference)')
    expect(css).toContain('@media (max-width: 900px)')
    expect(css).toContain('.settings-drawer { position: static; width: 100%')
    expect(css).toContain('.settings-nav__pending')
  })

  it('uses a readable status token instead of faint text for cards', () => {
    expect(css).toContain('color: var(--mono-readable)')
  })
})

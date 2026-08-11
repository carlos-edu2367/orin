import { describe, expect, it } from 'vitest'
import html from '../../index.html?raw'
import agentosStyles from '../../src/styles/agentos.css?raw'
import themeStyles from '../../src/styles/theme.css?raw'
import homeSource from '../../src/app/Home.tsx?raw'

describe('Orin visible identity', () => {
  it('exposes the Orin name and violet theme tokens at the app boundary', () => {
    expect(html).toContain('<title>Orin')
    expect(html).toContain('rel="icon"')
    expect(html).toContain('orin-favicon.svg')
    expect(themeStyles).toContain('--orin-accent:')
    expect(themeStyles).toContain('--orin-surface:')
    expect(agentosStyles).toContain('var(--orin-accent)')
  })

  it('uses the reusable brand component in the home surface', () => {
    expect(homeSource).toContain('<Brand />')
  })
})

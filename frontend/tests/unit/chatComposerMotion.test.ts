/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/styles/agentos.css'), 'utf8')

describe('chat composer motion', () => {
  it('uses a gentle fade and slide duration for the bottom reveal', () => {
    expect(stylesheet).toMatch(/\.chat__foot > \* \{[\s\S]*transition: opacity 0\.45s var\(--ease\), transform 0\.45s var\(--ease\)/)
  })

  it('keeps the two chat panes independently scrollable in a viewport-height shell', () => {
    expect(stylesheet).toMatch(/\.chat \{[\s\S]*height: 100dvh;[\s\S]*overflow: hidden;/)
    expect(stylesheet).toMatch(/\.workspace-navigation \{[\s\S]*overflow-y: auto;/)
    expect(stylesheet).toMatch(/\.chat__scroll \{[^}]*height: 100%;[^}]*overflow-y: auto;/)
  })

  it('uses the full bottom of the chat column as the composer reveal target', () => {
    expect(stylesheet).toMatch(/\.chat__foot \{[\s\S]*width: 100%;[\s\S]*padding: 28px 24px 22px;/)
    expect(stylesheet).toMatch(/\.chat__foot > \* \{[\s\S]*width: min\(100%, 760px\);/)
  })
})

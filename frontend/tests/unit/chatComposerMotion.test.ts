/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import process from 'node:process'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/styles/agentos.css'), 'utf8')

describe('chat composer motion', () => {
  it('uses a gentle fade and slide duration for the bottom reveal', () => {
    expect(stylesheet).toMatch(/\.chat__foot > \* \{[\s\S]*transition: opacity 0\.45s var\(--ease\), transform 0\.45s var\(--ease\)/)
  })

  it('keeps the two chat panes independently scrollable in a viewport-height shell', () => {
    expect(stylesheet).toMatch(/\.home \{[\s\S]*height: 100dvh;[\s\S]*overflow: hidden;/)
    expect(stylesheet).toMatch(/\.chat \{[\s\S]*height: 100dvh;[\s\S]*overflow: hidden;/)
    expect(stylesheet).toMatch(/\.workspace-navigation \{[\s\S]*overflow-y: auto;/)
    expect(stylesheet).toMatch(/\.chat__scroll \{[^}]*height: 100%;[^}]*overflow-y: auto;/)
  })

  it('keeps sidebar actions outside the scrollable navigation list', () => {
    expect(stylesheet).toMatch(/\.project-navigation \{[^}]*display: flex;[^}]*flex-direction: column;/)
    expect(stylesheet).toMatch(/\.project-navigation__scroll \{[^}]*flex: 1;[^}]*overflow-y: auto;/)
    expect(stylesheet).toMatch(/\.project-navigation__actions \{[^}]*border-top: 1px solid var\(--line\)[^}]*background: transparent;/)
  })

  it('uses the full bottom of the chat column as the composer reveal target', () => {
    expect(stylesheet).toMatch(/\.chat__foot \{[\s\S]*width: 100%;[\s\S]*padding: 28px 24px 22px;/)
    expect(stylesheet).toMatch(/\.chat__foot > \* \{[\s\S]*width: min\(100%, 760px\);/)
  })

  it('keeps the return-to-latest action above the composer hit area', () => {
    expect(stylesheet).toMatch(/\.chat__new-activity \{[\s\S]*z-index: 30;[\s\S]*right: 24px;[\s\S]*bottom: 174px;/)
  })
})

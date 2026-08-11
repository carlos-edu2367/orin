/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/styles/agentos.css'), 'utf8')

describe('chat composer motion', () => {
  it('uses a gentle fade and slide duration for the fixed composer', () => {
    expect(stylesheet).toMatch(/\.chat__foot > \* \{[\s\S]*transition: opacity 0\.45s var\(--ease\), transform 0\.45s var\(--ease\)/)
  })
})

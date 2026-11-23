import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { chatCommandHandlers } from '../../commands/useChatCommands'
import { sanitizeAssistantText } from '../residualToolBlocks'

// The same file the backend test (tests/unit/agentic/test_text_sanitize.py) reads: the two sides cannot drift apart.
// Vitest runs from `frontend/`, so the repository root is one level up.
const FIXTURE = resolve(globalThis.process.cwd(), '..', 'tests', 'fixtures', 'text_tool_leak_cases.json')

type LeakCase = { name: string; input: string; expected: string }
const cases: LeakCase[] = JSON.parse(readFileSync(FIXTURE, 'utf-8')).cases

// The backend keeps whitespace as the model wrote it; the page only renders what stays, so compare the words.
const words = (text: string) => text.split(/\s+/).filter(Boolean).join(' ')

describe('text tool leak fixtures shared with the backend', () => {
  it('has the cases the contract requires', () => {
    const names = cases.map((item) => item.name).join(' | ')
    for (const needed of ['print', 'html-escaped', 'code fence', 'legitimate result', '[Tool Use]', 'Orin log', 'without a closing', 'single backticks', 'double backticks', 'middle of a sentence', 'markdown report']) {
      expect(names).toContain(needed)
    }
  })

  it.each(cases.map((item) => [item.name, item] as const))('%s', (_name, item) => {
    expect(words(sanitizeAssistantText(item.input))).toBe(words(item.expected))
  })

  it('keeps both prose lines of the original print', () => {
    const printed = cases.find((item) => item.name.startsWith('print'))!
    const out = sanitizeAssistantText(printed.input)
    expect(out).toContain('Por código: os 10 PACOTE_344')
    expect(out).toContain('listo os 19 restantes para ler.')
    expect(out).not.toMatch(/invoke|function_results|<\/output>/)
  })

  it('/copy and /export carry the cleaned text of an old saved message', async () => {
    const printed = cases.find((item) => item.name.startsWith('print'))!
    const copied: string[] = []
    const downloads: string[] = []
    const handlers = chatCommandHandlers({
      conversationId: 'c1',
      title: 'Conversa',
      messages: [
        { id: 'm1', role: 'user', content: 'oi' },
        { id: 'm2', role: 'assistant', content: printed.input },
      ],
      notify: () => {},
      copyText: async (text: string) => {
        copied.push(text)
      },
      download: (_name: string, text: string) => {
        downloads.push(text)
      },
    } as unknown as Parameters<typeof chatCommandHandlers>[0])

    await handlers.copy('')
    handlers.export('')

    expect(copied[0]).toContain('listo os 19 restantes para ler.')
    expect(copied[0]).not.toContain('invoke')
    expect(downloads[0]).toContain('Por código')
    expect(downloads[0]).not.toContain('function_results')
  })
})

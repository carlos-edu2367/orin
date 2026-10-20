import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HooksSection } from '../../src/features/codeSettings/HooksSection'
import { KeybindingsSection } from '../../src/features/codeSettings/KeybindingsSection'

type Call = { url: string; method: string; body: unknown }

function stubFetch(answer: (call: Call) => { body: unknown } | undefined): Call[] {
  const calls: Call[] = []
  globalThis.fetch = vi.fn(async (input: Parameters<typeof fetch>[0], init?: NonNullable<Parameters<typeof fetch>[1]>) => {
    const call = { url: String(input), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined }
    calls.push(call)
    const result = answer(call) ?? { body: {} }
    return new Response(JSON.stringify(result.body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }) as unknown as typeof fetch
  return calls
}

const at = (path: string) => ({ children }: { children: ReactNode }) => <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>

afterEach(() => { vi.restoreAllMocks() })

describe('W7-R4 the hooks editor shows what was saved', () => {
  it('keeps the saved text in the textarea, disables Save and shows "Salvo."', async () => {
    const next = { Stop: [{ hooks: [{ type: 'command', command: 'echo fim' }] }] }
    // The reload after the save still answers the old, empty hooks: the editor must not fall back to them.
    const calls = stubFetch(({ method }) => (method === 'PUT'
      ? { body: { disabled: false, effective: [], errors: [], editable: { user: { file: 'C:/u/.orin/settings.json', hooks: next } } } }
      : { body: { disabled: false, effective: [], errors: [], editable: { user: { file: 'C:/u/.orin/settings.json', hooks: {} } } } }))
    render(<HooksSection />, { wrapper: at('/settings/hooks') })
    const editor = await screen.findByRole('textbox', { name: 'Hooks deste arquivo (JSON)' }) as HTMLTextAreaElement
    fireEvent.change(editor, { target: { value: JSON.stringify(next) } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar' }))
    expect(await screen.findByText('Salvo.')).toBeInTheDocument()
    await waitFor(() => expect(calls.some((call) => call.method === 'PUT')).toBe(true))
    expect((screen.getByRole('textbox', { name: 'Hooks deste arquivo (JSON)' }) as HTMLTextAreaElement).value).toBe(JSON.stringify(next, null, 2))
    expect(screen.getByRole('button', { name: 'Salvar' })).toBeDisabled()
  })
})

describe('W7-R4 the keybindings editor shows what was saved', () => {
  it('keeps the saved text in the textarea, disables Save and shows the saved message', async () => {
    const next = '{ "bindings": [ { "context": "Chat", "bindings": { "ctrl+enter": "chat:newline" } } ] }'
    stubFetch(({ method }) => (method === 'PUT'
      ? { body: { orin: { path: 'C:/u/.orin/keybindings.json', text: next, error: null }, claude: { path: 'C:/u/.claude/keybindings.json', text: null, error: null } } }
      : { body: { orin: { path: 'C:/u/.orin/keybindings.json', text: null, error: null }, claude: { path: 'C:/u/.claude/keybindings.json', text: null, error: null } } }))
    render(<KeybindingsSection />, { wrapper: at('/settings/keybindings') })
    const editor = await screen.findByRole('textbox', { name: 'keybindings.json' }) as HTMLTextAreaElement
    fireEvent.change(editor, { target: { value: next } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar' }))
    expect(await screen.findByText(/Salvo; os atalhos já valem\./)).toBeInTheDocument()
    expect((screen.getByRole('textbox', { name: 'keybindings.json' }) as HTMLTextAreaElement).value).toBe(next)
    expect(screen.getByRole('button', { name: 'Salvar' })).toBeDisabled()
  })
})

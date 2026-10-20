import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { LOCAL_CAPABILITIES } from '../../src/api/session'
import { chatCommandHandlers, folderPage, type ChatCommandDeps } from '../../src/features/commands/useChatCommands'
import { agentFormProblems, AgentsSection, draftOf } from '../../src/features/codeSettings/AgentsSection'
import { HooksSection, parseHooksText } from '../../src/features/codeSettings/HooksSection'
import { KEYBINDINGS_TEMPLATE } from '../../src/features/codeSettings/KeybindingsSection'
import { PermissionsSection } from '../../src/features/codeSettings/PermissionsSection'
import { TrustedFoldersSection } from '../../src/features/codeSettings/TrustedFoldersSection'
import { validateKeybindings } from '../../src/features/keybindings/bindings'
import { visibleSettingsGroups } from '../../src/features/settings/sections'

type Call = { url: string; method: string; body: unknown }
type Answer = { status?: number; body: unknown }

function stubFetch(answer: (call: Call) => Answer | undefined): Call[] {
  const calls: Call[] = []
  globalThis.fetch = vi.fn(async (input: Parameters<typeof fetch>[0], init?: NonNullable<Parameters<typeof fetch>[1]>) => {
    const call = { url: String(input), method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined }
    calls.push(call)
    const result = answer(call) ?? { body: {} }
    return new Response(JSON.stringify(result.body), { status: result.status ?? 200, headers: { 'Content-Type': 'application/json' } })
  }) as unknown as typeof fetch
  return calls
}

const at = (path: string) => ({ children }: { children: ReactNode }) => <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>

const effective = (permissions: Record<string, unknown[]>, writable = { user: 'C:/u/.orin/settings.json', project: 'C:/p/.orin/settings.json', local: 'C:/p/.orin/settings.local.json' }) => ({
  project_root: 'C:/p', trusted: true, values: {}, provenance: {}, layers: [], files: {}, writable, errors: [], untrusted_ignored: [],
  permissions: { allow: [], ask: [], deny: [], additionalDirectories: [], ...permissions },
})

afterEach(() => { vi.restoreAllMocks() })

describe('the settings navigation', () => {
  it('shows the Orin Code editors only on a local Orin', () => {
    const ids = (hostFolders: boolean) => visibleSettingsGroups({ isAdmin: false, capabilities: { ...LOCAL_CAPABILITIES, host_folders: hostFolders } })
      .flatMap((group) => group.items.map((item) => item.id))
    expect(ids(true)).toEqual(expect.arrayContaining(['permissions', 'hooks', 'agents', 'output-styles', 'keybindings', 'trusted-folders']))
    expect(ids(false)).not.toContain('permissions')
    expect(ids(false)).not.toContain('trusted-folders')
  })

  it('opens an editor on the conversation’s folder, encoded', () => {
    expect(folderPage('permissions', 'chat 1/x')).toBe('/settings/permissions?conversation=chat%201%2Fx')
  })
})

describe('the permissions editor', () => {
  it('shows each rule with its layer and file; only a writable layer’s rule can be removed', async () => {
    const calls = stubFetch(({ url, method }) => {
      if (url.includes('/v1/settings/effective')) {
        return { body: effective({
          allow: [{ rule: 'Bash(npm test:*)', layer: 'project', file: 'C:/p/.orin/settings.json' }, { rule: 'Bash(rm:*)', layer: 'user', file: 'C:/u/.orin/settings.json' }],
          deny: [{ rule: 'Bash(rm:*)', layer: 'managed', file: 'C:/ProgramData/Orin/managed-settings.json' }],
        }) }
      }
      if (url.includes('/v1/settings/permissions') && method === 'PUT') return { body: { permissions: {} } }
      return undefined
    })
    render(<PermissionsSection />, { wrapper: at('/settings/permissions?conversation=chat_1') })
    expect(await screen.findByText('Bash(npm test:*)')).toBeInTheDocument()
    expect(screen.getByText('C:/ProgramData/Orin/managed-settings.json')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Remover Bash\(rm:\*\) \(gerenciado\)/ })).toBeNull()
    expect(screen.getByText('sem efeito: também está em Negar')).toBeInTheDocument()
    expect(screen.getByText(/Pasta:/)).toHaveTextContent('C:/p')
    expect(calls[0].url).toContain('conversation_id=chat_1')
    fireEvent.click(screen.getByRole('button', { name: 'Remover Bash(npm test:*) (projeto)' }))
    await waitFor(() => expect(calls.find((call) => call.method === 'PUT')?.body).toEqual({ layer: 'project', kind: 'allow', rule: 'Bash(npm test:*)', op: 'remove', conversation_id: 'chat_1' }))
  })

  it('adds a rule to the chosen layer and lists the server’s problems when it refuses', async () => {
    stubFetch(({ url, method }) => {
      if (url.includes('/v1/settings/effective')) return { body: effective({}) }
      if (method === 'PUT') return { status: 422, body: { error: { code: 'settings_rejected', category: 'VALIDATION', details: { problems: ["'Bash(' is not a valid rule"] } } } }
      return undefined
    })
    render(<PermissionsSection />, { wrapper: at('/settings/permissions?conversation=chat_1') })
    fireEvent.change(await screen.findByRole('textbox', { name: 'Regra' }), { target: { value: 'Bash(' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Onde salvar' }), { target: { value: 'local' } })
    fireEvent.click(screen.getByRole('button', { name: 'Adicionar' }))
    expect(await screen.findByText("'Bash(' is not a valid rule")).toBeInTheDocument()
  })

  it('says the editor is local-only on a shared server', async () => {
    stubFetch(() => ({ status: 404, body: { error: { code: 'not_found', category: 'NOT_FOUND' } } }))
    render(<PermissionsSection />, { wrapper: at('/settings/permissions') })
    expect(await screen.findByRole('alert')).toHaveTextContent('Este editor existe só no Orin local')
    expect(screen.queryByText(/Mostrando as suas configurações de usuário/)).toBeNull() // nothing to show: no contradicting banner
  })
})

describe('the hooks editor', () => {
  it('checks the JSON before it can be saved', () => {
    expect(parseHooksText('{ "PreToolUse": [] }')).toEqual({ hooks: { PreToolUse: [] }, error: null })
    expect(parseHooksText('')).toEqual({ hooks: {}, error: null })
    expect(parseHooksText('{ nope').error).toMatch(/^JSON inválido/)
    expect(parseHooksText('[]').error).toMatch(/objeto/)
    expect(parseHooksText('{ "Stop": { "type": "command" } }').error).toMatch(/Stop/)
  })

  it('lists the hooks in force and saves a layer’s hooks', async () => {
    const calls = stubFetch(({ method }) => (method === 'PUT' ? { body: { disabled: false, effective: [], editable: {}, errors: [] } } : {
      body: {
        disabled: false, errors: [],
        effective: [{ event: 'PostToolUse', matcher: 'Edit|Write', type: 'command', source: 'project', label: 'npm run lint', timeout: 60 }],
        editable: { user: { file: 'C:/u/.orin/settings.json', hooks: {} } },
      },
    }))
    render(<HooksSection />, { wrapper: at('/settings/hooks') })
    expect(await screen.findByText('npm run lint')).toBeInTheDocument()
    const editor = screen.getByRole('textbox', { name: 'Hooks deste arquivo (JSON)' })
    fireEvent.change(editor, { target: { value: '{ "Stop": "x" }' } })
    expect(screen.getByRole('button', { name: 'Salvar' })).toBeDisabled()
    fireEvent.change(editor, { target: { value: '{ "Stop": [{ "hooks": [{ "type": "command", "command": "echo fim" }] }] }' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar' }))
    await waitFor(() => expect(calls.find((call) => call.method === 'PUT')?.body).toMatchObject({ layer: 'user', hooks: { Stop: [{ hooks: [{ type: 'command', command: 'echo fim' }] }] } }))
  })
})

describe('the agents editor', () => {
  const form = { name: 'revisor', scope: 'user' as const, description: 'Revisa testes', prompt: 'Revise.', tools: 'Read, Grep', disallowed: '', model: '', permissionMode: 'plan', maxTurns: '12', effort: 'high' }

  it('checks the form the way the server does, and sends tool names only', () => {
    expect(agentFormProblems(form)).toEqual([])
    expect(agentFormProblems({ ...form, name: '../x' })[0]).toMatch(/^Nome/)
    expect(agentFormProblems({ ...form, tools: 'Bash(rm:*)' })[0]).toMatch(/não é um nome de ferramenta/)
    expect(agentFormProblems({ ...form, maxTurns: '900' })[0]).toMatch(/de 1 a 500/)
    expect(draftOf(form)).toMatchObject({ tools: ['Read', 'Grep'], disallowedTools: [], permissionMode: 'plan', maxTurns: 12, effort: 'high', model: null })
    expect(draftOf({ ...form, tools: '' }).tools).toBeNull() // empty means every tool
  })

  it('lists agents and asks before deleting a file', async () => {
    const calls = stubFetch(({ method }) => (method === 'DELETE' ? { body: { agents: [], errors: [], shadowed: [] } } : {
      body: { agents: [{ name: 'revisor', description: 'Revisa testes', origin: 'user', path: 'C:/u/.orin/agents/revisor.md', editable: true, prompt: 'x', tools: null, disallowed_tools: [] }], errors: [], shadowed: [] },
    }))
    render(<AgentsSection />, { wrapper: at('/settings/agents') })
    fireEvent.click(await screen.findByRole('button', { name: 'Apagar' }))
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Apagar o arquivo' }))
    await waitFor(() => expect(calls.find((call) => call.method === 'DELETE')?.url).toContain('/v1/settings/agents/revisor?scope=user'))
  })
})

describe('keybindings and trusted folders', () => {
  it('starts a new keybindings file from a template that is valid', () => {
    expect(validateKeybindings(KEYBINDINGS_TEMPLATE, 'browser').errors).toEqual([])
  })

  it('revokes a trusted folder only after a confirmation', async () => {
    let revoked = false
    const calls = stubFetch(({ method }) => {
      if (method === 'POST') revoked = true
      return { body: { folders: revoked ? [] : ['C:/Projetos/pagamentos'] } }
    })
    render(<TrustedFoldersSection />, { wrapper: at('/settings/trusted-folders') })
    fireEvent.click(await screen.findByRole('button', { name: 'Revogar a confiança em C:/Projetos/pagamentos' }))
    expect(calls.some((call) => call.method === 'POST')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Revogar' }))
    await waitFor(() => expect(calls.find((call) => call.method === 'POST')?.body).toEqual({ path: 'C:/Projetos/pagamentos' }))
    expect(await screen.findByText('Nenhuma pasta confiável.')).toBeInTheDocument()
  })
})

describe('commands that open the editors', () => {
  function deps(overrides: Partial<ChatCommandDeps> = {}): ChatCommandDeps {
    const fetchImpl = (async () => new Response(JSON.stringify({ styles: [{ key: 'explanatory', name: 'Explicativo', description: '', source: 'builtin' }], active: 'explanatory', active_layer: 'local' }), { status: 200, headers: { 'Content-Type': 'application/json' } })) as unknown as typeof fetch
    return {
      client: new ApiClient({ fetchImpl, maxAttempts: 1 }), conversationId: 'chat_1', messages: [], title: 'T', modelId: 'm', provider: 'p',
      permissionMode: 'ask_permissions', mode: 'code', workspacePath: null, navigate: vi.fn(), send: vi.fn(async () => undefined),
      reload: vi.fn(async () => undefined), notify: vi.fn(), openInfo: vi.fn(), openPreferences: vi.fn(), openModelPicker: vi.fn(), setPermissionMode: vi.fn(),
      ...overrides,
    }
  }

  it('/permissions, /hooks and /agents open on this folder; /output-style <style> uses it here', async () => {
    const value = deps()
    const handlers = chatCommandHandlers(value)
    handlers.permissions('')
    handlers.hooks('')
    handlers.agents('')
    expect(value.navigate).toHaveBeenNthCalledWith(1, '/settings/permissions?conversation=chat_1')
    expect(value.navigate).toHaveBeenNthCalledWith(2, '/settings/hooks?conversation=chat_1')
    expect(value.navigate).toHaveBeenNthCalledWith(3, '/settings/agents?conversation=chat_1')
    await handlers['output-style']('explanatory')
    expect(value.notify).toHaveBeenCalledWith('Estilo de resposta: Explicativo (nesta pasta).')
  })
})

import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PermissionsSection } from '../../src/features/codeSettings/PermissionsSection'

function stubFetch(rules: Record<string, unknown[]>): void {
  const body = {
    project_root: 'C:/p', trusted: true, values: {}, provenance: {}, layers: [], files: {},
    writable: { user: 'C:/u/.orin/settings.json', project: 'C:/p/.orin/settings.json', local: 'C:/p/.orin/settings.local.json' },
    errors: [], untrusted_ignored: [], permissions: { allow: [], ask: [], deny: [], additionalDirectories: [], ...rules },
  }
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })) as unknown as typeof fetch
}

const at = (path: string) => ({ children }: { children: ReactNode }) => <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>

afterEach(() => { vi.restoreAllMocks() })

describe('W7-R5 removing a rule', () => {
  it('offers Remover only for a rule in the file the editor writes; elsewhere it says where the rule lives', async () => {
    stubFetch({
      allow: [
        { rule: 'Bash(curl:*)', layer: 'user', file: 'C:/u/.claude/settings.json' },
        { rule: 'Bash(git status)', layer: 'user', file: 'C:/u/.orin/settings.json' },
      ],
    })
    render(<PermissionsSection />, { wrapper: at('/settings/permissions') })
    expect(await screen.findByText('Bash(git status)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remover Bash(git status) (usuário)' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remover Bash(curl:*) (usuário)' })).toBeNull()
    // The rule's own file is shown once, with how to remove it there (no second copy of the path).
    expect(screen.getByText('C:/u/.claude/settings.json')).toBeInTheDocument()
    expect(screen.getByText('para removê-la, edite o arquivo acima')).toBeInTheDocument()
  })
})

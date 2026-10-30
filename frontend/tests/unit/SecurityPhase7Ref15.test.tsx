import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PermissionsSection } from '../../src/features/codeSettings/PermissionsSection'

type Call = { url: string; method: string; body: unknown }
type Answer = { status?: number; body: unknown }

function stubFetch(answer?: (call: Call) => Answer | undefined): Call[] {
  const calls: Call[] = []
  globalThis.fetch = vi.fn(async (input: Parameters<typeof fetch>[0], init?: NonNullable<Parameters<typeof fetch>[1]>) => {
    const call = {
      url: String(input),
      method: init?.method ?? 'GET',
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    }
    calls.push(call)
    const result = answer?.(call) ?? {
      body: {
        project_root: 'C:/Projetos/meu-app',
        trusted: true,
        values: {},
        provenance: {},
        layers: [],
        files: {},
        writable: {
          user: 'C:/u/.orin/settings.json',
          project: 'C:/Projetos/meu-app/.orin/settings.json',
          local: 'C:/Projetos/meu-app/.orin/settings.local.json',
        },
        errors: [],
        untrusted_ignored: [],
        permissions: { allow: [], ask: [], deny: [], additionalDirectories: [] },
      },
    }
    return new Response(JSON.stringify(result.body), {
      status: result.status ?? 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as unknown as typeof fetch
  return calls
}

const at = (path: string) => ({ children }: { children: ReactNode }) => (
  <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>
)

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('Phase 7 — ref-15.png: Segurança e controle (PermissionsSection)', () => {
  it('renders the editorial header, preserves SettingsSection h1, and keeps #security-advanced-rules mounted', async () => {
    const calls = stubFetch()
    const { container } = render(<PermissionsSection />, { wrapper: at('/settings/permissions?conversation=chat_1') })

    expect(screen.getByRole('heading', { level: 1, name: 'Permissões' })).toBeInTheDocument()
    expect(screen.getByTestId('security-ref15-heading')).toHaveTextContent('Segurança e controle')
    expect(screen.getByText('Você escolhe como o Orin pode agir.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Configurações/i })).toHaveAttribute('href', '/settings/general')

    // Rule A: first fetch call must be /v1/settings/effective with conversation_id
    await waitFor(() => expect(calls.length).toBeGreaterThan(0))
    expect(calls[0].url).toContain('/v1/settings/effective?conversation_id=chat_1')

    // Rule E: advanced rules editor remains mounted and visible
    const advanced = container.querySelector('#security-advanced-rules')
    expect(advanced).not.toBeNull()
    expect(await screen.findByRole('button', { name: 'Adicionar' })).toBeInTheDocument()
  })

  it('switches default permission modes, saves to localStorage + code-mode API, and supports Cancelar', async () => {
    const calls = stubFetch(({ url, method }) => {
      if (url.includes('/v1/code-mode/settings') && method === 'PUT') {
        return {
          body: {
            autonomy: 'plan_mode',
            system_notifications: false,
            monitoring_enabled: true,
          },
        }
      }
      return undefined
    })

    render(<PermissionsSection />, { wrapper: at('/settings/permissions') })

    const askRadio = screen.getByTestId('security-perm-ask')
    const planRadio = screen.getByTestId('security-perm-plan')
    const acceptRadio = screen.getByTestId('security-perm-accept')

    expect(askRadio).toHaveAttribute('aria-checked', 'true')
    expect(planRadio).toHaveAttribute('aria-checked', 'false')
    expect(acceptRadio).toHaveAttribute('aria-checked', 'false')

    // Select "Aceitar edições" then click "Cancelar" -> reverts to "Perguntar antes"
    fireEvent.click(acceptRadio)
    expect(acceptRadio).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByTestId('security-cancel-btn'))
    expect(askRadio).toHaveAttribute('aria-checked', 'true')

    // Select "Só planejar" and save
    fireEvent.click(planRadio)
    expect(planRadio).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(screen.getByTestId('security-save-btn'))

    expect(await screen.findByTestId('security-saved-notice')).toHaveTextContent('Configurações de segurança salvas.')
    expect(window.localStorage.getItem('orin.security.permissionMode')).toBe('plan_mode')

    await waitFor(() => {
      const putCall = calls.find((c) => c.url.includes('/v1/code-mode/settings') && c.method === 'PUT')
      expect(putCall?.body).toEqual({
        autonomy: 'plan_mode',
        system_notifications: false,
        monitoring_enabled: true,
      })
    })
  })

  it('shows honest empty folders state by default, manages authorized folders (add, scope select, duplicate/invalid rejection, remove)', async () => {
    const calls = stubFetch(({ url, method }) => {
      if (url.includes('/v1/settings/trusted-folders/revoke') && method === 'POST') {
        return { body: { folders: [] } }
      }
      return undefined
    })

    render(<PermissionsSection />, { wrapper: at('/settings/permissions') })

    const card = screen.getByTestId('security-authorized-folders-card')
    expect(within(card).getByTestId('security-empty-folders')).toHaveTextContent(
      'Nenhuma pasta autorizada ainda. Adicione uma pasta para permitir edições locais.',
    )
    expect(within(card).queryByText(/auryly/i)).toBeNull()

    // Open inline add folder form
    fireEvent.click(screen.getByTestId('security-add-folder-btn'))
    const input = screen.getByTestId('security-new-folder-input')
    const confirmBtn = screen.getByTestId('security-confirm-folder-btn')

    // Red-team: reject empty/whitespace path and >260 chars path
    fireEvent.change(input, { target: { value: '   ' } })
    fireEvent.click(confirmBtn)
    expect(screen.getByTestId('security-folder-error')).toHaveTextContent(/Informe um caminho de pasta válido/i)

    fireEvent.change(input, { target: { value: `C:/${'a'.repeat(265)}` } })
    fireEvent.click(confirmBtn)
    expect(screen.getByTestId('security-folder-error')).toHaveTextContent(/Informe um caminho de pasta válido/i)

    // Red-team: reject invalid characters (< > " | ? *) without role="alert"
    fireEvent.change(input, { target: { value: 'C:/Projetos/invalido<script>' } })
    fireEvent.click(confirmBtn)
    const errorMsg = screen.getByTestId('security-folder-error')
    expect(errorMsg).toHaveTextContent(/Informe um caminho de pasta válido/i)
    expect(errorMsg).not.toHaveAttribute('role', 'alert')

    // Valid path with backslashes and control chars is sanitized and added
    fireEvent.change(input, { target: { value: '  D:\\Workspace\\orin-app\u0007  ' } })
    fireEvent.click(confirmBtn)
    expect(screen.queryByTestId('security-folder-error')).toBeNull()
    expect(within(card).getByText('D:/Workspace/orin-app')).toBeInTheDocument()
    expect(within(card).queryByTestId('security-empty-folders')).toBeNull()

    // Change scope of added folder
    const scopeSelect = screen.getByRole('combobox', { name: 'Escopo da pasta D:/Workspace/orin-app' })
    expect(scopeSelect).toHaveValue('folder_only')
    fireEvent.change(scopeSelect, { target: { value: 'include_subfolders' } })
    expect(scopeSelect).toHaveValue('include_subfolders')

    // Red-team: reject duplicate path (case-insensitive and backslash-normalized)
    fireEvent.click(screen.getByTestId('security-add-folder-btn'))
    fireEvent.change(screen.getByTestId('security-new-folder-input'), { target: { value: 'd:\\workspace\\orin-app' } })
    fireEvent.click(screen.getByTestId('security-confirm-folder-btn'))
    expect(screen.getByTestId('security-folder-error')).toBeInTheDocument()

    // Remove the folder
    fireEvent.click(screen.getByRole('button', { name: 'Remover pasta autorizada D:/Workspace/orin-app' }))
    expect(within(card).queryByText('D:/Workspace/orin-app')).toBeNull()
    expect(within(card).getByTestId('security-empty-folders')).toBeInTheDocument()
    await waitFor(() => {
      const revokeCall = calls.find((c) => c.url.includes('/v1/settings/trusted-folders/revoke') && c.method === 'POST')
      expect(revokeCall?.body).toEqual({ path: 'D:/Workspace/orin-app' })
    })
  })

  it('renders external services card and neutral interactive approval simulation card without button name collisions', async () => {
    stubFetch()
    render(<PermissionsSection />, { wrapper: at('/settings/permissions') })

    // Card 3: Ações em serviços externos
    const externalCard = screen.getByTestId('security-external-actions-card')
    expect(within(externalCard).getByText('Ações em serviços externos')).toBeInTheDocument()
    expect(
      within(externalCard).getByText('Enviar mensagens, criar eventos ou alterar dados fora do computador sempre pede confirmação.'),
    ).toBeInTheDocument()

    // Right column: Simulação do pedido de permissão card
    const previewCard = screen.getByTestId('security-approval-preview-card')
    expect(within(previewCard).getByText('Simulação do pedido de permissão')).toBeInTheDocument()
    expect(
      within(previewCard).getByText('Como o Orin pede confirmação antes de alterar arquivos no projeto atual.'),
    ).toBeInTheDocument()
    expect(within(previewCard).getByText('src/index.ts')).toBeInTheDocument()
    expect(within(previewCard).getByText('package.json')).toBeInTheDocument()
    expect(within(previewCard).queryByText(/auryly/i)).toBeNull()

    // "+ Adicionar" badge must be a span, not a button
    const addBadge = within(previewCard).getByText('+ Adicionar')
    expect(addBadge.tagName.toLowerCase()).toBe('span')

    // Toggle diff preview
    expect(screen.queryByTestId('security-preview-diff-box')).toBeNull()
    fireEvent.click(screen.getByTestId('security-preview-diff-toggle'))
    expect(screen.getByTestId('security-preview-diff-box')).toHaveTextContent('+ export function startApp()')
    expect(screen.getByTestId('security-preview-diff-box')).not.toHaveTextContent('auryly')

    // Approve / Reject preview actions
    fireEvent.click(screen.getByTestId('security-preview-approve-btn'))
    expect(within(previewCard).getByText('Exemplo: alteração aprovada para esta execução.')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('security-preview-reject-btn'))
    expect(within(previewCard).getByText('Exemplo: alteração recusada.')).toBeInTheDocument()
  })

  it('recovers from corrupted localStorage and merges trusted folders from server API', async () => {
    window.localStorage.setItem('orin.security.authorizedFolders', '{not-valid-json')
    window.localStorage.setItem('orin.security.permissionMode', 'invalid_mode')

    stubFetch(({ url }) => {
      if (url.includes('/v1/code-mode/settings')) {
        return {
          body: {
            autonomy: 'code_autonomy',
            system_notifications: false,
            monitoring_enabled: true,
          },
        }
      }
      if (url.includes('/v1/settings/trusted-folders')) {
        return {
          body: {
            folders: ['C:\\Projetos\\servidor-extra', 'C:/Projetos/meu-app'],
          },
        }
      }
      return undefined
    })

    render(<PermissionsSection />, { wrapper: at('/settings/permissions') })

    const card = screen.getByTestId('security-authorized-folders-card')
    expect(await within(card).findByText('C:/Projetos/servidor-extra')).toBeInTheDocument()
    expect(within(card).getByText('C:/Projetos/meu-app')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByTestId('security-perm-accept')).toHaveAttribute('aria-checked', 'true')
    })
  })
})

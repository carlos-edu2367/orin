import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { LOCAL_CAPABILITIES } from '../../src/api/session'
import { parseWorkspaceState } from '../../src/api/workspace'
import { SessionContext } from '../../src/app/useSession'
import { WorkspaceFolderButton } from '../../src/features/conversations/WorkspaceFolderButton'
import { ProfileFolderBrowser } from '../../src/features/files/ProfileFolderBrowser'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const root = { path: '.', entries: [{ name: 'proj', kind: 'directory', bytes: 0, modified_at: 'x' }, { name: 'a.txt', kind: 'file', bytes: 3, modified_at: 'x' }], truncated: false }
const proj = { path: 'proj', entries: [], truncated: false }

describe('ProfileFolderBrowser', () => {
  it('navigates into a folder and chooses it', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValueOnce(json(root)).mockResolvedValueOnce(json(proj))
    const onChoose = vi.fn()
    render(<ProfileFolderBrowser client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onChoose={onChoose} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Abrir pasta proj' }))
    await screen.findByText('proj', { selector: '.profile-files__path' })
    await userEvent.click(screen.getByRole('button', { name: 'Usar esta pasta' }))
    expect(onChoose).toHaveBeenCalledWith('proj')
    expect(String(fetchImpl.mock.calls[1][0])).toBe('/v1/files?path=proj')
  })

  it('creates a folder and imports a zip into the current folder', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(json(root))
      .mockResolvedValueOnce(json({ path: 'novo' }, 201))
      .mockResolvedValueOnce(json(root))
      .mockResolvedValueOnce(json({ path: 'app' }, 201))
      .mockResolvedValueOnce(json(root))
    render(<ProfileFolderBrowser client={new ApiClient({ fetchImpl, maxAttempts: 1 })} onChoose={() => undefined} />)
    await userEvent.type(await screen.findByLabelText('Nome da nova pasta'), 'novo')
    await userEvent.click(screen.getByRole('button', { name: 'Criar pasta' }))
    const zip = new File(['PK'], 'app.zip', { type: 'application/zip' })
    await userEvent.upload(screen.getByLabelText('Importar .zip'), zip)
    expect(fetchImpl.mock.calls[3][0]).toBe('/v1/files/import')
    const body = fetchImpl.mock.calls[3][1]?.body as FormData
    expect((body.get('file') as File).name).toBe('app.zip')
    expect(body.get('folder_name')).toBe('app')
  })

  it('parses an unavailable workspace', () => {
    expect(parseWorkspaceState({ kind: 'unavailable', path: null, folder_name: 'host', scope: 'chat' }).kind).toBe('unavailable')
  })

  it('replaces the native picker with the profile browser on a server', async () => {
    const fetchImpl = vi.fn<typeof fetch>(() => Promise.resolve(json(root)))
    const session = { mode: 'session' as const, user: null, isAdmin: false, refresh: async () => undefined, signOut: async () => undefined, capabilities: { ...LOCAL_CAPABILITIES, host_folders: false, profile_files: true } }
    render(
      <MemoryRouter><SessionContext.Provider value={session}>
        <WorkspaceFolderButton
          client={new ApiClient({ fetchImpl, maxAttempts: 1 })}
          state={{ kind: 'managed', path: null, folderName: null, scope: 'chat', projectName: null }}
          onInspect={vi.fn()} onAttach={vi.fn()} onDetach={vi.fn()} onChange={vi.fn()}
        />
      </SessionContext.Provider></MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Adicionar pasta ao workspace' }))
    expect(screen.queryByRole('button', { name: 'Selecionar diretório…' })).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Abrir pasta proj' })).toBeInTheDocument()
  })
})

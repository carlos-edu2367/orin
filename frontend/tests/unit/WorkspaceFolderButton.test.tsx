import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkspaceFolderButton } from '../../src/features/conversations/WorkspaceFolderButton'
import type { InspectionOutcome, WorkspaceState } from '../../src/api/workspace'

const managed: WorkspaceState = { kind: 'managed', path: null, folderName: null, scope: 'chat', projectName: null }
const plainFolder: InspectionOutcome = { kind: 'folder', path: 'D:/site', exists: true, isDirectory: true, writable: true, entryCount: 4, entriesTruncated: false, risk: 'none' }
const driveRoot: InspectionOutcome = { kind: 'folder', path: 'C:/', exists: true, isDirectory: true, writable: true, entryCount: 12, entriesTruncated: false, risk: 'drive_root' }

function api(overrides: Partial<Parameters<typeof WorkspaceFolderButton>[0]> = {}) {
  return {
    state: managed,
    onInspect: vi.fn(async () => plainFolder),
    onAttach: vi.fn(async (path: string) => ({ ...managed, kind: 'local' as const, path, folderName: 'site' })),
    onDetach: vi.fn(async () => managed),
    onChange: vi.fn(),
    ...overrides,
  }
}

describe('WorkspaceFolderButton', () => {
  it('labels the managed state and the attached folder', () => {
    const { rerender } = render(<WorkspaceFolderButton {...api()} />)
    expect(screen.getByRole('button', { name: /pasta/i })).toBeInTheDocument()

    rerender(<WorkspaceFolderButton {...api({ state: { kind: 'local', path: 'D:/site', folderName: 'site', scope: 'chat', projectName: null } })} />)
    expect(screen.getByRole('button', { name: /site/ })).toHaveAttribute('title', 'D:/site')
  })

  it('falls back to the path when a drive root has no folder name', () => {
    render(<WorkspaceFolderButton {...api({ state: { kind: 'local', path: 'C:\\', folderName: '', scope: 'chat', projectName: null } })} />)
    const button = screen.getByRole('button', { name: 'C:\\' })
    expect(button).toHaveAttribute('title', 'C:\\')
    // The accessible-name computation falls back to the `title` attribute when
    // visible text is blank, which would let an empty label pass unnoticed.
    // Assert the rendered text itself so a regression here is caught for real.
    expect(button).toHaveTextContent('C:\\')
  })

  it('attaches a plain folder after one confirmation', async () => {
    const props = api()
    render(<WorkspaceFolderButton {...props} />)

    fireEvent.click(screen.getByRole('button', { name: /pasta/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Escolher pasta…' }))

    await waitFor(() => expect(screen.getByText('D:/site')).toBeInTheDocument())
    expect(screen.getByText(/4 itens/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Usar esta pasta' }))

    await waitFor(() => expect(props.onAttach).toHaveBeenCalledWith('D:/site', false))
  })

  it('names the risk and requires a deliberate click for a broad folder', async () => {
    const props = api({ onInspect: vi.fn(async () => driveRoot) })
    render(<WorkspaceFolderButton {...props} />)

    fireEvent.click(screen.getByRole('button', { name: /pasta/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Escolher pasta…' }))

    await waitFor(() => expect(screen.getByText(/criar, editar e apagar arquivos em C:\//)).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Usar esta pasta' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Trabalhar em C:/ mesmo assim' }))

    await waitFor(() => expect(props.onAttach).toHaveBeenCalledWith('C:/', true))
  })

  it('falls back to the path field when the dialog is unavailable', async () => {
    const props = api({ onInspect: vi.fn(async (path: string | null) => (path === null ? { kind: 'unavailable' as const } : plainFolder)) })
    render(<WorkspaceFolderButton {...props} />)

    fireEvent.click(screen.getByRole('button', { name: /pasta/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Escolher pasta…' }))

    await waitFor(() => expect(screen.getByText(/não foi possível abrir o seletor/i)).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Caminho da pasta'), { target: { value: 'D:/site' } })
    fireEvent.click(screen.getByRole('button', { name: 'Usar' }))

    await waitFor(() => expect(screen.getByText('D:/site')).toBeInTheDocument())
  })

  it('says a project folder covers every chat of the project', async () => {
    const props = api({ state: { kind: 'managed', path: null, folderName: null, scope: 'project', projectName: 'Site novo' } })
    render(<WorkspaceFolderButton {...props} />)

    fireEvent.click(screen.getByRole('button', { name: /pasta/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Escolher pasta…' }))

    await waitFor(() => expect(screen.getByText(/todos os chats do projeto Site novo/)).toBeInTheDocument())
  })

  it('detaches and reports the new state', async () => {
    const props = api({ state: { kind: 'local', path: 'D:/site', folderName: 'site', scope: 'chat', projectName: null } })
    render(<WorkspaceFolderButton {...props} />)

    fireEvent.click(screen.getByRole('button', { name: /site/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Remover' }))

    await waitFor(() => expect(props.onDetach).toHaveBeenCalledTimes(1))
    expect(props.onChange).toHaveBeenCalledWith(managed)
  })
})

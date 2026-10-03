import { useCallback, useEffect, useState, type ChangeEvent, type FormEvent } from 'react'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import { createFolder, importArchive, listFiles, type FileListing } from '../../api/files'

function parentOf(path: string): string {
  if (path === '.' || !path.includes('/')) return ''
  return path.slice(0, path.lastIndexOf('/'))
}

function childOf(path: string, name: string): string {
  return path === '.' || path === '' ? name : `${path}/${name}`
}

function relative(listing: FileListing): string {
  return listing.path === '.' ? '' : listing.path
}

export function ProfileFolderBrowser({ client, onChoose }: { client: ApiClient; onChoose: (path: string) => void }) {
  const [listing, setListing] = useState<FileListing | null>(null)
  const [current, setCurrent] = useState('')
  const [newFolder, setNewFolder] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (path: string) => {
    setError(null)
    try {
      const result = await listFiles(client, path)
      setListing(result); setCurrent(relative(result))
    } catch { setError('Não foi possível abrir esta pasta.') }
  }, [client])

  useEffect(() => {
    let active = true
    listFiles(client, '')
      .then((result) => { if (active) { setListing(result); setCurrent(relative(result)) } })
      .catch(() => { if (active) setError('Não foi possível abrir esta pasta.') })
    return () => { active = false }
  }, [client])

  async function submitFolder(event: FormEvent) {
    event.preventDefault()
    if (!newFolder.trim()) return
    setBusy(true)
    try { await createFolder(client, current, newFolder.trim()); setNewFolder(''); await load(current) }
    catch { setError('Não foi possível criar a pasta.') }
    finally { setBusy(false) }
  }

  async function importZip(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setBusy(true); setError(null)
    try { await importArchive(client, file, file.name.replace(/\.zip$/i, ''), current); await load(current) }
    catch (failure) { setError(failure instanceof ApiError && failure.code === 'archive_rejected' ? 'O arquivo .zip foi recusado (caminho inválido, muito grande ou a pasta já existe).' : 'Não foi possível importar o arquivo.') }
    finally { setBusy(false) }
  }

  return (
    <div className="profile-files">
      <div className="profile-files__bar">
        <button type="button" disabled={!current || busy} onClick={() => void load(parentOf(current))} aria-label="Voltar para a pasta acima">↑</button>
        <span className="profile-files__path">{current || 'Meus arquivos'}</span>
      </div>
      {error && <p className="profile-files__error" role="alert">{error}</p>}
      <ul className="profile-files__list">
        {listing?.entries.filter((entry) => entry.kind === 'directory').map((entry) => (
          <li key={entry.name}><button type="button" onClick={() => void load(childOf(current, entry.name))} aria-label={`Abrir pasta ${entry.name}`}>▸ {entry.name}</button></li>
        ))}
        {listing && listing.entries.every((entry) => entry.kind !== 'directory') && <li className="profile-files__empty">Nenhuma subpasta.</li>}
      </ul>
      <button type="button" className="button button--primary" disabled={busy} onClick={() => onChoose(current || '.')}>Usar esta pasta</button>
      <form className="profile-files__create" onSubmit={(event) => void submitFolder(event)}>
        <label>Nome da nova pasta<input value={newFolder} onChange={(event) => setNewFolder(event.target.value)} maxLength={255} /></label>
        <button type="submit" disabled={busy}>Criar pasta</button>
      </form>
      <label className="profile-files__import">Importar .zip<input type="file" accept=".zip,application/zip" disabled={busy} onChange={(event) => void importZip(event)} /></label>
    </div>
  )
}

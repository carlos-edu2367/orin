import { useCallback, useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient } from '../../api/client'
import { deleteManagedMemory, listManagedMemories, updateManagedMemory, type ManagedMemory } from '../../api/memory'
import { SettingsPage } from '../settings/SettingsPage'
import { SettingsSection } from '../settings/SettingsSection'

export function MemoryPage({ projectId, embedded = false }: { projectId?: string; embedded?: boolean }) {
  const client = useMemo(() => createBrowserApiClient(), [])
  const [scope, setScope] = useState<'user' | 'project'>(projectId ? 'project' : 'user')
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<ManagedMemory[]>([])
  const [error, setError] = useState(false)
  const load = useCallback(() => listManagedMemories(client, { scope, projectId, query }).then((result) => { setItems(result.items); setError(false) }).catch(() => setError(true)), [client, scope, projectId, query])
  useEffect(() => { const timer = window.setTimeout(load, 150); return () => window.clearTimeout(timer) }, [load])
  const controls = <>{!projectId && <div className="settings-tabs" aria-label="Escopo da memória"><button type="button" aria-pressed={scope === 'user'} onClick={() => setScope('user')}>Global</button><button type="button" aria-pressed={scope === 'project'} onClick={() => setScope('project')} disabled>Project</button></div>}<label className="settings-search" htmlFor="memory-search">Buscar memórias<input id="memory-search" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Conteúdo ou tag" /></label>{error ? <p role="alert">Não foi possível carregar as memórias.</p> : <MemoryList items={items} scope={scope} projectId={projectId} onChanged={load} />}</>
  const body = <><p className="eyebrow">MEMORY / {scope.toUpperCase()}</p><h1>{projectId ? 'Project Memory' : 'Memory'}</h1><p className="settings-content__lede">{projectId ? 'Somente as memórias deste projeto.' : 'Memórias usadas em todos os seus chats.'}</p>{controls}</>
  if (projectId) return <main className="project-page"><a href={`/projects/${projectId}`}>Voltar ao projeto</a><section className="memory-page">{body}</section></main>
  if (embedded) return <SettingsSection eyebrow="MEMORY / GLOBAL">{controls}</SettingsSection>
  return <SettingsPage>{body}</SettingsPage>
}

function MemoryList({ items, scope, projectId, onChanged }: { items: ManagedMemory[]; scope: 'user' | 'project'; projectId?: string; onChanged: () => void }) {
  const client = useMemo(() => createBrowserApiClient(), [])
  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  if (items.length === 0) return <p>Nenhuma memória neste escopo.</p>
  return (
    <div className="memory-list">
      {items.map((item) => (
        <article key={item.memory_id}>
          {editing === item.memory_id ? (
            <>
              <input aria-label="Editar memória" value={draft} onChange={(event) => setDraft(event.target.value)} />
              <button type="button" onClick={() => { void updateManagedMemory(client, item.memory_id, draft, scope, projectId).then(() => { setEditing(null); onChanged() }) }} disabled={!draft.trim()}>Salvar</button>
              <button type="button" onClick={() => setEditing(null)}>Cancelar</button>
            </>
          ) : (
            <>
              <p>{item.fact}</p>
              <small>{item.tags.join(' · ') || 'Sem tags'}{item.updated_at ? ` · atualizado ${new Date(item.updated_at).toLocaleDateString()}` : ''}</small>
              <button type="button" onClick={() => { setEditing(item.memory_id); setDraft(item.fact) }}>Editar</button>
              <button type="button" onClick={() => { void deleteManagedMemory(client, item.memory_id, scope, projectId).then(onChanged) }}>Excluir</button>
            </>
          )}
        </article>
      ))}
    </div>
  )
}

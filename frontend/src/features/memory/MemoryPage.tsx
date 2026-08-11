import { useCallback, useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient } from '../../api/client'
import { deleteManagedMemory, listManagedMemories, type ManagedMemory } from '../../api/memory'
import { SettingsPage } from '../settings/SettingsPage'

export function MemoryPage({ projectId }: { projectId?: string }) {
  const client = useMemo(() => createBrowserApiClient(), [])
  const [scope, setScope] = useState<'user' | 'project'>(projectId ? 'project' : 'user')
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<ManagedMemory[]>([])
  const [error, setError] = useState(false)
  const load = useCallback(() => listManagedMemories(client, { scope, projectId, query }).then((result) => { setItems(result.items); setError(false) }).catch(() => setError(true)), [client, scope, projectId, query])
  useEffect(() => { const timer = window.setTimeout(load, 150); return () => window.clearTimeout(timer) }, [load])
  const body = <><p className="eyebrow">MEMORY / {scope.toUpperCase()}</p><h1>{projectId ? 'Project Memory' : 'Memory'}</h1><p className="settings-content__lede">{projectId ? 'Somente as memórias deste projeto.' : 'Memórias usadas em todos os seus chats.'}</p>{!projectId && <div className="settings-tabs" role="tablist"><button role="tab" aria-selected={scope === 'user'} onClick={() => setScope('user')}>Global</button><button role="tab" aria-selected={scope === 'project'} onClick={() => setScope('project')} disabled>Project</button></div>}<label className="settings-search">Buscar memórias<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Conteúdo ou tag" /></label>{error ? <p role="alert">Não foi possível carregar as memórias.</p> : <MemoryList items={items} onDelete={(item) => void deleteManagedMemory(client, item.memory_id, scope, projectId).then(load)} />}</>
  return projectId ? <main className="project-page"><a href={`/projects/${projectId}`}>Voltar ao projeto</a><section className="memory-page">{body}</section></main> : <SettingsPage>{body}</SettingsPage>
}

function MemoryList({ items, onDelete }: { items: ManagedMemory[]; onDelete: (item: ManagedMemory) => void }) { return <div className="memory-list">{items.length === 0 ? <p>Nenhuma memória neste escopo.</p> : items.map((item) => <article key={item.memory_id}><p>{item.fact}</p><small>{item.tags.join(' · ') || 'Sem tags'}{item.updated_at ? ` · atualizado ${new Date(item.updated_at).toLocaleDateString()}` : ''}</small><button type="button" onClick={() => onDelete(item)}>Excluir</button></article>)}</div> }

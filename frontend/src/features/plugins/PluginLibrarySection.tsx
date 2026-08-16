import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { fetchPluginLibrary, type PluginLibraryEntry } from '../../api/plugins'
import type { ApiClient } from '../../api/client'
import { PluginInstallDialog } from './PluginInstallDialog'
import { McpFromRepoDialog } from './McpFromRepoDialog'

const ORIGIN_LABEL: Record<PluginLibraryEntry['origin'], string> = { registry: 'Registro', web: 'Web' }

function githubUrl(sourceUrl: string): string { return sourceUrl.replace(/\.git$/, '') }

export function PluginLibrarySection({ client, onInstalled }: { client: ApiClient; onInstalled: () => void }) {
  const [entries, setEntries] = useState<PluginLibraryEntry[]>([])
  const [webAvailable, setWebAvailable] = useState(true)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [installing, setInstalling] = useState<string | null>(null)
  const [addingMcpFor, setAddingMcpFor] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [activeQuery, setActiveQuery] = useState('')
  const load = useCallback((refresh: boolean, q: string) => {
    (refresh ? setRefreshing : setLoading)(true)
    return fetchPluginLibrary(client, refresh, q)
      .then((result) => { setEntries(result.entries); setWebAvailable(result.web_search_available); setError(null) })
      .catch(() => setError('Não foi possível carregar a biblioteca de plugins.'))
      .finally(() => { setLoading(false); setRefreshing(false) })
  }, [client])
  useEffect(() => { void load(false, '') }, [load])
  function search(event: FormEvent) {
    event.preventDefault()
    const trimmed = query.trim()
    setActiveQuery(trimmed)
    void load(true, trimmed)
  }
  function clearSearch() {
    setQuery('')
    setActiveQuery('')
    void load(false, '')
  }
  return <div className="plugin-library">
    <div className="plugin-library__head">
      <p className="plugin-library__lede">Plugins compatíveis com MCP encontrados em marketplaces conhecidos e na web.</p>
      <button type="button" className="button button--secondary" onClick={() => void load(true, activeQuery)} disabled={refreshing}>{refreshing ? 'Atualizando…' : 'Atualizar'}</button>
    </div>
    <form className="plugin-library__search" onSubmit={search}>
      <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar plugins por nome ou palavra-chave…" aria-label="Buscar na biblioteca de plugins" />
      <button type="submit" className="button button--secondary">Buscar</button>
      {activeQuery && <button type="button" className="button--quiet" onClick={clearSearch}>Limpar</button>}
    </form>
    {activeQuery && <p className="plugin-library__active-query">Resultados para "{activeQuery}"</p>}
    {!webAvailable && <p className="plugin-library__note">Busca na web indisponível no momento — mostrando apenas o registro conhecido.</p>}
    {loading && entries.length === 0 && <div className="plugins-section__loading" aria-label="Carregando biblioteca"><span /><span /><span /></div>}
    {error && <div className="plugins-section__error" role="alert"><span className="plugins-section__status-mark" aria-hidden="true">!</span><div><strong>Não foi possível carregar a biblioteca</strong><p>{error}</p></div><button type="button" className="button button--secondary" onClick={() => void load(false, activeQuery)}>Tentar novamente</button></div>}
    <div className="plugin-library__list" aria-label="Plugins disponíveis para instalação">
      {entries.map((entry) => <article className="plugin-library-card" key={entry.source_url}>
        <div className="plugin-library-card__head">
          <span className="plugin-library-card__icon" aria-hidden="true">✦</span>
          <div className="plugin-library-card__identity"><strong>{entry.name}</strong><span className={`plugin-library-card__badge is-${entry.origin}`}>{ORIGIN_LABEL[entry.origin]}</span></div>
        </div>
        <p className="plugin-library-card__description">{entry.description || 'Sem descrição disponível.'}</p>
        <div className="plugin-library-card__actions">
          <a className="button button--quiet" href={githubUrl(entry.source_url)} target="_blank" rel="noreferrer">Ver no GitHub <span aria-hidden="true">↗</span></a>
          {entry.installable_kind === 'mcp_raw'
            ? <button type="button" className="button button--primary" onClick={() => setAddingMcpFor(entry.source_url)}>Adicionar como servidor MCP</button>
            : <button type="button" className="button button--primary" onClick={() => setInstalling(entry.source_url)}>Instalar</button>}
        </div>
      </article>)}
      {!loading && !error && entries.length === 0 && <p className="plugin-library__empty">Nenhum plugin encontrado no momento.</p>}
    </div>
    {installing && <PluginInstallDialog key={installing} client={client} initialReference={installing} onClose={() => setInstalling(null)} onInstalled={() => { setInstalling(null); onInstalled() }} onNoManifest={() => { setAddingMcpFor(installing); setInstalling(null) }} />}
    {addingMcpFor && <McpFromRepoDialog key={addingMcpFor} client={client} sourceUrl={addingMcpFor} onClose={() => setAddingMcpFor(null)} onAdded={() => { setAddingMcpFor(null); onInstalled() }} />}
  </div>
}

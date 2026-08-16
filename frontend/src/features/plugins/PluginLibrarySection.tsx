import { useCallback, useEffect, useState } from 'react'
import { fetchPluginLibrary, type PluginLibraryEntry } from '../../api/plugins'
import type { ApiClient } from '../../api/client'
import { PluginInstallDialog } from './PluginInstallDialog'

const ORIGIN_LABEL: Record<PluginLibraryEntry['origin'], string> = { registry: 'Registro', web: 'Web' }

export function PluginLibrarySection({ client, onInstalled }: { client: ApiClient; onInstalled: () => void }) {
  const [entries, setEntries] = useState<PluginLibraryEntry[]>([])
  const [webAvailable, setWebAvailable] = useState(true)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [installing, setInstalling] = useState<string | null>(null)
  const load = useCallback((refresh: boolean) => {
    (refresh ? setRefreshing : setLoading)(true)
    return fetchPluginLibrary(client, refresh)
      .then((result) => { setEntries(result.entries); setWebAvailable(result.web_search_available); setError(null) })
      .catch(() => setError('Não foi possível carregar a biblioteca de plugins.'))
      .finally(() => { setLoading(false); setRefreshing(false) })
  }, [client])
  useEffect(() => { void load(false) }, [load])
  return <div className="plugin-library">
    <div className="plugin-library__head">
      <p className="plugin-library__lede">Plugins compatíveis com MCP encontrados em marketplaces conhecidos e na web.</p>
      <button type="button" className="button button--secondary" onClick={() => void load(true)} disabled={refreshing}>{refreshing ? 'Atualizando…' : 'Atualizar'}</button>
    </div>
    {!webAvailable && <p className="plugin-library__note">Busca na web indisponível no momento — mostrando apenas o registro conhecido.</p>}
    {loading && entries.length === 0 && <div className="plugins-section__loading" aria-label="Carregando biblioteca"><span /><span /><span /></div>}
    {error && <div className="plugins-section__error" role="alert"><span className="plugins-section__status-mark" aria-hidden="true">!</span><div><strong>Não foi possível carregar a biblioteca</strong><p>{error}</p></div><button type="button" className="button button--secondary" onClick={() => void load(false)}>Tentar novamente</button></div>}
    <div className="plugin-library__list" aria-label="Plugins disponíveis para instalação">
      {entries.map((entry) => <article className="plugin-library-card" key={entry.source_url}>
        <div className="plugin-library-card__head">
          <span className="plugin-library-card__icon" aria-hidden="true">✦</span>
          <div className="plugin-library-card__identity"><strong>{entry.name}</strong><span className={`plugin-library-card__badge is-${entry.origin}`}>{ORIGIN_LABEL[entry.origin]}</span></div>
        </div>
        <p className="plugin-library-card__description">{entry.description || 'Sem descrição disponível.'}</p>
        <button type="button" className="button button--primary" onClick={() => setInstalling(entry.source_url)}>Instalar</button>
      </article>)}
      {!loading && !error && entries.length === 0 && <p className="plugin-library__empty">Nenhum plugin encontrado no momento.</p>}
    </div>
    {installing && <PluginInstallDialog key={installing} client={client} initialReference={installing} onClose={() => setInstalling(null)} onInstalled={() => { setInstalling(null); onInstalled() }} />}
  </div>
}

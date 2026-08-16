import { useCallback, useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { listPlugins, type PluginSummary } from '../../api/plugins'
import { SettingsSection } from '../settings/SettingsSection'
import { PluginCard } from './PluginCard'
import { PluginInstallDialog } from './PluginInstallDialog'
import { PluginLibrarySection } from './PluginLibrarySection'

export function PluginsSection({ client }: { client?: ApiClient }) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const [plugins, setPlugins] = useState<PluginSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dialog, setDialog] = useState(false)
  const [tab, setTab] = useState<'installed' | 'library'>('installed')
  const refresh = useCallback(() => listPlugins(apiClient).then((value) => { setPlugins(value); setError(null) }).catch(() => setError('Não foi possível carregar os plugins.')).finally(() => setLoading(false)), [apiClient])
  useEffect(() => { void refresh() }, [refresh])
  const activeCount = plugins.filter((plugin) => plugin.state === 'active').length
  const warningCount = plugins.reduce((count, plugin) => count + plugin.warnings.length, 0)
  return <SettingsSection
    eyebrow="EXTENSÕES / PLUGINS"
    title="Plugins"
    lede="Pacotes declarativos são inspecionados antes de qualquer contribuição ser ativada."
    actions={<button type="button" className="button button--primary plugins-section__install" onClick={() => setDialog(true)}><span className="plugins-section__install-mark" aria-hidden="true">+</span>Instalar plugin</button>}
  >
    <div className="plugins-section">
      <section className="plugins-section__overview" aria-label="Resumo dos plugins">
        <div className="plugins-section__overview-copy">
          <span className="plugins-section__signal" aria-hidden="true">EXTENSION REGISTRY</span>
          <h2>Amplie o workspace com segurança</h2>
          <p>Cada plugin passa por inspeção antes de entrar no ambiente. Revise as contribuições e mantenha somente o que está em uso.</p>
        </div>
        <dl className="plugins-section__stats">
          <div><dt>Instalados</dt><dd>{plugins.length}</dd></div>
          <div><dt>Ativos</dt><dd>{activeCount}</dd></div>
          <div><dt>Avisos</dt><dd className={warningCount > 0 ? 'has-warning' : undefined}>{warningCount}</dd></div>
        </dl>
      </section>

      <div className="plugins-section__tabs" role="tablist" aria-label="Seções de plugins">
        <button type="button" id="plugins-tab-installed" role="tab" aria-selected={tab === 'installed'} aria-controls="plugins-panel-installed" className={`plugins-section__tab ${tab === 'installed' ? 'is-active' : ''}`} onClick={() => setTab('installed')}>Instalados</button>
        <button type="button" id="plugins-tab-library" role="tab" aria-selected={tab === 'library'} aria-controls="plugins-panel-library" className={`plugins-section__tab ${tab === 'library' ? 'is-active' : ''}`} onClick={() => setTab('library')}>Biblioteca</button>
      </div>

      {tab === 'installed' && <div id="plugins-panel-installed" role="tabpanel" aria-labelledby="plugins-tab-installed">
        {loading && plugins.length === 0 && <div className="plugins-section__loading" aria-label="Carregando plugins"><span /><span /><span /></div>}
        {error && <div className="plugins-section__error" role="alert"><span className="plugins-section__status-mark" aria-hidden="true">!</span><div><strong>Não foi possível carregar os plugins</strong><p>{error}</p></div><button type="button" className="button button--secondary" onClick={() => { setLoading(true); void refresh() }}>Tentar novamente</button></div>}
        <div className="plugins-section__list" aria-label="Plugins instalados">
          {plugins.map((plugin) => <PluginCard key={plugin.plugin_id} plugin={plugin} client={apiClient} onChanged={() => void refresh()} />)}
          {!loading && !error && plugins.length === 0 && <div className="plugins-section__empty"><span className="plugins-section__empty-icon" aria-hidden="true">✦</span><div><h2>Nenhum plugin instalado</h2><p>Instale um pacote para adicionar Skills, servidores MCP ou agentes ao seu workspace.</p></div><button type="button" className="button button--secondary" onClick={() => setDialog(true)}>Explorar um plugin</button></div>}
        </div>
      </div>}

      {tab === 'library' && <div id="plugins-panel-library" role="tabpanel" aria-labelledby="plugins-tab-library"><PluginLibrarySection client={apiClient} onInstalled={() => void refresh()} /></div>}
    </div>
    {dialog && <PluginInstallDialog client={apiClient} onClose={() => setDialog(false)} onInstalled={() => { setDialog(false); void refresh() }} />}
  </SettingsSection>
}

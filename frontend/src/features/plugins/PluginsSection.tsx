import { useCallback, useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { listPlugins, type PluginSummary } from '../../api/plugins'
import { SettingsPage } from '../settings/SettingsPage'
import { PluginCard } from './PluginCard'
import { PluginInstallDialog } from './PluginInstallDialog'

export function PluginsSection({ client }: { client?: ApiClient }) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const [plugins, setPlugins] = useState<PluginSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dialog, setDialog] = useState(false)
  const refresh = useCallback(() => listPlugins(apiClient).then((value) => { setPlugins(value); setError(null) }).catch(() => setError('Não foi possível carregar os plugins.')).finally(() => setLoading(false)), [apiClient])
  useEffect(() => { void refresh() }, [refresh])
  return <SettingsPage><p className="eyebrow">EXTENSÕES / PLUGINS</p><h1>Plugins</h1><p className="settings-content__lede">Pacotes declarativos são inspecionados antes de qualquer contribuição ser ativada.</p><div className="plugins-section__actions"><button type="button" className="button button--primary" onClick={() => setDialog(true)}>Instalar plugin</button></div>{loading && plugins.length === 0 && <p>Carregando…</p>}{error && <p role="alert">{error}</p>}<div className="plugins-section__list">{plugins.map((plugin) => <PluginCard key={plugin.plugin_id} plugin={plugin} client={apiClient} onChanged={() => void refresh()} />)}{!loading && !error && plugins.length === 0 && <p>Nenhum plugin instalado ainda.</p>}</div>{dialog && <PluginInstallDialog client={apiClient} onClose={() => setDialog(false)} onInstalled={() => { setDialog(false); void refresh() }} />}</SettingsPage>
}

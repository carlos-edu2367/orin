import { useEffect, useMemo, useState } from 'react'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import { getAgentRuntimeSettings, setAgentRuntimeSettings } from '../../api/runtime'
import { SettingsPage } from './SettingsPage'

export function RuntimeSettingsPage({ client }: { client?: ApiClient }) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const [maxIterations, setMaxIterations] = useState<number | null>(null)
  const [draft, setDraft] = useState('24')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    void getAgentRuntimeSettings(apiClient, controller.signal).then((settings) => {
      setMaxIterations(settings.max_iterations)
      if (settings.max_iterations !== null) setDraft(String(settings.max_iterations))
    }).catch(() => setNotice('Não foi possível carregar o limite atual.')).finally(() => setLoading(false))
    return () => controller.abort()
  }, [apiClient])

  async function save() {
    const next = maxIterations === null ? null : Number(draft)
    if (next !== null && (!Number.isInteger(next) || next < 1)) {
      setNotice('Informe um número inteiro maior que zero.')
      return
    }
    setSaving(true)
    setNotice(null)
    try {
      const settings = await setAgentRuntimeSettings(apiClient, next)
      setMaxIterations(settings.max_iterations)
      if (settings.max_iterations !== null) setDraft(String(settings.max_iterations))
      setNotice('Limite salvo.')
    } catch {
      setNotice('Não foi possível salvar o limite.')
    } finally {
      setSaving(false)
    }
  }

  return <SettingsPage>
    <p className="eyebrow">RUNTIME</p>
    <h1>Interações do agente</h1>
    <p className="settings-content__lede">Defina quantas rodadas de ferramentas e raciocínio um turno pode executar. Sem limite remove o teto de rodadas; a proteção de uma hora contra processos travados continua ativa.</p>
    <section className="runtime-settings" aria-busy={loading}>
      <label className="provider-panel__toggle"><input type="checkbox" checked={maxIterations === null} disabled={loading || saving} onChange={(event) => setMaxIterations(event.target.checked ? null : Number(draft) || 24)} />Sem limite de interações</label>
      <label className="runtime-settings__field">Máximo de interações por turno<input aria-label="Máximo de interações por turno" type="number" min="1" step="1" value={draft} disabled={loading || saving || maxIterations === null} onChange={(event) => setDraft(event.target.value)} /></label>
      <p>Uma interação é uma nova rodada do agente após receber o resultado de uma ferramenta. O limite não interrompe uma resposta já em texto.</p>
      <button type="button" className="button button--primary" disabled={loading || saving} onClick={() => void save()}>Salvar limite</button>
      {notice && <p role="status" className="runtime-settings__notice">{notice}</p>}
    </section>
  </SettingsPage>
}

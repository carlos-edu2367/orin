import { useEffect, useState } from 'react'
import { ApiClient, createBrowserApiClient } from '../../api/client'
import { getAutostart, setAutostart, type AutostartStatus } from '../../api/autostart'
import { useSession } from '../../app/useSession'
import { SettingsSection } from './SettingsSection'

type CloseBehavior = 'ask' | 'background' | 'quit'

const CLOSE_CHOICES: ReadonlyArray<{ value: CloseBehavior; label: string; hint: string }> = [
  { value: 'ask', label: 'Perguntar', hint: 'Mostra a pergunta toda vez que você fecha a janela.' },
  { value: 'background', label: 'Manter em segundo plano', hint: 'A janela some, o Orin continua rodando e as tarefas agendadas seguem.' },
  { value: 'quit', label: 'Fechar o Orin', hint: 'Encerra tudo, inclusive as tarefas agendadas.' },
]

/**
 * Scheduled conversations only run while Orin does. This is where the person
 * decides what closing the window means, and whether Orin starts by itself,
 * windowless, when the computer is turned on.
 */
export function BackgroundSection({ client: providedClient }: { client?: ApiClient }) {
  const [client] = useState(() => providedClient ?? createBrowserApiClient())
  const { capabilities } = useSession()
  const desktop = typeof window !== 'undefined' ? window.orinDesktop : undefined
  const [autostart, setAutostartState] = useState<AutostartStatus | null>(null)
  const [closeBehavior, setCloseBehavior] = useState<CloseBehavior | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let active = true
    getAutostart(client).then((value) => { if (active) setAutostartState(value) }).catch(() => { if (active) setError(true) })
    desktop?.getPreferences?.().then((value) => { if (active && value) setCloseBehavior(value.closeBehavior) }).catch(() => undefined)
    return () => { active = false }
  }, [client, desktop])

  async function toggleAutostart(enabled: boolean) {
    setSaving(true)
    setError(false)
    try { setAutostartState(await setAutostart(client, enabled)) } catch { setError(true) } finally { setSaving(false) }
  }

  async function chooseClose(value: CloseBehavior) {
    setCloseBehavior(value)
    try {
      const saved = await desktop?.setPreferences?.({ closeBehavior: value })
      if (saved) setCloseBehavior(saved.closeBehavior)
    } catch { setError(true) }
  }

  const canChange = capabilities.ui_updater
  return (
    <SettingsSection eyebrow="SISTEMA / SEGUNDO PLANO">
      <section className="installation-status background-settings" aria-labelledby="background-title">
        <div className="installation-status__heading">
          <div><p className="eyebrow">TAREFAS AGENDADAS</p><h2 id="background-title">Segundo plano</h2></div>
        </div>
        <p className="background-settings__lede">Conversas agendadas só rodam enquanto o Orin está ligado. Deixe-o em segundo plano para que elas aconteçam sem uma janela aberta no seu caminho.</p>
        {error && <p role="alert">Não foi possível salvar essa configuração. Tente de novo.</p>}

        {autostart?.supported ? (
          <label className="background-settings__row">
            <input
              type="checkbox"
              checked={autostart.enabled}
              disabled={saving || !canChange}
              onChange={(event) => void toggleAutostart(event.target.checked)}
            />
            <span>
              <strong>Iniciar o Orin em segundo plano ao ligar o computador</strong>
              <small>Abre sem janela, só com o ícone na bandeja do sistema. Use o ícone para abrir o Orin quando quiser.</small>
            </span>
          </label>
        ) : autostart ? (
          <p className="installation-status__managed">Iniciar com o computador está disponível no Orin instalado pelo instalador.</p>
        ) : null}

        {desktop?.getPreferences && closeBehavior && (
          <fieldset className="background-settings__group">
            <legend>Ao fechar a janela do Orin</legend>
            {CLOSE_CHOICES.map((choice) => (
              <label key={choice.value} className="background-settings__row">
                <input type="radio" name="close-behavior" value={choice.value} checked={closeBehavior === choice.value} onChange={() => void chooseClose(choice.value)} />
                <span><strong>{choice.label}</strong><small>{choice.hint}</small></span>
              </label>
            ))}
          </fieldset>
        )}
      </section>
    </SettingsSection>
  )
}

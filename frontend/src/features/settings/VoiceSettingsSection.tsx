import { useEffect, useState } from 'react'
import {
  loadLiveVoicePrefs,
  onLiveVoicePrefsChange,
  saveLiveVoicePrefs,
  type LiveVoicePrefs,
  type SttChoice,
  type TtsChoice,
} from '../conversations/live/liveVoicePrefs'
import { SettingsSection } from './SettingsSection'

const STT_OPTIONS: ReadonlyArray<{ value: SttChoice; label: string }> = [
  { value: 'auto', label: 'Automático' },
  { value: 'local_sidecar', label: 'Whisper local' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'browser', label: 'Navegador' },
]

const TTS_OPTIONS: ReadonlyArray<{ value: TtsChoice; label: string }> = [
  { value: 'auto', label: 'Automático' },
  { value: 'system', label: 'Voz do sistema' },
  { value: 'chatterbox', label: 'Chatterbox' },
  { value: 'kokoro', label: 'Kokoro' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'openai', label: 'OpenAI' },
]

export function VoiceSettingsSection() {
  const [prefs, setPrefs] = useState<LiveVoicePrefs>(() => loadLiveVoicePrefs())
  const [savedNotice, setSavedNotice] = useState(false)

  useEffect(() => onLiveVoicePrefsChange(setPrefs), [])

  function updatePrefs(patch: Partial<LiveVoicePrefs>) {
    const next: LiveVoicePrefs = { ...prefs, ...patch }
    setPrefs(next)
    saveLiveVoicePrefs(next)
    setSavedNotice(true)
  }

  return (
    <SettingsSection
      eyebrow="VOZ / ÁUDIO"
      title="Voz"
      lede="Escolha como o Orin ouve e fala com você nas conversas por voz."
    >
      <section className="runtime-settings voice-settings-section" data-testid="settings-voice-section">
        <label className="runtime-settings__field">
          Reconhecimento de fala
          <select
            aria-label="Reconhecimento de fala"
            data-testid="voice-stt-select"
            value={prefs.stt}
            onChange={(event) => updatePrefs({ stt: event.target.value as SttChoice })}
          >
            {STT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="runtime-settings__field">
          Síntese de voz
          <select
            aria-label="Síntese de voz"
            data-testid="voice-tts-select"
            value={prefs.tts}
            onChange={(event) => updatePrefs({ tts: event.target.value as TtsChoice })}
          >
            {TTS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="runtime-settings__field">
          <span>Velocidade da fala ({prefs.rate.toFixed(2)}×)</span>
          <input
            aria-label="Velocidade da fala"
            data-testid="voice-rate-slider"
            type="range"
            min="0.7"
            max="1.5"
            step="0.05"
            value={prefs.rate}
            onChange={(event) => updatePrefs({ rate: Number(event.target.value) })}
          />
        </label>

        <label className="provider-panel__toggle">
          <input
            type="checkbox"
            data-testid="voice-barge-in-toggle"
            checked={prefs.bargeIn}
            onChange={(event) => updatePrefs({ bargeIn: event.target.checked })}
          />
          Interromper ao falar (Barge-in)
        </label>

        <label className="provider-panel__toggle">
          <input
            type="checkbox"
            data-testid="voice-wake-word-toggle"
            checked={prefs.wakeWordEnabled}
            onChange={(event) => updatePrefs({ wakeWordEnabled: event.target.checked })}
          />
          Ativar dizendo &quot;Orin&quot;
        </label>

        {savedNotice && (
          <p role="status" className="runtime-settings__notice" data-testid="voice-saved-notice">
            ✓ Preferências de voz salvas
          </p>
        )}
      </section>
    </SettingsSection>
  )
}

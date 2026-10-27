import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { connectLiveVoiceSession } from '../../api/conversations'
import {
  getLiveVoiceCapabilities,
  getLocalWhisperInstall,
  startLocalWhisperInstall,
  UNAVAILABLE_CAPABILITIES,
  type LiveVoiceCapabilities,
  type LocalWhisperInstallStatus,
} from '../../api/liveVoice'
import { browserRecognitionSupported, isElectron } from './live/browserRecognition'
import { LevelMeter } from './live/LevelMeter'
import { listMicrophones, MicAccessError, MicCapture } from './live/liveAudio'
import { listSystemVoices, onSystemVoicesChanged, SpeechOutput, type SystemVoice } from './live/liveSpeech'
import {
  loadLiveVoicePrefs,
  resolveStt,
  resolveTts,
  saveLiveVoicePrefs,
  STT_LABELS,
  type LiveVoicePrefs,
  type SttChoice,
  type TtsChoice,
} from './live/liveVoicePrefs'

export type LiveVoiceQuickSettingsProps = {
  isOpen: boolean
  client?: ApiClient
  companionName?: string
  onClose: () => void
}

type OptionCardProps = {
  name: string
  value: string
  checked: boolean
  disabled?: boolean
  title: string
  description: string
  badge?: { text: string; tone: 'ok' | 'warn' | 'muted' }
  onSelect: () => void
  children?: ReactNode
  testId?: string
}

function OptionCard({ name, value, checked, disabled, title, description, badge, onSelect, children, testId }: OptionCardProps) {
  return (
    <label className={`voice-option${checked ? ' is-checked' : ''}${disabled ? ' is-disabled' : ''}`} data-testid={testId}>
      <input type="radio" name={name} value={value} checked={checked} disabled={disabled} onChange={onSelect} />
      <span className="voice-option__body">
        <span className="voice-option__head">
          <strong>{title}</strong>
          {badge && <span className={`voice-badge is-${badge.tone}`}>{badge.text}</span>}
        </span>
        <span className="voice-option__desc">{description}</span>
        {children}
      </span>
    </label>
  )
}

const SAMPLE_TEXT = 'Oi! Essa é a minha voz. Se gostar, é só fechar e começar a conversar.'

export function LiveVoiceQuickSettings(props: LiveVoiceQuickSettingsProps) {
  // Mounted only while open, so every opening starts from the saved preferences.
  if (!props.isOpen) return null
  return <VoiceSettingsPanel {...props} />
}

function VoiceSettingsPanel({ client: providedClient, companionName = 'Orin', onClose }: LiveVoiceQuickSettingsProps) {
  const client = useMemo(() => providedClient ?? createBrowserApiClient(), [providedClient])
  const [prefs, setPrefs] = useState<LiveVoicePrefs>(() => loadLiveVoicePrefs())
  const [caps, setCaps] = useState<LiveVoiceCapabilities | null>(null)
  const [install, setInstall] = useState<LocalWhisperInstallStatus | null>(null)
  const [mics, setMics] = useState<Array<{ deviceId: string; label: string }>>([])
  const [micError, setMicError] = useState<string | null>(null)
  const [voices, setVoices] = useState<SystemVoice[]>(() => listSystemVoices())
  const [sampleState, setSampleState] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const micRef = useRef<MicCapture | null>(null)
  const sampleRef = useRef<SpeechOutput | null>(null)
  const browserSupported = useMemo(() => browserRecognitionSupported(), [])

  const loadCaps = useCallback((signal?: AbortSignal) => {
    getLiveVoiceCapabilities(client, signal).then(setCaps).catch(() => setCaps((current) => current ?? UNAVAILABLE_CAPABILITIES))
  }, [client])

  useEffect(() => {
    const controller = new AbortController()
    loadCaps(controller.signal)
    getLocalWhisperInstall(client, controller.signal).then(setInstall).catch(() => {})
    const offVoices = onSystemVoicesChanged(() => setVoices(listSystemVoices()))
    return () => {
      controller.abort()
      offVoices()
    }
  }, [client, loadCaps])

  // While the panel is open the chosen microphone is live, so the meter proves it works.
  useEffect(() => {
    let cancelled = false
    MicCapture.open({ deviceId: prefs.micDeviceId })
      .then(async (mic) => {
        if (cancelled) {
          mic.close()
          return
        }
        micRef.current = mic
        // Device names are only readable once permission was granted.
        setMics(await listMicrophones().catch(() => []))
      })
      .catch((error: unknown) => {
        if (cancelled) return
        setMicError(error instanceof MicAccessError || error instanceof Error ? error.message : 'Não foi possível abrir o microfone.')
        void listMicrophones().then(setMics).catch(() => {})
      })
    return () => {
      cancelled = true
      micRef.current?.close()
      micRef.current = null
    }
  }, [prefs.micDeviceId])

  // Poll a running install until it settles, then re-read what can run.
  useEffect(() => {
    if (install?.state !== 'running') return
    const timer = window.setInterval(() => {
      getLocalWhisperInstall(client)
        .then((status) => {
          setInstall(status)
          if (status.state !== 'running') loadCaps()
        })
        .catch(() => {})
    }, 2500)
    return () => window.clearInterval(timer)
  }, [install?.state, client, loadCaps])

  useEffect(() => () => sampleRef.current?.dispose(), [])

  const meterLevel = useCallback(() => micRef.current?.level ?? 0, [])

  const capabilities = caps ?? UNAVAILABLE_CAPABILITIES
  const autoStt = resolveStt('auto', capabilities, browserSupported)
  const effectiveStt = resolveStt(prefs.stt, capabilities, browserSupported)
  const effectiveTts = resolveTts(prefs.tts, capabilities)
  const local = capabilities.stt.local_sidecar
  const openaiStt = capabilities.stt.openai
  const openaiTts = capabilities.tts.openai
  const hardware = capabilities.hardware

  function update(patch: Partial<LiveVoicePrefs>) {
    setPrefs((current) => {
      const next = { ...current, ...patch }
      saveLiveVoicePrefs(next)
      return next
    })
    setSavedAt(Date.now())
  }

  async function handleInstall() {
    try {
      setInstall(await startLocalWhisperInstall(client))
    } catch {
      setInstall({ state: 'failed', message: 'Não consegui iniciar a instalação. Tente pelo terminal.', command: local.installCommand ?? 'uv pip install faster-whisper' })
    }
  }

  function playSample() {
    sampleRef.current?.stop()
    const output = sampleRef.current ?? new SpeechOutput()
    sampleRef.current = output
    output.setSystemVoice({ voiceUri: prefs.systemVoiceUri, rate: prefs.rate, pitch: prefs.pitch })
    output.setEvents({ onIdle: () => setSampleState(null) })
    if (effectiveTts === 'system') {
      setSampleState('Tocando a voz do sistema…')
      output.speakSystem(SAMPLE_TEXT)
      return
    }
    setSampleState('Gerando a voz da OpenAI…')
    const socket = connectLiveVoiceSession('voice-preview', (event) => {
      if (event.type === 'session.ready') {
        socket?.sendSpeakText(SAMPLE_TEXT, `Você é ${companionName}. Fale em português do Brasil, com calor e naturalidade.`)
      } else if (event.type === 'tts.chunk') {
        setSampleState('Tocando a voz da OpenAI…')
        output.playPcm(event.clean_text, event.pcm_base64, event.sample_rate, event.chunk_seq)
        socket?.close()
      } else if (event.type === 'tts.error') {
        setSampleState(event.message)
        socket?.close()
      }
    }, { csrfToken: client.socketCsrfToken() })
    socket?.sendConfig({ tts_provider: 'openai', tts_model: 'gpt-4o-mini-tts', voice_id: prefs.openaiVoice, speed: prefs.rate })
  }

  const localBadge = local.available
    ? { text: local.device === 'cuda' ? 'Pronto · GPU' : 'Pronto · CPU', tone: 'ok' as const }
    : install?.state === 'running'
      ? { text: 'Instalando…', tone: 'warn' as const }
      : { text: 'Não instalado', tone: 'muted' as const }

  const sttChoices: Array<{ value: SttChoice; title: string; description: string; disabled: boolean; badge: OptionCardProps['badge']; extra?: ReactNode }> = [
    {
      value: 'auto',
      title: 'Automático',
      description: autoStt ? `Usa o melhor disponível agora: ${STT_LABELS[autoStt]}.` : 'Nada disponível ainda — instale o Whisper local abaixo.',
      disabled: false,
      badge: { text: 'Recomendado', tone: 'ok' },
    },
    {
      value: 'local_sidecar',
      title: 'Whisper local (grátis, privado)',
      description: hardware.hasCuda
        ? `Roda na sua ${hardware.deviceName ?? 'GPU'}; nada sai do seu computador.`
        : 'Roda no seu computador; nada sai da sua máquina.',
      disabled: !local.available,
      badge: localBadge,
      extra: !local.available ? (
        <span className="voice-option__extra">
          <button
            type="button"
            className="voice-button is-primary"
            data-testid="live-install-whisper-button"
            onClick={(event) => {
              event.preventDefault()
              void handleInstall()
            }}
            disabled={install?.state === 'running'}
          >
            {install?.state === 'running' ? 'Instalando…' : 'Instalar agora'}
          </button>
          <span className="voice-option__note" role="status">
            {install?.message || `Ou pelo terminal: ${local.installCommand ?? install?.command ?? 'uv pip install faster-whisper'}`}
          </span>
        </span>
      ) : undefined,
    },
    {
      value: 'openai',
      title: 'OpenAI (nuvem)',
      description: openaiStt.available ? 'Usa a sua chave OpenAI. Rápido e preciso.' : 'Configure uma chave OpenAI em Configurações → Provedores.',
      disabled: !openaiStt.available,
      badge: openaiStt.available ? { text: 'Pronto', tone: 'ok' } : { text: 'Sem chave', tone: 'muted' },
    },
    {
      value: 'browser',
      title: 'Reconhecimento do navegador',
      description: browserSupported
        ? 'Sem instalar nada; o áudio passa pelo serviço do navegador.'
        : isElectron()
          ? 'Não funciona no app desktop do Orin.'
          : 'Este navegador não oferece reconhecimento de fala.',
      disabled: !browserSupported,
      badge: browserSupported ? { text: 'Pronto', tone: 'ok' } : { text: 'Indisponível', tone: 'muted' },
    },
  ]

  return (
    <div className="voice-settings" data-testid="live-voice-quick-settings" role="dialog" aria-modal="true" aria-labelledby="voice-settings-title">
      <section className="voice-settings__panel">
        <header className="voice-settings__header">
          <div>
            <span className="voice-settings__eyebrow">Conversa por voz</span>
            <h2 id="voice-settings-title">Configurar voz de {companionName}</h2>
          </div>
          <button type="button" className="voice-button is-quiet" data-testid="live-quick-close-button" onClick={onClose} aria-label="Fechar configuração de voz">
            Fechar
          </button>
        </header>

        <div className="voice-settings__section" data-testid="live-quick-hardware-banner">
          <h3>1. Microfone</h3>
          <div className="voice-field">
            <label htmlFor="live-quick-mic-select">Microfone</label>
            <select
              id="live-quick-mic-select"
              data-testid="live-quick-mic-select"
              value={prefs.micDeviceId}
              onChange={(event) => {
                setMicError(null)
                update({ micDeviceId: event.target.value })
              }}
            >
              <option value="default">Padrão do sistema</option>
              {mics.filter((mic) => mic.deviceId !== 'default').map((mic) => (
                <option key={mic.deviceId} value={mic.deviceId}>{mic.label}</option>
              ))}
            </select>
          </div>
          {micError ? (
            <p className="voice-settings__error" role="alert">{micError}</p>
          ) : (
            <LevelMeter level={meterLevel} label="Nível do microfone (teste)" />
          )}
          <p className="voice-settings__hint">Fale algo: as barras devem subir. Se não subirem, escolha outro microfone.</p>
          <div className="voice-field is-inline">
            <label htmlFor="live-quick-sensitivity">Sensibilidade</label>
            <input
              id="live-quick-sensitivity"
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={prefs.sensitivity}
              onChange={(event) => update({ sensitivity: Number(event.target.value) })}
            />
            <span className="voice-field__value">{Math.round(prefs.sensitivity * 100)}%</span>
          </div>
          <div className="voice-field is-inline">
            <label htmlFor="live-quick-silence">Pausa que encerra a frase</label>
            <input
              id="live-quick-silence"
              type="range"
              min={400}
              max={2000}
              step={100}
              value={prefs.endSilenceMs}
              onChange={(event) => update({ endSilenceMs: Number(event.target.value) })}
            />
            <span className="voice-field__value">{(prefs.endSilenceMs / 1000).toFixed(1)}s</span>
          </div>
        </div>

        <div className="voice-settings__section" data-testid="live-quick-stt-options" role="radiogroup" aria-labelledby="voice-settings-stt-title">
          <h3 id="voice-settings-stt-title">2. Como {companionName} te ouve</h3>
          {sttChoices.map((choice) => (
            <OptionCard
              key={choice.value}
              name="live-stt"
              value={choice.value}
              checked={prefs.stt === choice.value}
              disabled={choice.disabled && prefs.stt !== choice.value}
              title={choice.title}
              description={choice.description}
              badge={choice.badge}
              onSelect={() => update({ stt: choice.value })}
              testId={`live-stt-option-${choice.value}`}
            >
              {choice.extra}
            </OptionCard>
          ))}
          {prefs.stt !== 'auto' && effectiveStt === null && (
            <p className="voice-settings__error" role="alert">Essa opção não está disponível agora; volte para Automático.</p>
          )}
        </div>

        <div className="voice-settings__section" data-testid="live-quick-tts-options" role="radiogroup" aria-labelledby="voice-settings-tts-title">
          <h3 id="voice-settings-tts-title">3. A voz de {companionName}</h3>
          {([
            { value: 'auto', title: 'Automático', description: `Usa ${openaiTts.available ? 'a voz natural da OpenAI' : 'a voz do sistema'}.`, disabled: false, badge: { text: 'Recomendado', tone: 'ok' } },
            { value: 'system', title: 'Voz do sistema (grátis, local)', description: 'As vozes já instaladas no seu computador.', disabled: false, badge: { text: 'Pronto', tone: 'ok' } },
            {
              value: 'openai',
              title: 'Voz natural OpenAI',
              description: openaiTts.available ? 'Muito mais expressiva; usa a sua chave OpenAI.' : 'Configure uma chave OpenAI em Configurações → Provedores.',
              disabled: !openaiTts.available,
              badge: openaiTts.available ? { text: 'Pronto', tone: 'ok' } : { text: 'Sem chave', tone: 'muted' },
            },
          ] as Array<{ value: TtsChoice; title: string; description: string; disabled: boolean; badge: OptionCardProps['badge'] }>).map((choice) => (
            <OptionCard
              key={choice.value}
              name="live-tts"
              value={choice.value}
              checked={prefs.tts === choice.value}
              disabled={choice.disabled && prefs.tts !== choice.value}
              title={choice.title}
              description={choice.description}
              badge={choice.badge}
              onSelect={() => update({ tts: choice.value })}
              testId={`live-tts-option-${choice.value}`}
            />
          ))}

          {effectiveTts === 'system' ? (
            <div className="voice-field">
              <label htmlFor="live-quick-system-voice">Voz</label>
              <select
                id="live-quick-system-voice"
                data-testid="live-quick-tts-select"
                value={prefs.systemVoiceUri ?? ''}
                onChange={(event) => update({ systemVoiceUri: event.target.value || null })}
              >
                <option value="">Melhor voz em português</option>
                {voices.map((voice) => (
                  <option key={voice.uri} value={voice.uri}>{voice.name} ({voice.lang})</option>
                ))}
              </select>
            </div>
          ) : (
            <div className="voice-field">
              <label htmlFor="live-quick-openai-voice">Voz</label>
              <select
                id="live-quick-openai-voice"
                data-testid="live-quick-tts-select"
                value={prefs.openaiVoice}
                onChange={(event) => update({ openaiVoice: event.target.value })}
              >
                {(openaiTts.voices.length ? openaiTts.voices : ['coral', 'nova', 'shimmer', 'sage', 'ash', 'onyx']).map((voice) => (
                  <option key={voice} value={voice}>{voice}</option>
                ))}
              </select>
            </div>
          )}
          <div className="voice-field is-inline">
            <label htmlFor="live-quick-rate">Velocidade</label>
            <input id="live-quick-rate" type="range" min={0.7} max={1.5} step={0.05} value={prefs.rate} onChange={(event) => update({ rate: Number(event.target.value) })} />
            <span className="voice-field__value">{prefs.rate.toFixed(2)}×</span>
          </div>
          {effectiveTts === 'system' && (
            <div className="voice-field is-inline">
              <label htmlFor="live-quick-pitch">Tom</label>
              <input id="live-quick-pitch" type="range" min={0.6} max={1.5} step={0.05} value={prefs.pitch} onChange={(event) => update({ pitch: Number(event.target.value) })} />
              <span className="voice-field__value">{prefs.pitch.toFixed(2)}</span>
            </div>
          )}
          <div className="voice-settings__row">
            <button type="button" className="voice-button" data-testid="live-quick-test-voice-button" onClick={playSample}>
              Ouvir amostra
            </button>
            {sampleState && <span className="voice-settings__hint" role="status" data-testid="live-quick-voice-preview-status">{sampleState}</span>}
          </div>
        </div>

        <div className="voice-settings__section">
          <h3>4. Conversa</h3>
          <label className="voice-toggle">
            <input type="checkbox" checked={prefs.bargeIn} onChange={(event) => update({ bargeIn: event.target.checked })} />
            <span>Parar de falar quando eu começar a falar por cima</span>
          </label>
          <p className="voice-settings__hint">
            Sem fone de ouvido? Use o <strong>Modo sem fone</strong> na conversa: o microfone só abre enquanto você segura Espaço.
          </p>
        </div>

        <footer className="voice-settings__footer">
          <span className="voice-settings__hint" role="status" data-testid="live-quick-saved-notice">
            {savedAt ? '✓ Salvo neste dispositivo' : hardware.hasCuda ? `${hardware.deviceName ?? 'GPU'} detectada` : ''}
          </span>
          <button type="button" className="voice-button is-primary" onClick={onClose}>Pronto</button>
        </footer>
      </section>
    </div>
  )
}

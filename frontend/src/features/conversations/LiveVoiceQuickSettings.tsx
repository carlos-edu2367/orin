import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { connectLiveVoiceSession } from '../../api/conversations'
import {
  deleteChatterboxVoice,
  getLiveVoiceCapabilities,
  startEngineInstall,
  UNAVAILABLE_CAPABILITIES,
  uploadChatterboxVoice,
  type EngineInstallStatus,
  type LiveTtsEngineId,
  type LiveVoiceCapabilities,
  type LiveVoiceModel,
  type LocalEngineId,
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
  sortVoicesForPortuguese,
  STT_LABELS,
  sttModelFor,
  TTS_LABELS,
  ttsSelection,
  voiceLabel,
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

type Badge = { text: string; tone: 'ok' | 'warn' | 'muted' }

type OptionCardProps = {
  name: string
  value: string
  checked: boolean
  disabled?: boolean
  title: string
  description: string
  badge?: Badge
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

function installBadge(status: EngineInstallStatus | undefined, gpuHint = false): Badge {
  if (!status) return { text: 'Indisponível', tone: 'muted' }
  if (status.state === 'running') return { text: 'Instalando…', tone: 'warn' }
  if (status.installed) {
    const gpu = status.device === 'cuda' || status.device === 'mps'
    return { text: gpu ? 'Pronto · GPU' : 'Pronto · CPU', tone: gpu || !gpuHint ? 'ok' : 'warn' }
  }
  if (status.state === 'failed') return { text: 'Falhou', tone: 'warn' }
  return { text: 'Não instalado', tone: 'muted' }
}

function InstallControl({ engine, status, onInstall }: { engine: LocalEngineId; status?: EngineInstallStatus; onInstall: (engine: LocalEngineId) => void }) {
  if (!status || status.installed) return null
  const running = status.state === 'running'
  return (
    <span className="voice-option__extra">
      <button
        type="button"
        className="voice-button is-primary"
        data-testid={`live-install-${engine}-button`}
        disabled={running}
        onClick={(event) => {
          event.preventDefault()
          onInstall(engine)
        }}
      >
        {running ? 'Instalando…' : status.state === 'failed' ? 'Tentar de novo' : `Instalar agora${status.sizeHint ? ` (${status.sizeHint})` : ''}`}
      </button>
      <span className="voice-option__note" role="status">
        {status.message || 'Instala num ambiente isolado; o Orin não é alterado.'}
      </span>
    </span>
  )
}

const VENDOR_LABELS: Record<string, string> = {
  elevenlabs: 'ElevenLabs', google: 'Google', openai: 'OpenAI', mistralai: 'Mistral', minimax: 'MiniMax', microsoft: 'Microsoft',
  deepgram: 'Deepgram', 'fish-audio': 'Fish Audio', 'x-ai': 'xAI', qwen: 'Qwen', heygen: 'HeyGen', hexgrad: 'Kokoro (nuvem)',
  'bytedance-seed': 'ByteDance', canopylabs: 'Canopy Labs', sesame: 'Sesame',
}

function ModelSelect({ id, models, value, onChange, testId }: { id: string; models: LiveVoiceModel[]; value: string; onChange: (id: string) => void; testId: string }) {
  const groups = useMemo(() => {
    const byVendor = new Map<string, LiveVoiceModel[]>()
    for (const item of models) byVendor.set(item.vendor, [...(byVendor.get(item.vendor) ?? []), item])
    return [...byVendor.entries()]
  }, [models])
  return (
    <select id={id} data-testid={testId} value={value} onChange={(event) => onChange(event.target.value)}>
      {groups.length > 1
        ? groups.map(([vendor, items]) => (
            <optgroup key={vendor} label={VENDOR_LABELS[vendor] ?? vendor}>
              {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </optgroup>
          ))
        : models.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select>
  )
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).replace(/^data:[^,]*,/, ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
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
  const [mics, setMics] = useState<Array<{ deviceId: string; label: string }>>([])
  const [micError, setMicError] = useState<string | null>(null)
  const [voices, setVoices] = useState<SystemVoice[]>(() => listSystemVoices())
  const [sampleState, setSampleState] = useState<string | null>(null)
  const [clipState, setClipState] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const micRef = useRef<MicCapture | null>(null)
  const sampleRef = useRef<SpeechOutput | null>(null)
  const clipInputRef = useRef<HTMLInputElement | null>(null)
  const browserSupported = useMemo(() => browserRecognitionSupported(), [])

  const loadCaps = useCallback((signal?: AbortSignal) => {
    getLiveVoiceCapabilities(client, signal).then(setCaps).catch(() => setCaps((current) => current ?? UNAVAILABLE_CAPABILITIES))
  }, [client])

  useEffect(() => {
    const controller = new AbortController()
    loadCaps(controller.signal)
    const offVoices = onSystemVoicesChanged(() => setVoices(listSystemVoices()))
    return () => {
      controller.abort()
      offVoices()
    }
  }, [loadCaps])

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

  const capabilities = caps ?? UNAVAILABLE_CAPABILITIES
  const installing = [capabilities.stt.local_sidecar.install, capabilities.tts.kokoro.install, capabilities.tts.chatterbox.install]
    .some((status) => status?.state === 'running')

  // Re-read what can run while an engine installs, until it settles.
  useEffect(() => {
    if (!installing) return
    const timer = window.setInterval(() => loadCaps(), 2500)
    return () => window.clearInterval(timer)
  }, [installing, loadCaps])

  useEffect(() => () => sampleRef.current?.dispose(), [])

  const meterLevel = useCallback(() => micRef.current?.level ?? 0, [])

  const autoStt = resolveStt('auto', capabilities, browserSupported)
  const effectiveStt = resolveStt(prefs.stt, capabilities, browserSupported)
  const effectiveTts = resolveTts(prefs.tts, capabilities)
  const selection = effectiveTts === 'system' ? null : ttsSelection(prefs, capabilities, effectiveTts)
  const hardware = capabilities.hardware
  const stt = capabilities.stt
  const tts = capabilities.tts

  function update(patch: Partial<LiveVoicePrefs>) {
    setPrefs((current) => {
      const next = { ...current, ...patch }
      saveLiveVoicePrefs(next)
      return next
    })
    setSavedAt(Date.now())
  }

  function chooseTtsModel(engine: LiveTtsEngineId, modelId: string) {
    update({ ttsModels: { ...prefs.ttsModels, [engine]: modelId } })
  }

  function chooseTtsVoice(engine: LiveTtsEngineId, modelId: string, voice: string) {
    update({ ttsVoices: { ...prefs.ttsVoices, [`${engine}:${modelId}`]: voice } })
  }

  async function handleInstall(engine: LocalEngineId) {
    try {
      const status = await startEngineInstall(client, engine)
      setCaps((current) => {
        const base = current ?? UNAVAILABLE_CAPABILITIES
        if (engine === 'whisper') return { ...base, stt: { ...base.stt, local_sidecar: { ...base.stt.local_sidecar, install: status } } }
        return { ...base, tts: { ...base.tts, [engine]: { ...base.tts[engine], install: status } } }
      })
    } catch {
      setSampleState('Não consegui iniciar a instalação. Tente de novo.')
    }
  }

  async function handleClipUpload(file: File) {
    setClipState('Enviando o clipe…')
    try {
      const clip = await uploadChatterboxVoice(client, {
        name: file.name.replace(/\.[^.]+$/, ''),
        filename: file.name,
        dataBase64: await fileToBase64(file),
      })
      chooseTtsVoice('chatterbox', selection?.modelId || 'chatterbox-multilingual', clip.id)
      setClipState(`Voz “${clip.name}” adicionada.`)
      loadCaps()
    } catch {
      setClipState('Não consegui usar esse arquivo. Envie um WAV/MP3 de até 12 MB.')
    }
  }

  async function handleClipDelete(clipId: string) {
    try {
      await deleteChatterboxVoice(client, clipId)
      chooseTtsVoice('chatterbox', selection?.modelId || 'chatterbox-multilingual', 'default')
      setClipState('Voz removida.')
      loadCaps()
    } catch {
      setClipState('Não consegui remover essa voz.')
    }
  }

  function playSample() {
    sampleRef.current?.stop()
    const output = sampleRef.current ?? new SpeechOutput()
    sampleRef.current = output
    output.setSystemVoice({ voiceUri: prefs.systemVoiceUri, rate: prefs.rate, pitch: prefs.pitch })
    output.setEvents({ onIdle: () => setSampleState(null) })
    if (effectiveTts === 'system' || !selection) {
      setSampleState('Tocando a voz do sistema…')
      output.speakSystem(SAMPLE_TEXT)
      return
    }
    const engineLabel = TTS_LABELS[effectiveTts]
    setSampleState(`Gerando a voz (${engineLabel})…`)
    const socket = connectLiveVoiceSession('voice-preview', (event) => {
      if (event.type === 'session.ready') {
        socket?.sendSpeakText(SAMPLE_TEXT, `Você é ${companionName}. Fale em português do Brasil, com calor e naturalidade.`)
      } else if (event.type === 'tts.chunk') {
        setSampleState(`Tocando ${engineLabel}…`)
        if (event.encoding && event.encoding !== 'pcm16' && event.audio_base64) output.playEncoded(event.clean_text, event.audio_base64, event.chunk_seq)
        else if (event.pcm_base64) output.playPcm(event.clean_text, event.pcm_base64, event.sample_rate, event.chunk_seq)
        socket?.close()
      } else if (event.type === 'tts.error') {
        setSampleState(event.message)
        socket?.close()
      }
    }, { csrfToken: client.socketCsrfToken() })
    socket?.sendConfig({ tts_provider: effectiveTts, tts_model: selection.modelId, voice_id: selection.voice, speed: prefs.rate, language: 'pt' })
  }

  const sttChoices: Array<{ value: SttChoice; title: string; description: string; disabled: boolean; badge: Badge; extra?: ReactNode }> = [
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
      disabled: !stt.local_sidecar.available,
      badge: installBadge(stt.local_sidecar.install),
      extra: <InstallControl engine="whisper" status={stt.local_sidecar.install} onInstall={(engine) => void handleInstall(engine)} />,
    },
    {
      value: 'openrouter',
      title: 'OpenRouter (nuvem)',
      description: stt.openrouter.available ? 'Whisper, Voxtral, Scribe e outros, com a sua chave OpenRouter.' : 'Configure uma chave OpenRouter em Configurações → Provedores.',
      disabled: !stt.openrouter.available,
      badge: stt.openrouter.available ? { text: 'Pronto', tone: 'ok' } : { text: 'Sem chave', tone: 'muted' },
      extra: stt.openrouter.available && stt.openrouter.models.length > 0 && prefs.stt === 'openrouter' ? (
        <span className="voice-field voice-option__extra">
          <label htmlFor="live-quick-stt-model">Modelo</label>
          <ModelSelect
            id="live-quick-stt-model"
            testId="live-quick-stt-model-select"
            models={stt.openrouter.models}
            value={sttModelFor(prefs, capabilities, 'openrouter') ?? ''}
            onChange={(modelId) => update({ sttModels: { ...prefs.sttModels, openrouter: modelId } })}
          />
        </span>
      ) : undefined,
    },
    {
      value: 'openai',
      title: 'OpenAI (nuvem)',
      description: stt.openai.available ? 'Usa a sua chave OpenAI. Rápido e preciso.' : 'Configure uma chave OpenAI em Configurações → Provedores.',
      disabled: !stt.openai.available,
      badge: stt.openai.available ? { text: 'Pronto', tone: 'ok' } : { text: 'Sem chave', tone: 'muted' },
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

  const autoTts = resolveTts('auto', capabilities)
  const ttsChoices: Array<{ value: TtsChoice; title: string; description: string; disabled: boolean; badge: Badge; extra?: ReactNode }> = [
    {
      value: 'auto',
      title: 'Automático',
      description: `Usa ${autoTts === 'system' ? 'a voz do sistema' : TTS_LABELS[autoTts]} agora.`,
      disabled: false,
      badge: { text: 'Recomendado', tone: 'ok' },
    },
    {
      value: 'system',
      title: 'Voz do sistema (grátis)',
      description: 'As vozes já instaladas no seu computador. Funciona sempre.',
      disabled: false,
      badge: { text: 'Pronto', tone: 'ok' },
    },
    {
      value: 'chatterbox',
      title: 'Chatterbox (local, mais natural)',
      description: hardware.hasCuda
        ? `Voz expressiva que pode clonar uma voz sua; roda na sua ${hardware.deviceName ?? 'GPU'}.`
        : 'Voz expressiva e clonável. Sem GPU NVIDIA fica lenta (vários segundos por frase).',
      disabled: !tts.chatterbox.available,
      badge: installBadge(tts.chatterbox.install, true),
      extra: <InstallControl engine="chatterbox" status={tts.chatterbox.install} onInstall={(engine) => void handleInstall(engine)} />,
    },
    {
      value: 'kokoro',
      title: 'Kokoro (local, leve)',
      description: 'Rápida até na CPU, sem internet. Vozes em português: Dora, Alex e Santa.',
      disabled: !tts.kokoro.available,
      badge: installBadge(tts.kokoro.install),
      extra: <InstallControl engine="kokoro" status={tts.kokoro.install} onInstall={(engine) => void handleInstall(engine)} />,
    },
    {
      value: 'openrouter',
      title: 'OpenRouter (nuvem)',
      description: tts.openrouter.available
        ? `${tts.openrouter.models.length} modelos (${[...new Set(tts.openrouter.models.map((item) => VENDOR_LABELS[item.vendor] ?? item.vendor))].slice(0, 5).join(', ')}…), cada um com suas vozes.`
        : 'Configure uma chave OpenRouter em Configurações → Provedores.',
      disabled: !tts.openrouter.available,
      badge: tts.openrouter.available ? { text: 'Pronto', tone: 'ok' } : { text: 'Sem chave', tone: 'muted' },
    },
    {
      value: 'openai',
      title: 'OpenAI (nuvem)',
      description: tts.openai.available ? 'Muito expressiva; usa a sua chave OpenAI.' : 'Configure uma chave OpenAI em Configurações → Provedores.',
      disabled: !tts.openai.available,
      badge: tts.openai.available ? { text: 'Pronto', tone: 'ok' } : { text: 'Sem chave', tone: 'muted' },
    },
  ]

  const modelVoices = selection && effectiveTts !== 'system'
    ? sortVoicesForPortuguese(selection.model?.voices.length ? selection.model.voices : tts[effectiveTts].voices)
    : []
  const clips = tts.chatterbox.voiceClips ?? [{ id: 'default', name: 'Voz padrão do Chatterbox' }]

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
            <input id="live-quick-sensitivity" type="range" min={0} max={1} step={0.05} value={prefs.sensitivity} onChange={(event) => update({ sensitivity: Number(event.target.value) })} />
            <span className="voice-field__value">{Math.round(prefs.sensitivity * 100)}%</span>
          </div>
          <div className="voice-field is-inline">
            <label htmlFor="live-quick-silence">Pausa que encerra a frase</label>
            <input id="live-quick-silence" type="range" min={400} max={2000} step={100} value={prefs.endSilenceMs} onChange={(event) => update({ endSilenceMs: Number(event.target.value) })} />
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
          {ttsChoices.map((choice) => (
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
            >
              {choice.extra}
            </OptionCard>
          ))}

          <div className="voice-settings__subpanel" data-testid="live-quick-voice-picker">
            <p className="voice-settings__hint">Falando com: <strong>{TTS_LABELS[effectiveTts]}</strong></p>
            {effectiveTts === 'system' && (
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
            )}

            {selection && effectiveTts !== 'system' && tts[effectiveTts].models.length > 1 && (
              <div className="voice-field">
                <label htmlFor="live-quick-tts-model">Modelo</label>
                <ModelSelect
                  id="live-quick-tts-model"
                  testId="live-quick-tts-model-select"
                  models={tts[effectiveTts].models}
                  value={selection.modelId}
                  onChange={(modelId) => chooseTtsModel(effectiveTts, modelId)}
                />
              </div>
            )}

            {selection && effectiveTts !== 'system' && effectiveTts !== 'chatterbox' && (
              <div className="voice-field">
                <label htmlFor="live-quick-tts-voice">Voz{modelVoices.length ? ` (${modelVoices.length} neste modelo)` : ''}</label>
                {modelVoices.length > 0 ? (
                  <select
                    id="live-quick-tts-voice"
                    data-testid="live-quick-tts-select"
                    value={selection.voice}
                    onChange={(event) => chooseTtsVoice(effectiveTts, selection.modelId, event.target.value)}
                  >
                    {modelVoices.map((voice) => (
                      <option key={voice} value={voice}>{voiceLabel(effectiveTts, voice)}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    id="live-quick-tts-voice"
                    data-testid="live-quick-tts-select"
                    value={selection.voice}
                    placeholder="Este modelo não lista vozes; deixe vazio para a padrão"
                    onChange={(event) => chooseTtsVoice(effectiveTts, selection.modelId, event.target.value.trim())}
                  />
                )}
              </div>
            )}

            {selection && effectiveTts === 'chatterbox' && (
              <div className="voice-field">
                <label htmlFor="live-quick-tts-voice">Voz</label>
                <div className="voice-settings__row">
                  <select
                    id="live-quick-tts-voice"
                    data-testid="live-quick-tts-select"
                    value={selection.voice}
                    onChange={(event) => chooseTtsVoice('chatterbox', selection.modelId, event.target.value)}
                  >
                    {clips.map((clip) => <option key={clip.id} value={clip.id}>{clip.name}</option>)}
                  </select>
                  {selection.voice !== 'default' && (
                    <button type="button" className="voice-button is-quiet is-small" onClick={() => void handleClipDelete(selection.voice)}>
                      Remover
                    </button>
                  )}
                </div>
                <div className="voice-settings__row">
                  <button type="button" className="voice-button" data-testid="live-chatterbox-upload-button" onClick={() => clipInputRef.current?.click()}>
                    Clonar uma voz (enviar áudio)
                  </button>
                  <input
                    ref={clipInputRef}
                    type="file"
                    accept=".wav,.mp3,.flac,.ogg,.m4a,audio/*"
                    hidden
                    data-testid="live-chatterbox-upload-input"
                    onChange={(event) => {
                      const file = event.target.files?.[0]
                      if (file) void handleClipUpload(file)
                      event.target.value = ''
                    }}
                  />
                </div>
                <p className="voice-settings__hint" role="status">
                  {clipState ?? 'Um clipe de 5 a 15 segundos de fala limpa, sem música, já basta.'}
                </p>
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

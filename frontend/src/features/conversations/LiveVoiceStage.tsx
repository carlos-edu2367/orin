import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useReducedMotion } from 'motion/react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { getLiveVoiceCapabilities, type LiveVoiceCapabilities } from '../../api/liveVoice'
import type { LiveVoicePhase } from './activityTypes'
import { LiveVoiceQuickSettings } from './LiveVoiceQuickSettings'
import { LevelMeter } from './live/LevelMeter'
import { LiveOrb } from './live/LiveOrb'
import { loadLiveVoicePrefs, onLiveVoicePrefsChange, STT_LABELS, type LiveVoicePrefs } from './live/liveVoicePrefs'
import { orbTheme } from './live/orbThemes'
import { useLiveVoice, type AssistantSpeechItem } from './live/useLiveVoice'

export {
  LIVE_VOICE_DUCKING_MIN_SPEECH_MS,
  LIVE_VOICE_DUCKING_MULTIPLIER,
  LIVE_VOICE_DUCKING_TAIL_MS,
  LIVE_VOICE_ECHO_SIMILARITY_THRESHOLD,
  LONG_FORM_MIC_CONSTRAINTS,
  computeEchoSimilarity,
  isEchoOfRecentTts,
  shouldTriggerDuckingBargeIn,
  type DuckingEvaluationInput,
  type LongFormMicConstraints,
} from './live/echo'

export type LiveVoiceStageProps = {
  isOpen: boolean
  conversationId?: string
  client?: ApiClient
  companionName?: string
  userNickname?: string
  orbThemeId?: string
  assistantSpeech?: ReadonlyArray<AssistantSpeechItem>
  assistantBusy?: boolean
  workingStatusText?: string | null
  /** Pins the displayed phase (visual tests and previews). */
  phaseOverride?: LiveVoicePhase
  reducedMotion?: boolean
  onUserSpeechUtterance?: (text: string) => void
  onOpenPersonalize?: () => void
  onClose: () => void
}

export function stateLabel(phase: LiveVoicePhase, name = 'Orin', working?: string | null): string {
  switch (phase) {
    case 'connecting':
      return 'Ligando o microfone…'
    case 'listening':
      return 'Estou ouvindo'
    case 'hearing':
      return 'Ouvindo você…'
    case 'transcribing':
      return 'Entendendo o que você disse…'
    case 'thinking':
      return 'Pensando…'
    case 'working':
      return working || 'Trabalhando nisso…'
    case 'speaking':
      return `${name} está falando`
    case 'interrupted':
      return 'Parei — pode falar!'
    case 'muted':
      return 'Microfone desligado'
    case 'error':
      return 'Não consegui usar o microfone'
  }
}

function Caption({ text, charIndex }: { text: string; charIndex: number }) {
  const words = useMemo(() => [...text.matchAll(/\S+/g)].map((match) => ({ word: match[0], start: match.index ?? 0 })), [text])
  let active = -1
  for (let i = 0; i < words.length; i += 1) if (words[i].start <= charIndex) active = i
  return (
    <p className="live-stage__caption-text" data-testid="live-voice-assistant-caption">
      {words.map((item, index) => (
        <span
          key={`${item.start}-${item.word}`}
          className={index === active ? 'is-active' : index < active ? 'is-said' : undefined}
          data-testid={index === active ? 'live-caption-active-word' : undefined}
        >
          {item.word}{' '}
        </span>
      ))}
    </p>
  )
}

function Icon({ name }: { name: 'mic' | 'mic-off' | 'gear' | 'close' | 'hand' | 'ptt' | 'user' }) {
  const common = { width: 20, height: 20, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true }
  switch (name) {
    case 'mic':
      return <svg {...common}><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
    case 'mic-off':
      return <svg {...common}><path d="M15 9.5V6a3 3 0 0 0-5.7-1.3M9 9v2a3 3 0 0 0 4.6 2.5M5 11a7 7 0 0 0 11.2 5.6M19 11a7 7 0 0 1-.6 2.8M12 18v3M3 3l18 18" /></svg>
    case 'gear':
      return <svg {...common}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></svg>
    case 'close':
      return <svg {...common}><path d="M6 6l12 12M18 6L6 18" /></svg>
    case 'hand':
      return <svg {...common}><path d="M8 13V5.5a1.5 1.5 0 0 1 3 0V12M11 11.5v-7a1.5 1.5 0 0 1 3 0V12M14 11.5V6.5a1.5 1.5 0 0 1 3 0V15a6 6 0 0 1-6 6h-1a6 6 0 0 1-5-2.7L3.5 15a1.5 1.5 0 0 1 2.4-1.8L8 15" /></svg>
    case 'ptt':
      return <svg {...common}><rect x="4" y="9" width="16" height="10" rx="3" /><path d="M9 9V6a3 3 0 0 1 6 0v3" /></svg>
    case 'user':
      return <svg {...common}><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>
  }
}

export function LiveVoiceStage({
  isOpen,
  conversationId,
  client: providedClient,
  companionName = 'Orin',
  userNickname = '',
  orbThemeId = 'aurora',
  assistantSpeech = [],
  assistantBusy = false,
  workingStatusText = null,
  phaseOverride,
  reducedMotion: forcedReducedMotion,
  onUserSpeechUtterance,
  onOpenPersonalize,
  onClose,
}: LiveVoiceStageProps) {
  const systemReducedMotion = useReducedMotion()
  const isReducedMotion = Boolean(forcedReducedMotion ?? systemReducedMotion)
  const client = useMemo(() => providedClient ?? createBrowserApiClient(), [providedClient])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [prefs, setPrefs] = useState<LiveVoicePrefs>(() => loadLiveVoicePrefs())
  const [capabilities, setCapabilities] = useState<LiveVoiceCapabilities | null>(null)
  const [pushToTalk, setPushToTalk] = useState(false)
  const utteranceRef = useRef(onUserSpeechUtterance)
  useEffect(() => { utteranceRef.current = onUserSpeechUtterance }, [onUserSpeechUtterance])

  useEffect(() => onLiveVoicePrefsChange(setPrefs), [])

  const refreshCapabilities = useCallback(() => {
    const controller = new AbortController()
    getLiveVoiceCapabilities(client, controller.signal).then(setCapabilities).catch(() => {})
    return () => controller.abort()
  }, [client])
  useEffect(() => {
    if (!isOpen) return
    return refreshCapabilities()
  }, [isOpen, refreshCapabilities])

  const voice = useLiveVoice({
    active: isOpen && !phaseOverride,
    conversationId,
    client,
    prefs,
    capabilities,
    assistantSpeech,
    assistantBusy,
    pushToTalk,
    onUtterance: (text) => utteranceRef.current?.(text),
    voiceInstructions: `Você é ${companionName}. Fale em português do Brasil, de forma natural, calorosa e conversacional.`,
  })
  const phase = phaseOverride ?? voice.phase
  const { setMuted, muted, interrupt, setPushToTalkHeld } = voice

  // The orb reacts to whoever is talking.
  const micLevel = voice.micLevel
  const outputLevel = voice.outputLevel
  const phaseRef = useRef(phase)
  useEffect(() => { phaseRef.current = phase }, [phase])
  const micLevelRef = useRef(micLevel)
  const outputLevelRef = useRef(outputLevel)
  useEffect(() => {
    micLevelRef.current = micLevel
    outputLevelRef.current = outputLevel
  })
  const orbLevel = useCallback(
    () => (phaseRef.current === 'speaking' ? outputLevelRef.current() : micLevelRef.current() * 0.9),
    [],
  )
  const meterLevel = useCallback(() => micLevelRef.current(), [])

  useEffect(() => {
    if (!isOpen) return
    const typing = (target: EventTarget | null) =>
      target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
    function handleKeyDown(event: KeyboardEvent) {
      if (settingsOpen || typing(event.target)) return
      if (event.key === 'Escape') onClose()
      else if (event.code === 'Space' && pushToTalk) {
        event.preventDefault()
        if (!event.repeat) setPushToTalkHeld(true)
      } else if (event.key.toLowerCase() === 'm' && !event.ctrlKey && !event.metaKey && !event.altKey) {
        setMuted(!muted)
      }
    }
    function handleKeyUp(event: KeyboardEvent) {
      if (event.code === 'Space' && pushToTalk) setPushToTalkHeld(false)
    }
    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('keyup', handleKeyUp)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('keyup', handleKeyUp)
    }
  }, [isOpen, onClose, settingsOpen, pushToTalk, muted, setMuted, setPushToTalkHeld])

  if (!isOpen) return null

  const palette = orbTheme(orbThemeId)
  const stageStyle = {
    '--live-top': palette.stageTop,
    '--live-bottom': palette.stageBottom,
    '--live-glow': palette.glow,
    '--live-accent': palette.core[1],
  } as CSSProperties
  const greeting = userNickname ? `Oi, ${userNickname}!` : 'Oi!'
  const speakingNow = phase === 'speaking'
  const sttLabel = voice.sttEngine ? STT_LABELS[voice.sttEngine] : 'Sem transcrição'
  const ttsLabel = voice.ttsEngine === 'openai' ? 'Voz OpenAI' : 'Voz do sistema'
  const issueNeedsSetup = voice.issue
    && ['no_stt', 'local_stt_unavailable', 'cloud_stt_no_key', 'browser_network', 'cloud_tts_no_key'].includes(voice.issue.code)

  return (
    <div
      className="live-stage"
      data-testid="live-voice-stage"
      data-voice-state={phase}
      data-reduced-motion={isReducedMotion ? 'true' : 'false'}
      role="dialog"
      aria-modal="true"
      aria-label={`Conversa por voz com ${companionName}`}
      style={stageStyle}
    >
      <div className="live-stage__backdrop" aria-hidden="true" />
      <header className="live-stage__top">
        <div className="live-stage__identity">
          <span className="live-stage__live-dot" aria-hidden="true" />
          <strong data-testid="live-voice-companion-name">{companionName}</strong>
          <span className="live-stage__chip">Ao vivo</span>
        </div>
        <button
          type="button"
          className="live-stage__engines"
          data-testid="live-voice-active-profile-pill"
          onClick={() => setSettingsOpen(true)}
          title="Trocar como ele te ouve e qual voz usa"
        >
          <span>Ouve: {sttLabel}</span>
          <span aria-hidden="true">·</span>
          <span>Fala: {ttsLabel}</span>
        </button>
        <div className="live-stage__top-actions">
          {onOpenPersonalize && (
            <button type="button" className="live-icon-button" onClick={onOpenPersonalize} aria-label={`Personalizar ${companionName}`} title={`Personalizar ${companionName}`}>
              <Icon name="user" />
            </button>
          )}
          <button
            type="button"
            className="live-icon-button"
            data-testid="live-voice-open-settings-button"
            onClick={() => setSettingsOpen(true)}
            aria-label="Configurar voz e microfone"
            title="Configurar voz e microfone"
          >
            <Icon name="gear" />
          </button>
          <button
            type="button"
            className="live-icon-button"
            data-testid="live-voice-close-button"
            onClick={onClose}
            aria-label="Encerrar conversa por voz (Esc)"
            title="Encerrar (Esc)"
          >
            <Icon name="close" />
          </button>
        </div>
      </header>

      <main className="live-stage__center">
        <div className="live-stage__orb" data-testid="live-voice-orb">
          <LiveOrb phase={phase} theme={orbThemeId} level={orbLevel} staticMode={isReducedMotion} />
        </div>

        <p className="live-stage__status" data-testid="live-voice-state-label" aria-live="polite">
          {stateLabel(phase, companionName, workingStatusText)}
        </p>

        <div className="live-stage__caption" data-testid="live-voice-captions">
          {speakingNow && voice.assistantText ? (
            <Caption text={voice.assistantText} charIndex={voice.assistantCharIndex} />
          ) : voice.userText ? (
            <p className="live-stage__caption-text is-user" data-testid="live-voice-user-caption">“{voice.userText}”</p>
          ) : (
            <p className="live-stage__caption-text is-hint">
              {greeting} Pode falar naturalmente — você pode me interromper quando quiser.
            </p>
          )}
        </div>

        {voice.issue && (
          <div className="live-stage__issue" role="alert" data-testid="live-voice-issue">
            <span>{voice.issue.message}</span>
            <div className="live-stage__issue-actions">
              {issueNeedsSetup && (
                <button type="button" className="live-pill-button" onClick={() => setSettingsOpen(true)}>
                  Resolver na configuração
                </button>
              )}
              <button type="button" className="live-pill-button is-quiet" onClick={voice.dismissIssue}>
                Dispensar
              </button>
            </div>
          </div>
        )}
      </main>

      <footer className="live-stage__dock">
        <div className="live-stage__meter">
          <LevelMeter level={meterLevel} disabled={muted || phase === 'error'} />
        </div>
        <div className="live-stage__controls">
          <button
            type="button"
            className={`live-round-button${muted ? ' is-off' : ''}`}
            data-testid="live-voice-mute-button"
            aria-pressed={muted}
            onClick={() => setMuted(!muted)}
            aria-label={muted ? 'Ligar microfone (M)' : 'Desligar microfone (M)'}
            title={muted ? 'Ligar microfone (M)' : 'Desligar microfone (M)'}
          >
            <Icon name={muted ? 'mic-off' : 'mic'} />
          </button>
          <button
            type="button"
            className="live-round-button is-wide"
            data-testid="live-voice-barge-in-button"
            onClick={interrupt}
            disabled={!speakingNow}
            title="Interromper a fala"
          >
            <Icon name="hand" />
            <span>Interromper</span>
          </button>
          <button
            type="button"
            className={`live-round-button is-wide${pushToTalk ? ' is-on' : ''}`}
            data-testid="live-voice-ptt-button"
            aria-pressed={pushToTalk}
            onClick={() => setPushToTalk((value) => !value)}
            onPointerDown={() => pushToTalk && setPushToTalkHeld(true)}
            onPointerUp={() => pushToTalk && setPushToTalkHeld(false)}
            onPointerLeave={() => pushToTalk && setPushToTalkHeld(false)}
            title="Sem fone? Segure para falar (Espaço) e o microfone só abre enquanto você segura."
          >
            <Icon name="ptt" />
            <span>{pushToTalk ? 'Segure para falar' : 'Modo sem fone'}</span>
          </button>
          <button type="button" className="live-round-button is-end" onClick={onClose} aria-label="Encerrar conversa por voz" title="Encerrar">
            <Icon name="close" />
          </button>
        </div>
      </footer>

      <LiveVoiceQuickSettings
        isOpen={settingsOpen}
        client={client}
        companionName={companionName}
        onClose={() => {
          setSettingsOpen(false)
          refreshCapabilities()
        }}
      />
    </div>
  )
}

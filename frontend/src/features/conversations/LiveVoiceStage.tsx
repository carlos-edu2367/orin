import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useReducedMotion } from 'motion/react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { getLiveVoiceCapabilities, type LiveVoiceCapabilities } from '../../api/liveVoice'
import { OrinMascot } from '../../components/OrinMascot'
import type { DelegatedCodeSessionInfo, LiveVoicePhase, TodoItem } from './activityTypes'
import { LiveVoiceQuickSettings } from './LiveVoiceQuickSettings'
import { LevelMeter } from './live/LevelMeter'
import { LiveOrb } from './live/LiveOrb'
import { loadLiveVoicePrefs, onLiveVoicePrefsChange, STT_LABELS, TTS_LABELS, voiceLabel, type LiveVoicePrefs } from './live/liveVoicePrefs'
import { orbTheme } from './live/orbThemes'
import { useLiveVoice, type AssistantSpeechItem } from './live/useLiveVoice'
import { setLiveVoiceStageOpen } from './live/wakeWord'

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

export type LiveVoiceTranscriptItem = {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp?: string
}

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
  transcriptItems?: ReadonlyArray<LiveVoiceTranscriptItem>
  todos?: ReadonlyArray<TodoItem>
  delegatedCodeSession?: DelegatedCodeSessionInfo | null
  onOpenCodeSession?: (codeConversationId?: string) => void
}

function sanitizeNickname(userNickname?: string): string {
  return (userNickname ?? '')
    .replace(/[<>{}`]/g, '')
    .trim()
    .slice(0, 40)
    .replace(/[.!?]+$/, '')
    .trim()
}

export function stateLabel(
  phase: LiveVoicePhase,
  name = 'Orin',
  working?: string | null,
  userNickname?: string,
): string {
  switch (phase) {
    case 'connecting':
      return 'Ligando o microfone…'
    case 'listening': {
      const cleanNick = sanitizeNickname(userNickname)
      return cleanNick ? `Estou ouvindo, ${cleanNick}.` : 'Estou ouvindo'
    }
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

export function sanitizeTranscriptText(raw: string): string {
  if (!raw) return ''
  const cleaned = raw
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/<\/?speak\b[^>]*>/gi, ' ')
    .replace(/<\/?[a-z][^>]*>/gi, ' ')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/(\*\*|__|\*|_|~~|^#{1,6}\s+)/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
  if (cleaned.length <= 180) return cleaned
  return `${cleaned.slice(0, 180).trimEnd()}…`
}

function formatTranscriptTimestamp(ts?: string): string {
  if (!ts) return '14:26'
  const trimmed = ts.trim()
  if (/^\d{1,2}:\d{2}$/.test(trimmed) || trimmed === 'Agora') return trimmed
  const parsed = new Date(trimmed)
  if (!Number.isNaN(parsed.getTime())) {
    return parsed.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
  }
  return trimmed
}

const WAVEFORM_BAR_COUNT = 31
const WAVEFORM_CENTER_INDEX = 15
const WAVEFORM_BASE_HEIGHTS: readonly number[] = Array.from({ length: WAVEFORM_BAR_COUNT }, (_, i) => {
  const dist = Math.abs(i - WAVEFORM_CENTER_INDEX)
  const norm = dist / WAVEFORM_CENTER_INDEX
  const envelope = Math.exp(-(norm * norm) * 2.8)
  const harmonic = 0.58 + 0.42 * Math.cos(dist * 1.45)
  return Math.max(4, Math.round(4 + 34 * envelope * harmonic))
})

const DEMO_TODOS: ReadonlyArray<TodoItem> = [
  { id: 'demo-todo-1', content: 'Estrutura da página', status: 'completed' },
  { id: 'demo-todo-2', content: 'Seções principais', status: 'completed' },
  { id: 'demo-todo-3', content: 'Revisão de conteúdo', status: 'completed' },
  { id: 'demo-todo-4', content: 'Publicação', status: 'pending' },
]

type AudioOutputOption = {
  deviceId: string
  label: string
}

const DEFAULT_SPEAKER_OPTION: AudioOutputOption = {
  deviceId: 'default',
  label: 'Alto-falantes (Padrão)',
}

function LiveWaveform({
  level,
  staticMode,
}: {
  level: () => number
  staticMode: boolean
}) {
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (staticMode) return
    let rafId = 0
    const tick = () => {
      const current = Math.max(0, Math.min(1, level()))
      containerRef.current?.style.setProperty('--waveform-level', current.toFixed(3))
      rafId = window.requestAnimationFrame(tick)
    }
    rafId = window.requestAnimationFrame(tick)
    return () => window.cancelAnimationFrame(rafId)
  }, [level, staticMode])

  return (
    <div
      ref={containerRef}
      className="live-waveform"
      data-testid="live-voice-waveform"
      data-reduced-motion={staticMode ? 'true' : 'false'}
      aria-hidden="true"
    >
      {WAVEFORM_BASE_HEIGHTS.map((heightPx, index) => (
        <span
          key={index}
          className="live-waveform__bar"
          data-bar-index={index}
          style={
            {
              height: `${heightPx}px`,
              '--bar-base-height': `${heightPx}px`,
            } as CSSProperties
          }
        />
      ))}
    </div>
  )
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

type IconName =
  | 'mic'
  | 'mic-off'
  | 'gear'
  | 'close'
  | 'hand'
  | 'ptt'
  | 'user'
  | 'captions'
  | 'speaker'
  | 'phone-off'
  | 'external'
  | 'doc'
  | 'chevron-right'

function Icon({ name }: { name: IconName }) {
  const common = {
    width: 20,
    height: 20,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.8,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true,
  }
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
    case 'captions':
      return <svg {...common}><rect x="3" y="5" width="18" height="14" rx="3" /><path d="M7 10h4M13 10h4M7 14h6M15 14h2" /></svg>
    case 'speaker':
      return <svg {...common}><path d="M11 5L6 9H3v6h3l5 4V5z" /><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" /></svg>
    case 'phone-off':
      return <svg {...common}><path d="M10.7 13.3a14 14 0 0 1 2.6 0l2 2a1.6 1.6 0 0 0 2 .2l2.4-1.4a1.6 1.6 0 0 0 .6-2.1C18.5 8.8 15.4 7 12 7s-6.5 1.8-8.3 5a1.6 1.6 0 0 0 .6 2.1l2.4 1.4a1.6 1.6 0 0 0 2-.2l2-2z" /></svg>
    case 'external':
      return <svg {...common}><path d="M14 4h6v6M20 4l-9 9M10 6H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4" /></svg>
    case 'doc':
      return <svg {...common}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M9 13h6M9 17h4" /></svg>
    case 'chevron-right':
      return <svg {...common}><path d="M9 6l6 6-6 6" /></svg>
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
  transcriptItems,
  todos,
  delegatedCodeSession,
  onOpenCodeSession,
}: LiveVoiceStageProps) {
  const systemReducedMotion = useReducedMotion()
  const isReducedMotion = Boolean(forcedReducedMotion ?? systemReducedMotion)
  const client = useMemo(() => providedClient ?? createBrowserApiClient(), [providedClient])
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [prefs, setPrefs] = useState<LiveVoicePrefs>(() => loadLiveVoicePrefs())
  const [capabilities, setCapabilities] = useState<LiveVoiceCapabilities | null>(null)
  const [pushToTalk, setPushToTalk] = useState(false)
  const [captionsEnabled, setCaptionsEnabled] = useState(true)
  const [speakerOptions, setSpeakerOptions] = useState<AudioOutputOption[]>([DEFAULT_SPEAKER_OPTION])
  const [selectedSpeakerId, setSelectedSpeakerId] = useState<string>(DEFAULT_SPEAKER_OPTION.deviceId)
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
    setLiveVoiceStageOpen(true)
    return () => setLiveVoiceStageOpen(false)
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) return
    return refreshCapabilities()
  }, [isOpen, refreshCapabilities])

  useEffect(() => {
    if (!isOpen) return
    const mediaDevices = typeof navigator !== 'undefined' ? navigator.mediaDevices : undefined
    if (!mediaDevices || typeof mediaDevices.enumerateDevices !== 'function') {
      setSpeakerOptions([DEFAULT_SPEAKER_OPTION])
      setSelectedSpeakerId(DEFAULT_SPEAKER_OPTION.deviceId)
      return
    }

    let cancelled = false
    const syncDevices = () => {
      Promise.resolve(mediaDevices.enumerateDevices())
        .then((devices) => {
          if (cancelled || !Array.isArray(devices)) return
          const outputs: AudioOutputOption[] = []
          const seen = new Set<string>()
          for (const device of devices) {
            if (!device || device.kind !== 'audiooutput') continue
            const label = (device.label ?? '').trim()
            const id = (device.deviceId ?? '').trim()
            if (!label || !id || seen.has(id)) continue
            seen.add(id)
            outputs.push({ deviceId: id, label })
          }
          if (outputs.length === 0) {
            setSpeakerOptions([DEFAULT_SPEAKER_OPTION])
            setSelectedSpeakerId(DEFAULT_SPEAKER_OPTION.deviceId)
            return
          }
          const nextOptions = seen.has('default') ? outputs : [DEFAULT_SPEAKER_OPTION, ...outputs]
          setSpeakerOptions(nextOptions)
          setSelectedSpeakerId((prev) =>
            nextOptions.some((opt) => opt.deviceId === prev) ? prev : outputs[0].deviceId,
          )
        })
        .catch(() => {
          if (cancelled) return
          setSpeakerOptions([DEFAULT_SPEAKER_OPTION])
          setSelectedSpeakerId(DEFAULT_SPEAKER_OPTION.deviceId)
        })
    }

    syncDevices()
    mediaDevices.addEventListener?.('devicechange', syncDevices)
    return () => {
      cancelled = true
      mediaDevices.removeEventListener?.('devicechange', syncDevices)
    }
  }, [isOpen])

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
    voiceInstructions: `Você é ${companionName}. Fale SEMPRE estritamente em português do Brasil (pt-BR) nativo, de forma natural, calorosa, concisa e conversacional. Nunca use sotaque de Portugal (pt-PT), espanhol (es) ou italiano (it).`,
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
    () =>
      phaseRef.current === 'speaking' ||
      phaseRef.current === 'thinking' ||
      phaseRef.current === 'working' ||
      phaseRef.current === 'transcribing'
        ? Math.max(outputLevelRef.current(), micLevelRef.current() * 0.4)
        : micLevelRef.current() * 0.9,
    [],
  )
  const meterLevel = useCallback(() => micLevelRef.current(), [])

  const displayTranscriptItems = useMemo(() => {
    const provided = (transcriptItems ?? [])
      .map((item) => ({
        ...item,
        content: sanitizeTranscriptText(item.content),
        timestamp: formatTranscriptTimestamp(item.timestamp),
      }))
      .filter((item) => item.content.length > 0)

    const base: LiveVoiceTranscriptItem[] =
      provided.length > 0
        ? provided.slice(-6)
        : [
            {
              id: 'demo-user-1',
              role: 'user',
              content: 'Como está a página da Auryly?',
              timestamp: '14:26',
            },
            {
              id: 'demo-assistant-1',
              role: 'assistant',
              content: 'A prévia já está pronta. Quer dar uma olhada?',
              timestamp: '14:26',
            },
          ]

    const liveUser = sanitizeTranscriptText(voice.userText)
    if (liveUser && base[base.length - 1]?.content !== liveUser) {
      base.push({
        id: 'live-user-turn',
        role: 'user',
        content: liveUser,
        timestamp: 'Agora',
      })
    }

    const liveAssistant = sanitizeTranscriptText(voice.assistantText)
    if (liveAssistant && base[base.length - 1]?.content !== liveAssistant) {
      base.push({
        id: 'live-assistant-turn',
        role: 'assistant',
        content: liveAssistant,
        timestamp: 'Agora',
      })
    }

    return base.slice(-6)
  }, [transcriptItems, voice.userText, voice.assistantText])

  const effectiveTodos = useMemo<ReadonlyArray<TodoItem>>(() => {
    if (todos && todos.length > 0) return todos
    if (todos && todos.length === 0 && delegatedCodeSession?.total_todo_count === 0) return []
    return DEMO_TODOS
  }, [todos, delegatedCodeSession?.total_todo_count])

  const taskTitle = useMemo(() => {
    const goal = delegatedCodeSession?.goal?.trim()
    if (goal) return goal
    const activeTitle = delegatedCodeSession?.active_task_title?.trim()
    if (activeTitle) return activeTitle
    return 'Página da Auryly'
  }, [delegatedCodeSession?.goal, delegatedCodeSession?.active_task_title])

  const { completedCount, totalCount, progressPercent } = useMemo(() => {
    const rawTotal =
      typeof delegatedCodeSession?.total_todo_count === 'number'
        ? Math.max(0, delegatedCodeSession.total_todo_count)
        : effectiveTodos.length
    const rawCompleted =
      typeof delegatedCodeSession?.completed_todo_count === 'number'
        ? Math.max(0, delegatedCodeSession.completed_todo_count)
        : effectiveTodos.filter((item) => item.status === 'completed').length
    const boundedCompleted = rawTotal > 0 ? Math.min(rawCompleted, rawTotal) : 0
    const percent =
      rawTotal > 0 ? Math.min(100, Math.max(0, Math.round((boundedCompleted / rawTotal) * 100))) : 0
    return {
      completedCount: boundedCompleted,
      totalCount: rawTotal,
      progressPercent: percent,
    }
  }, [
    delegatedCodeSession?.completed_todo_count,
    delegatedCodeSession?.total_todo_count,
    effectiveTodos,
  ])

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
  const cleanNick = sanitizeNickname(userNickname)
  const userInitial = (cleanNick[0] || 'C').toUpperCase()
  const greeting = cleanNick ? `Oi, ${cleanNick}!` : 'Oi!'
  const speakingNow = phase === 'speaking'
  const isMicMuted = muted || phase === 'muted'
  const sttLabel = voice.sttEngine ? STT_LABELS[voice.sttEngine] : 'Sem transcrição'
  const ttsLabel = voice.ttsEngine === 'system' || !voice.ttsVoice
    ? TTS_LABELS[voice.ttsEngine]
    : `${TTS_LABELS[voice.ttsEngine]} · ${voiceLabel(voice.ttsEngine, voice.ttsVoice).split(' · ')[0]}`
  const issueNeedsSetup = voice.issue
    && ['no_stt', 'local_stt_unavailable', 'local_stt_failed', 'local_tts_unavailable', 'local_tts_failed', 'cloud_stt_no_key', 'browser_network', 'cloud_tts_no_key'].includes(voice.issue.code)

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
        <div className="live-stage__identity live-stage__breadcrumb" data-testid="live-voice-breadcrumb">
          <span className="live-stage__breadcrumb-root">Friend</span>
          <span className="live-stage__breadcrumb-sep" aria-hidden="true">/</span>
          <strong className="live-stage__companion-name" data-testid="live-voice-companion-name">{companionName}</strong>
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
          <div
            className={`live-stage__mic-pill${isMicMuted ? ' is-muted' : ''}`}
            data-testid="live-voice-mic-status-pill"
          >
            <span className="live-stage__live-dot" aria-hidden="true" />
            <Icon name={isMicMuted ? 'mic-off' : 'mic'} />
            <span>{isMicMuted ? 'Microfone silenciado' : 'Microfone ativo'}</span>
          </div>
          {onOpenPersonalize && (
            <button
              type="button"
              className="live-icon-button"
              onClick={onOpenPersonalize}
              aria-label={`Personalizar ${companionName}`}
              title={`Personalizar ${companionName}`}
            >
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
          <OrinMascot size={30} variant="flat" statusDot="online" ariaHidden />
        </div>
      </header>

      <div className="live-stage__body">
        <div className="live-stage__main-col">
          <main className="live-stage__center">
            <div className="live-stage__orb" data-testid="live-voice-orb">
              <div className="live-stage__mascot-hero">
                <OrinMascot size={148} variant="soft3d" animated={!isReducedMotion} ariaHidden />
              </div>
              <LiveOrb phase={phase} theme={orbThemeId} level={orbLevel} staticMode={isReducedMotion} />
            </div>

            <p className="live-stage__status" data-testid="live-voice-state-label" aria-live="polite">
              {stateLabel(phase, companionName, workingStatusText, userNickname)}
            </p>

            <LiveWaveform level={orbLevel} staticMode={isReducedMotion} />

            <div
              className="live-stage__caption"
              data-testid="live-voice-captions"
              data-captions-enabled={captionsEnabled ? 'true' : 'false'}
            >
              {captionsEnabled && speakingNow && voice.assistantText ? (
                <Caption text={voice.assistantText} charIndex={voice.assistantCharIndex} />
              ) : captionsEnabled && voice.userText ? (
                <p className="live-stage__caption-text is-user" data-testid="live-voice-user-caption">“{voice.userText}”</p>
              ) : (
                <p className="live-stage__caption-text is-hint">
                  <span className="visually-hidden">{greeting} </span>
                  Pode falar naturalmente e me interromper.
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
            <div className="live-stage__meter visually-hidden">
              <LevelMeter level={meterLevel} disabled={muted || phase === 'error'} />
            </div>
            <div className="live-stage__controls">
              <button
                type="button"
                className={`live-round-button live-dock-control${muted ? ' is-off' : ''}`}
                data-testid="live-voice-mute-button"
                aria-pressed={muted}
                onClick={() => setMuted(!muted)}
                aria-label={muted ? 'Ligar microfone (M)' : 'Desligar microfone (M)'}
                title={muted ? 'Ligar microfone (M)' : 'Desligar microfone (M)'}
              >
                <span className="live-dock-control__circle">
                  <Icon name={muted ? 'mic-off' : 'mic'} />
                </span>
                <span className="live-dock-control__label">
                  {muted ? 'Ativar microfone' : 'Silenciar microfone'}
                </span>
              </button>

              <button
                type="button"
                className={`live-round-button live-dock-control${captionsEnabled ? ' is-on' : ''}`}
                data-testid="live-voice-captions-toggle"
                aria-pressed={captionsEnabled}
                onClick={() => setCaptionsEnabled((value) => !value)}
                aria-label={captionsEnabled ? 'Desativar legendas ao vivo' : 'Ativar legendas ao vivo'}
                title="Legendas ao vivo"
              >
                <span className="live-dock-control__circle">
                  <Icon name="captions" />
                </span>
                <span className="live-dock-control__label">Legendas ao vivo</span>
              </button>

              <div className="live-speaker-picker-wrap">
                <Icon name="speaker" />
                <select
                  className="live-speaker-picker"
                  data-testid="live-voice-speaker-picker"
                  aria-label="Saída de áudio"
                  value={selectedSpeakerId}
                  onChange={(event) => setSelectedSpeakerId(event.target.value)}
                >
                  {speakerOptions.map((option) => (
                    <option key={option.deviceId} value={option.deviceId}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>

              <button
                type="button"
                className="live-round-button live-dock-control is-end"
                data-testid="live-voice-end-call-button"
                onClick={onClose}
                aria-label="Encerrar conversa por voz"
                title="Encerrar"
              >
                <span className="live-dock-control__circle">
                  <Icon name="phone-off" />
                </span>
                <span className="live-dock-control__label">Encerrar</span>
              </button>

              <button
                type="button"
                className="live-round-button is-wide live-dock-aux visually-hidden"
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
                className={`live-round-button is-wide live-dock-aux visually-hidden${pushToTalk ? ' is-on' : ''}`}
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
            </div>
          </footer>
        </div>

        <aside
          className="live-stage__side-panel"
          data-testid="live-voice-side-panel"
          aria-label="Nesta conversa"
        >
          <section className="live-side-section" data-testid="live-voice-transcript-list">
            <h3 className="live-side-section__title">Nesta conversa</h3>
            <ul className="live-transcript-list">
              {displayTranscriptItems.map((item) => {
                const isUser = item.role === 'user'
                return (
                  <li
                    key={item.id}
                    className={`live-transcript-item is-${item.role}`}
                  >
                    <div className="live-transcript-item__avatar" aria-hidden="true">
                      {isUser ? (
                        <span className="live-transcript-item__initial">{userInitial}</span>
                      ) : (
                        <OrinMascot size={24} variant="mini" ariaHidden />
                      )}
                    </div>
                    <div className="live-transcript-item__body">
                      <div className="live-transcript-item__meta">
                        <strong>{isUser ? 'Você' : companionName}</strong>
                        {item.timestamp && <span>{item.timestamp}</span>}
                      </div>
                      <p className="live-transcript-item__text">{item.content}</p>
                    </div>
                  </li>
                )
              })}
            </ul>
          </section>

          <section className="live-task-card" data-testid="live-voice-task-card">
            <div className="live-task-card__header">
              <span className="live-task-card__icon" aria-hidden="true">
                <Icon name="doc" />
              </span>
              <div className="live-task-card__titles">
                <strong className="live-task-card__title">{taskTitle}</strong>
                <span className="live-task-card__subtitle">
                  {completedCount} de {totalCount} etapas concluídas
                </span>
              </div>
              <span className="live-task-card__chevron" aria-hidden="true">
                <Icon name="chevron-right" />
              </span>
            </div>

            <div
              className="live-task-card__progress"
              data-testid="live-voice-task-progress-bar"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progressPercent}
              aria-label="Progresso das etapas da tarefa"
            >
              <span style={{ width: `${progressPercent}%` }} />
            </div>

            {effectiveTodos.length > 0 && (
              <ul className="live-task-card__checklist">
                {effectiveTodos.map((todo, idx) => {
                  const isDone = todo.status === 'completed'
                  return (
                    <li
                      key={todo.id ?? `${idx}-${todo.content}`}
                      className={`live-task-card__step${isDone ? ' is-completed' : ''}`}
                    >
                      <span className="live-task-card__check" aria-hidden="true">
                        {isDone ? '✓' : '○'}
                      </span>
                      <span className="live-task-card__step-text">{todo.content}</span>
                    </li>
                  )
                })}
              </ul>
            )}

            <button
              type="button"
              className="live-task-card__action"
              data-testid="live-voice-open-in-code"
              onClick={() => onOpenCodeSession?.(delegatedCodeSession?.code_conversation_id)}
            >
              <Icon name="external" />
              <span>Abrir no Code</span>
            </button>
          </section>
        </aside>
      </div>

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

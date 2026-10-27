import { useEffect, useMemo, useRef, useState } from 'react'
import { useReducedMotion } from 'motion/react'
import { connectLiveVoiceSession, type LiveVoiceSocketHandle, type WordTiming } from '../../api/conversations'
import { LiveVoiceQuickSettings } from './LiveVoiceQuickSettings'
import type { LiveVoicePhase } from './activityTypes'

export type LongFormMicConstraints = {
  echoCancellation: boolean
  noiseSuppression: boolean
  autoGainControl: boolean
  channelCount: number
  sampleRate: number
}

export const LONG_FORM_MIC_CONSTRAINTS: LongFormMicConstraints = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: false,
  channelCount: 1,
  sampleRate: 24000,
}

export const LIVE_VOICE_DUCKING_MULTIPLIER = 2.8
export const LIVE_VOICE_DUCKING_MIN_SPEECH_MS = 220
export const LIVE_VOICE_DUCKING_TAIL_MS = 250
export const LIVE_VOICE_ECHO_SIMILARITY_THRESHOLD = 0.65

export type DuckingEvaluationInput = {
  rmsEnergy: number
  baseThreshold: number
  sustainedSpeechMs: number
  isAssistantSpeaking: boolean
  msSinceLastTtsChunk: number
}

export function shouldTriggerDuckingBargeIn(input: DuckingEvaluationInput): boolean {
  const inDuckingWindow =
    input.isAssistantSpeaking || input.msSinceLastTtsChunk <= LIVE_VOICE_DUCKING_TAIL_MS
  if (!inDuckingWindow) {
    return input.rmsEnergy >= input.baseThreshold
  }
  const requiredEnergy = input.baseThreshold * LIVE_VOICE_DUCKING_MULTIPLIER
  return (
    input.rmsEnergy >= requiredEnergy &&
    input.sustainedSpeechMs >= LIVE_VOICE_DUCKING_MIN_SPEECH_MS
  )
}

function normalizeLexicalText(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function charTrigrams(text: string): Map<string, number> {
  const padded = `  ${text}  `
  const counts = new Map<string, number>()
  for (let i = 0; i <= padded.length - 3; i += 1) {
    const gram = padded.slice(i, i + 3)
    counts.set(gram, (counts.get(gram) ?? 0) + 1)
  }
  return counts
}

export function computeEchoSimilarity(transcript: string, referenceTts: string): number {
  const normMic = normalizeLexicalText(transcript)
  const normTts = normalizeLexicalText(referenceTts)
  if (!normMic || !normTts) return 0
  if (normMic === normTts) return 1
  if (normMic.length >= 6 && normTts.includes(normMic)) {
    return 0.92
  }

  const micWords = normMic.split(' ').filter(Boolean)
  const ttsWordsSet = new Set(normTts.split(' ').filter(Boolean))
  if (micWords.length === 0 || ttsWordsSet.size === 0) return 0

  let matchedWords = 0
  for (const w of micWords) {
    if (ttsWordsSet.has(w)) matchedWords += 1
  }
  const wordContainment = matchedWords / micWords.length

  const gramsA = charTrigrams(normMic)
  const gramsB = charTrigrams(normTts)
  let intersection = 0
  let totalA = 0
  let totalB = 0
  for (const [gram, countA] of gramsA.entries()) {
    totalA += countA
    const countB = gramsB.get(gram) ?? 0
    intersection += Math.min(countA, countB)
  }
  for (const countB of gramsB.values()) {
    totalB += countB
  }
  const trigramDice = totalA + totalB > 0 ? (2 * intersection) / (totalA + totalB) : 0

  return Number(Math.min(1, wordContainment * 0.85 + trigramDice * 0.15).toFixed(4))
}

export function isEchoOfRecentTts(
  transcript: string,
  recentTtsSentences: ReadonlyArray<string>,
  threshold = LIVE_VOICE_ECHO_SIMILARITY_THRESHOLD,
): boolean {
  for (const sentence of recentTtsSentences) {
    if (computeEchoSimilarity(transcript, sentence) >= threshold) {
      return true
    }
  }
  return false
}

export type LiveVoiceStageProps = {
  isOpen: boolean
  conversationId?: string
  voiceState?: LiveVoicePhase
  spokenText?: string
  userTranscript?: string
  wordTimings?: WordTiming[]
  playbackElapsedMs?: number
  workingStatusText?: string | null
  isPushToTalkMode?: boolean
  isPushToTalkActive?: boolean
  reducedMotion?: boolean
  onBargeIn?: (spokenCharOffset: number) => void
  onUserSpeechUtterance?: (text: string) => void
  onTogglePushToTalkMode?: () => void
  onPushToTalkHoldChange?: (holding: boolean) => void
  onClose: () => void
}

function stateLabel(state: LiveVoicePhase): string {
  switch (state) {
    case 'listening':
      return 'Ouvindo você…'
    case 'thinking':
      return 'Pensando…'
    case 'working':
      return 'Trabalhando em segundo plano…'
    case 'speaking':
      return 'Falando com você'
    case 'interrupted':
      return 'Interrompido — pode falar!'
  }
}

export function LiveVoiceStage({
  isOpen,
  conversationId,
  voiceState = 'listening',
  spokenText = '',
  userTranscript = '',
  wordTimings = [],
  playbackElapsedMs = 0,
  workingStatusText = null,
  isPushToTalkMode = false,
  isPushToTalkActive = false,
  reducedMotion: forcedReducedMotion,
  onBargeIn,
  onUserSpeechUtterance,
  onTogglePushToTalkMode,
  onPushToTalkHoldChange,
  onClose,
}: LiveVoiceStageProps) {
  const systemReducedMotion = useReducedMotion()
  const isReducedMotion = Boolean(forcedReducedMotion ?? systemReducedMotion)
  const [interruptedTruncatedText, setInterruptedTruncatedText] = useState<string | null>(null)
  const [quickSettingsOpen, setQuickSettingsOpen] = useState(false)
  const [activeProfileSummary, setActiveProfileSummary] = useState('100% Local Expressivo (PT-BR)')
  const socketRef = useRef<LiveVoiceSocketHandle | null>(null)
  const epochRef = useRef<number>(1)
  const chunkSeqRef = useRef<number>(0)

  useEffect(() => {
    if (!isOpen || !conversationId) return
    const handle = connectLiveVoiceSession(conversationId, (event) => {
      if (event.type === 'session.ready') {
        epochRef.current = event.audio_epoch
      } else if (event.type === 'tts.chunk') {
        epochRef.current = event.epoch
        chunkSeqRef.current = event.chunk_seq
        setInterruptedTruncatedText(null)
      } else if (event.type === 'audio.clear') {
        epochRef.current = event.new_epoch
        if (event.truncated_text) {
          setInterruptedTruncatedText(event.truncated_text)
        }
      } else if (event.type === 'stt.transcript' && event.is_end_of_utterance && event.text.trim()) {
        onUserSpeechUtterance?.(event.text.trim())
      }
    })
    socketRef.current = handle
    return () => {
      handle?.close()
      socketRef.current = null
    }
  }, [isOpen, conversationId, onUserSpeechUtterance])

  const activeWordTiming = useMemo(() => {
    if (wordTimings.length === 0) return null
    return (
      wordTimings.find(
        (wt) => playbackElapsedMs >= wt.start_ms && playbackElapsedMs <= wt.end_ms,
      ) ?? wordTimings[wordTimings.length - 1]
    )
  }, [wordTimings, playbackElapsedMs])

  useEffect(() => {
    if (!isOpen) return
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        onClose()
      } else if (e.code === 'Space' && e.ctrlKey) {
        e.preventDefault()
        onPushToTalkHoldChange?.(true)
      }
    }
    function handleKeyUp(e: KeyboardEvent) {
      if (e.code === 'Space') {
        onPushToTalkHoldChange?.(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('keyup', handleKeyUp)
    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('keyup', handleKeyUp)
    }
  }, [isOpen, onClose, onPushToTalkHoldChange])

  if (!isOpen) return null

  function handleBargeInClick() {
    const charOffset = activeWordTiming ? activeWordTiming.char_end : spokenText.length
    if (spokenText) {
      setInterruptedTruncatedText(`${spokenText.slice(0, charOffset).trim()} [interrompido]`)
    }
    socketRef.current?.sendBargeIn(epochRef.current, chunkSeqRef.current, playbackElapsedMs)
    onBargeIn?.(charOffset)
  }

  return (
    <div
      className="live-voice-stage"
      data-testid="live-voice-stage"
      data-voice-state={voiceState}
      data-reduced-motion={isReducedMotion ? 'true' : 'false'}
      role="dialog"
      aria-label="Modo Conversa Live"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 90,
        background: 'radial-gradient(circle at 50% 35%, rgba(139, 92, 246, 0.20), #070611 72%)',
        color: '#F4F2FF',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '2rem 1.5rem',
      }}
    >
      <header
        style={{
          width: '100%',
          maxWidth: '760px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
          <span
            style={{
              padding: '0.22rem 0.65rem',
              borderRadius: '999px',
              background: 'rgba(139, 92, 246, 0.22)',
              border: '1px solid rgba(167, 139, 250, 0.4)',
              color: '#DDD6FE',
              fontSize: '0.78rem',
              fontWeight: 600,
            }}
          >
            ((•)) CONVERSA LIVE · FULL-DUPLEX
          </span>
          <span
            data-testid="live-voice-active-profile-pill"
            style={{
              padding: '0.2rem 0.6rem',
              borderRadius: '999px',
              background: 'rgba(255, 255, 255, 0.06)',
              border: '1px solid rgba(255, 255, 255, 0.14)',
              fontSize: '0.76rem',
              color: '#E9D5FF',
            }}
          >
            ◈ {activeProfileSummary}
          </span>
          <span style={{ fontSize: '0.76rem', opacity: 0.72 }}>
            Anti-Eco 4 Barreiras Ativo
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <button
            type="button"
            className="btn-secondary"
            data-testid="live-voice-open-settings-button"
            onClick={() => setQuickSettingsOpen(true)}
            aria-label="Configurar voz, ouvido e hardware do modo Live"
          >
            ⚙ Configurar Voz
          </button>

          <button
            type="button"
            className="btn-ghost"
            data-testid="live-voice-close-button"
            onClick={onClose}
            aria-label="Sair do modo Conversa Live"
          >
            Encerrar Live (Esc)
          </button>
        </div>
      </header>

      <main
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: '1.5rem',
          maxWidth: '680px',
          textAlign: 'center',
        }}
      >
        <div
          data-testid="live-voice-orb"
          style={{
            width: '148px',
            height: '148px',
            borderRadius: '999px',
            background:
              voiceState === 'interrupted'
                ? 'radial-gradient(circle, #F59E0B, #78350F)'
                : voiceState === 'speaking'
                  ? 'radial-gradient(circle, #C4B5FD, #8B5CF6 60%, #2E1065)'
                  : 'radial-gradient(circle, #A78BFA, #6D28D9 65%, #1E1B4B)',
            boxShadow: isReducedMotion
              ? '0 0 0 2px rgba(167, 139, 250, 0.5)'
              : '0 0 56px rgba(139, 92, 246, 0.55)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            transition: isReducedMotion ? 'none' : 'transform 180ms ease, box-shadow 180ms ease',
          }}
        >
          <span style={{ fontSize: '2rem', fontWeight: 700 }}>◈</span>
        </div>

        <div style={{ display: 'grid', gap: '0.45rem' }}>
          <strong data-testid="live-voice-state-label" style={{ fontSize: '1.1rem', color: '#DDD6FE' }}>
            {stateLabel(voiceState)}
          </strong>
          {workingStatusText && (
            <div
              data-testid="live-voice-working-badge"
              style={{
                padding: '0.35rem 0.85rem',
                borderRadius: '999px',
                background: 'rgba(16, 14, 32, 0.88)',
                border: '1px solid rgba(139, 92, 246, 0.35)',
                fontSize: '0.82rem',
                color: '#C4B5FD',
              }}
            >
              ⚙ {workingStatusText}
            </div>
          )}
        </div>

        <div
          data-testid="live-voice-captions"
          style={{
            minHeight: '4.5rem',
            padding: '1rem 1.25rem',
            borderRadius: '1rem',
            background: 'rgba(16, 14, 32, 0.78)',
            border: '1px solid rgba(255, 255, 255, 0.08)',
            fontSize: '1.05rem',
            lineHeight: 1.55,
          }}
        >
          {interruptedTruncatedText ? (
            <p style={{ margin: 0, color: '#FCD34D' }}>{interruptedTruncatedText}</p>
          ) : wordTimings.length > 0 ? (
            <p style={{ margin: 0, display: 'flex', flexWrap: 'wrap', gap: '0.32rem', justifyContent: 'center' }}>
              {wordTimings.map((wt, idx) => {
                const isActive = activeWordTiming === wt
                return (
                  <span
                    key={`${wt.word}-${idx}`}
                    data-testid={isActive ? 'live-caption-active-word' : undefined}
                    style={{
                      color: isActive ? '#F4F2FF' : 'rgba(244, 242, 255, 0.65)',
                      background: isActive ? 'rgba(139, 92, 246, 0.35)' : 'transparent',
                      padding: '0.05rem 0.25rem',
                      borderRadius: '0.3rem',
                      fontWeight: isActive ? 700 : 400,
                    }}
                  >
                    {wt.word}
                  </span>
                )
              })}
            </p>
          ) : spokenText ? (
            <p style={{ margin: 0 }}>{spokenText}</p>
          ) : userTranscript ? (
            <p style={{ margin: 0, opacity: 0.82 }}>“{userTranscript}”</p>
          ) : (
            <p style={{ margin: 0, opacity: 0.55 }}>
              Fale naturalmente — você pode interromper o Orin a qualquer momento.
            </p>
          )}
        </div>
      </main>

      <footer
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.85rem',
          flexWrap: 'wrap',
          justifyContent: 'center',
        }}
      >
        <button
          type="button"
          className="btn-secondary"
          data-testid="live-voice-barge-in-button"
          onClick={handleBargeInClick}
        >
          ✋ Interromper fala (Barge-in &lt;80ms)
        </button>

        <button
          type="button"
          className={isPushToTalkMode ? 'btn-primary' : 'btn-secondary'}
          data-testid="live-voice-ptt-button"
          onClick={() => onTogglePushToTalkMode?.()}
          onMouseDown={() => onPushToTalkHoldChange?.(true)}
          onMouseUp={() => onPushToTalkHoldChange?.(false)}
        >
          {isPushToTalkMode
            ? isPushToTalkActive
              ? '🎙 Gravando PTT (Solte Ctrl+Espaço)'
              : '🎙 Push-to-Talk Ativo (Segure Ctrl+Espaço)'
            : '🎙 Ativar Push-to-Talk (Sem Fone)'}
        </button>
      </footer>

      <LiveVoiceQuickSettings
        isOpen={quickSettingsOpen}
        onClose={() => setQuickSettingsOpen(false)}
        onProfileSummaryChange={setActiveProfileSummary}
      />
    </div>
  )
}

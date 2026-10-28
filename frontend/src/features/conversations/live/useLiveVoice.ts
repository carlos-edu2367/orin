import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ApiClient } from '../../../api/client'
import { connectLiveVoiceSession, type LiveVoiceServerEvent, type LiveVoiceSocketHandle } from '../../../api/conversations'
import { UNAVAILABLE_CAPABILITIES, type LiveVoiceCapabilities } from '../../../api/liveVoice'
import type { LiveVoicePhase } from '../activityTypes'
import { BrowserRecognition, browserRecognitionSupported } from './browserRecognition'
import { isEchoOfRecentTts } from './echo'
import { EnergyVad, LIVE_SAMPLE_RATE, MicAccessError, MicCapture, concatPcm, type VadEvent } from './liveAudio'
import { SpeechOutput, textForSpeech } from './liveSpeech'
import { resolveStt, resolveTts, sttModelFor, ttsSelection, type LiveVoicePrefs, type ResolvedStt, type TtsEngine } from './liveVoicePrefs'

export type AssistantSpeechItem = {
  id: string
  text: string
  turnId?: string
  messageId?: string
}

export type LiveVoiceIssue = {
  code: string
  message: string
  hint?: string
}

export type UseLiveVoiceOptions = {
  active: boolean
  conversationId?: string
  client: ApiClient
  prefs: LiveVoicePrefs
  capabilities: LiveVoiceCapabilities | null
  /** Everything the assistant has asked to say in this conversation, oldest first. */
  assistantSpeech: ReadonlyArray<AssistantSpeechItem>
  /** A turn is running (the assistant is thinking or using tools). */
  assistantBusy: boolean
  onUtterance: (text: string) => void
  voiceInstructions?: string
  pushToTalk?: boolean
}

export type LiveVoiceController = {
  phase: LiveVoicePhase
  sttEngine: ResolvedStt
  ttsEngine: TtsEngine
  /** Model and voice the chosen engine speaks with ('' for the system voice). */
  ttsModelId: string
  ttsVoice: string
  userText: string
  assistantText: string
  assistantCharIndex: number
  issue: LiveVoiceIssue | null
  muted: boolean
  setMuted: (muted: boolean) => void
  interrupt: () => void
  setPushToTalkHeld: (held: boolean) => void
  /** 0..1, read every animation frame by the orb and the meter. */
  micLevel: () => number
  outputLevel: () => number
  thinkingAudio: () => boolean
  dismissIssue: () => void
}

const INTERRUPTED_FLASH_MS = 900
const AWAIT_REPLY_TIMEOUT_MS = 90_000
const TRANSCRIBE_TIMEOUT_MS = 25_000

export function useLiveVoice(options: UseLiveVoiceOptions): LiveVoiceController {
  const { active, conversationId, client, prefs, capabilities, assistantSpeech, assistantBusy } = options
  const caps = capabilities ?? UNAVAILABLE_CAPABILITIES
  const browserSupported = useMemo(() => browserRecognitionSupported(), [])
  // Until the server says what can run, nothing is started: a guess would open the wrong engine.
  const sttEngine = capabilities ? resolveStt(prefs.stt, caps, browserSupported) : null
  const ttsEngine = resolveTts(prefs.tts, caps)
  const ttsChoice = ttsEngine === 'system' ? { modelId: '', voice: '' } : ttsSelection(prefs, caps, ttsEngine)
  const ttsModelId = ttsChoice.modelId
  const ttsVoice = ttsChoice.voice
  const sttModel = sttEngine && sttEngine !== 'browser' ? sttModelFor(prefs, caps, sttEngine) : null

  const [micState, setMicState] = useState<'connecting' | 'ready' | 'error'>('connecting')
  const [userSpeaking, setUserSpeaking] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [awaitingReply, setAwaitingReply] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [interrupted, setInterrupted] = useState(false)
  const [muted, setMutedState] = useState(false)
  const [userText, setUserText] = useState('')
  const [assistantText, setAssistantText] = useState('')
  const [assistantCharIndex, setAssistantCharIndex] = useState(0)
  const [issue, setIssue] = useState<LiveVoiceIssue | null>(null)

  const socketRef = useRef<LiveVoiceSocketHandle | null>(null)
  const micRef = useRef<MicCapture | null>(null)
  const vadRef = useRef<EnergyVad | null>(null)
  const speechRef = useRef<SpeechOutput | null>(null)
  const recognitionRef = useRef<BrowserRecognition | null>(null)
  const epochRef = useRef(1)
  const spokenIdsRef = useRef<Set<string> | null>(null)
  const spokenTurnIdsRef = useRef<Set<string>>(new Set())
  const spokenMessageIdsRef = useRef<Set<string>>(new Set())
  const remSpokenTurnIdsRef = useRef<Set<string>>(new Set())
  const seededFromEmptyRef = useRef(false)
  const hasUserSpokenOrBusyRef = useRef(Boolean(assistantBusy))
  const recentSpokenRef = useRef<string[]>([])
  const manualInterruptRef = useRef(false)
  const vadCandidateSuppressRef = useRef(false)
  const pendingTtsCountRef = useRef(0)
  const hasSpokenInTurnRef = useRef(false)
  const [unsuppressSeq, setUnsuppressSeq] = useState(0)
  const eouTimerRef = useRef<number | null>(null)
  const interruptTimerRef = useRef<number | null>(null)
  const mutedRef = useRef(false)
  const pttHeldRef = useRef(false)

  // Latest values for callbacks living inside long-lived audio handlers.
  const latest = useRef({ options, sttEngine, ttsEngine, prefs })
  useLayoutEffect(() => {
    latest.current = { options, sttEngine, ttsEngine, prefs }
  })

  const clearEouTimer = () => {
    if (eouTimerRef.current !== null) window.clearTimeout(eouTimerRef.current)
    eouTimerRef.current = null
  }

  const recordItemSeen = useCallback((item: AssistantSpeechItem, markRemainderDone = false) => {
    spokenIdsRef.current?.add(item.id)
    if (item.turnId) {
      spokenTurnIdsRef.current.add(item.turnId)
      if (markRemainderDone || item.id.startsWith('message-rem:') || item.id.startsWith('message:')) {
        remSpokenTurnIdsRef.current.add(item.turnId)
      }
    }
    const rawMsgId = item.messageId
      ?? (item.id.startsWith('message-rem:') ? item.id.slice('message-rem:'.length) : undefined)
      ?? (item.id.startsWith('message:') ? item.id.slice('message:'.length) : undefined)
    if (rawMsgId) {
      spokenMessageIdsRef.current.add(rawMsgId)
      if (markRemainderDone || item.id.startsWith('message-rem:') || item.id.startsWith('message:')) {
        spokenIdsRef.current?.add(`message:${rawMsgId}`)
        spokenIdsRef.current?.add(`message-rem:${rawMsgId}`)
      }
    }
  }, [])

  const releaseSuppress = useCallback(() => {
    if (vadCandidateSuppressRef.current) {
      vadCandidateSuppressRef.current = false
      setUnsuppressSeq((n) => n + 1)
    }
  }, [])

  const deliverTranscript = useCallback((raw: string) => {
    const text = raw.trim()
    setTranscribing(false)
    if (!text) {
      releaseSuppress()
      return
    }
    const speechActive = speechRef.current?.speaking ?? false
    if (speechActive && isEchoOfRecentTts(text, recentSpokenRef.current)) {
      releaseSuppress()
      return
    }
    if (!speechActive && isEchoOfRecentTts(text, recentSpokenRef.current.slice(-2), 0.85)) {
      releaseSuppress()
      return
    }
    hasUserSpokenOrBusyRef.current = true
    seededFromEmptyRef.current = false
    if (spokenIdsRef.current) {
      for (const item of latest.current.options.assistantSpeech) {
        recordItemSeen(item, true)
      }
    }
    setUserText(text)
    setAwaitingReply(true)
    hasSpokenInTurnRef.current = false
    manualInterruptRef.current = false
    vadCandidateSuppressRef.current = false
    latest.current.options.onUtterance(text)
  }, [recordItemSeen, releaseSuppress])

  const stopSpeechPlayback = useCallback((isManual: boolean) => {
    const speech = speechRef.current
    speech?.stopThinking()
    if (!speech?.speaking && pendingTtsCountRef.current === 0 && !isManual) return
    const position = speech?.playbackPosition() ?? null
    speech?.stop()
    if (isManual) {
      manualInterruptRef.current = true
      vadCandidateSuppressRef.current = false
      if (spokenIdsRef.current) {
        for (const item of latest.current.options.assistantSpeech) {
          recordItemSeen(item, true)
        }
      }
    } else {
      vadCandidateSuppressRef.current = true
    }
    pendingTtsCountRef.current = 0
    if (!speechRef.current?.speaking) {
      vadRef.current?.setDucking(false)
      recognitionRef.current?.setPaused(false)
    }
    if (position) socketRef.current?.sendBargeIn(epochRef.current, position.chunkSeq, position.playedMs)
    setSpeaking(false)
    setInterrupted(true)
    if (interruptTimerRef.current !== null) window.clearTimeout(interruptTimerRef.current)
    interruptTimerRef.current = window.setTimeout(() => setInterrupted(false), INTERRUPTED_FLASH_MS)
  }, [recordItemSeen])

  const interrupt = useCallback(() => {
    if (!speechRef.current?.speaking && pendingTtsCountRef.current === 0) return
    stopSpeechPlayback(true)
  }, [stopSpeechPlayback])

  const handleVad = useCallback((event: VadEvent) => {
    const socket = socketRef.current
    const engine = latest.current.sttEngine
    const serverStt = engine !== null && engine !== 'browser'
    if (event.type === 'speech_start') {
      clearEouTimer()
      speechRef.current?.stopThinking()
      hasSpokenInTurnRef.current = false
      if (speechRef.current?.speaking) {
        if (!latest.current.prefs.bargeIn) return
        stopSpeechPlayback(false)
      }
      setUserSpeaking(true)
      if (serverStt) for (const frame of event.preRoll) socket?.sendPcmFrame(frame.slice().buffer)
    } else if (event.type === 'speech_frame') {
      if (serverStt) socket?.sendPcmFrame(event.frame.slice().buffer)
    } else {
      setUserSpeaking(false)
      if (!serverStt) {
        releaseSuppress()
        return
      }
      const audioMs = (concatPcm(event.frames).length / LIVE_SAMPLE_RATE) * 1000
      // A cough or a click is not a sentence: drop it before paying for a transcription.
      if (audioMs < 350) {
        releaseSuppress()
        socket?.sendAudioReset()
        return
      }
      setTranscribing(true)
      socket?.sendSpeechEnd('', event.silenceMs, { sample_rate: LIVE_SAMPLE_RATE })
    }
  }, [releaseSuppress, stopSpeechPlayback])

  const handleServerEvent = useCallback((event: LiveVoiceServerEvent) => {
    switch (event.type) {
      case 'session.ready':
        epochRef.current = event.audio_epoch
        break
      case 'audio.clear':
        epochRef.current = event.new_epoch
        pendingTtsCountRef.current = 0
        if (!speechRef.current?.speaking) {
          vadRef.current?.setDucking(false)
          recognitionRef.current?.setPaused(false)
        }
        break
      case 'stt.processing':
        setTranscribing(true)
        break
      case 'stt.transcript':
        setIssue(null)
        deliverTranscript(event.text)
        break
      case 'stt.empty':
      case 'vad.echo_discarded':
        setTranscribing(false)
        setUserSpeaking(false)
        vadRef.current?.reset()
        releaseSuppress()
        break
      case 'stt.error':
        setTranscribing(false)
        setUserSpeaking(false)
        vadRef.current?.reset()
        releaseSuppress()
        setIssue({ code: event.code, message: event.message, hint: event.hint })
        break
      case 'vad.eou_wait': {
        setTranscribing(false)
        setUserText(event.partial_text)
        clearEouTimer()
        const waitMs = Math.max(200, event.required_silence_ms - (event.silence_ms ?? 0))
        const hintText = event.partial_text || ''
        eouTimerRef.current = window.setTimeout(() => {
          if (vadRef.current?.speaking) return
          setTranscribing(true)
          socketRef.current?.sendSpeechEnd(hintText, event.required_silence_ms, { sample_rate: LIVE_SAMPLE_RATE })
        }, waitMs)
        break
      }
      case 'tts.chunk':
        epochRef.current = event.epoch ?? event.audio_epoch ?? epochRef.current
        if (pendingTtsCountRef.current > 0) {
          pendingTtsCountRef.current = Math.max(0, pendingTtsCountRef.current - 1)
        }
        if (manualInterruptRef.current || vadCandidateSuppressRef.current) {
          if (!speechRef.current?.speaking && pendingTtsCountRef.current === 0) {
            vadRef.current?.setDucking(false)
            recognitionRef.current?.setPaused(false)
          }
          break
        }
        if (event.encoding && event.encoding !== 'pcm16' && event.audio_base64) {
          speechRef.current?.playEncoded(event.clean_text, event.audio_base64, event.chunk_seq)
        } else if (event.pcm_base64) {
          speechRef.current?.playPcm(event.clean_text, event.pcm_base64, event.sample_rate, event.chunk_seq)
        }
        if (!speechRef.current?.speaking && pendingTtsCountRef.current === 0) {
          vadRef.current?.setDucking(false)
          recognitionRef.current?.setPaused(false)
        }
        break
      case 'tts.error':
        pendingTtsCountRef.current = Math.max(0, pendingTtsCountRef.current - 1)
        setIssue({ code: event.code, message: event.message, hint: event.hint })
        if (event.text && !manualInterruptRef.current && !vadCandidateSuppressRef.current) {
          speechRef.current?.speakSystem(event.text)
        }
        if (!speechRef.current?.speaking && pendingTtsCountRef.current === 0) {
          vadRef.current?.setDucking(false)
          recognitionRef.current?.setPaused(false)
        }
        break
      default:
        break
    }
  }, [deliverTranscript, releaseSuppress])

  // Socket, speaker and microphone live exactly as long as the Live mode is open.
  useEffect(() => {
    if (!active || !conversationId) return
    let disposed = false
    spokenIdsRef.current = null
    spokenTurnIdsRef.current = new Set()
    spokenMessageIdsRef.current = new Set()
    remSpokenTurnIdsRef.current = new Set()
    seededFromEmptyRef.current = false
    hasUserSpokenOrBusyRef.current = Boolean(latest.current.options.assistantBusy)
    manualInterruptRef.current = false
    vadCandidateSuppressRef.current = false
    pendingTtsCountRef.current = 0
    hasSpokenInTurnRef.current = false

    const socket = connectLiveVoiceSession(conversationId, (event) => handleServerEvent(event), {
      csrfToken: client.socketCsrfToken(),
      onStatus: (status) => {
        if (status === 'unauthorized') {
          setIssue({ code: 'unauthorized', message: 'Sua sessão expirou. Recarregue a página para voltar a conversar por voz.' })
        }
        if (status === 'closed' || status === 'unauthorized') {
          pendingTtsCountRef.current = 0
          if (!speechRef.current?.speaking) {
            vadRef.current?.setDucking(false)
            recognitionRef.current?.setPaused(false)
          }
        }
      },
    })
    socketRef.current = socket

    const speech = new SpeechOutput({
      onStart: (text) => {
        hasSpokenInTurnRef.current = true
        speechRef.current?.stopThinking()
        setSpeaking(true)
        setAwaitingReply(false)
        setAssistantText(text)
        setAssistantCharIndex(0)
        vadRef.current?.setDucking(true)
        recognitionRef.current?.setPaused(true)
        recentSpokenRef.current = [...recentSpokenRef.current.slice(-7), text]
      },
      onProgress: (_text, charIndex) => setAssistantCharIndex(charIndex),
      onIdle: () => {
        setSpeaking(false)
        // The room still rings for a moment after the last word; keep ducking if more TTS chunks are still in flight.
        window.setTimeout(() => {
          if (!speechRef.current?.speaking && pendingTtsCountRef.current === 0) {
            vadRef.current?.setDucking(false)
            recognitionRef.current?.setPaused(false)
          }
        }, 250)
      },
    }, { voiceUri: prefs.systemVoiceUri, rate: prefs.rate, pitch: prefs.pitch })
    speechRef.current = speech

    const vad = new EnergyVad({ sensitivity: prefs.sensitivity, endSilenceMs: prefs.endSilenceMs })
    vadRef.current = vad

    void MicCapture.open({
      deviceId: prefs.micDeviceId,
      onFrame: (frame, rms) => {
        if (mutedRef.current) return
        if (latest.current.options.pushToTalk && !pttHeldRef.current) return
        for (const event of vad.process(frame, rms)) handleVad(event)
      },
    })
      .then((mic) => {
        if (disposed) {
          mic.close()
          return
        }
        micRef.current = mic
        setMicState('ready')
      })
      .catch((error: unknown) => {
        if (disposed) return
        setMicState('error')
        setIssue({
          code: error instanceof MicAccessError ? `mic_${error.state}` : 'mic_error',
          message: error instanceof Error ? error.message : 'Não foi possível abrir o microfone.',
        })
      })

    return () => {
      disposed = true
      clearEouTimer()
      if (interruptTimerRef.current !== null) window.clearTimeout(interruptTimerRef.current)
      micRef.current?.close()
      micRef.current = null
      recognitionRef.current?.stop()
      recognitionRef.current = null
      speech.dispose()
      speechRef.current = null
      socket?.close()
      socketRef.current = null
      vadRef.current = null
      setSpeaking(false)
      setUserSpeaking(false)
      setTranscribing(false)
      setAwaitingReply(false)
      setMicState('connecting')
      setIssue(null)
      setUserText('')
      setAssistantText('')
    }
    // The audio graph is rebuilt only for a new conversation or microphone.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, conversationId, prefs.micDeviceId, client, handleServerEvent, handleVad])

  // Tuning changes apply live, without reopening the microphone.
  useEffect(() => {
    vadRef.current?.setSensitivity(prefs.sensitivity)
    speechRef.current?.setSystemVoice({ voiceUri: prefs.systemVoiceUri, rate: prefs.rate, pitch: prefs.pitch })
  }, [prefs.sensitivity, prefs.systemVoiceUri, prefs.rate, prefs.pitch])

  // Tell the server which engines, models and voice to use.
  useEffect(() => {
    if (!active) return
    const socket = socketRef.current
    if (!socket) return
    const serverStt = sttEngine && sttEngine !== 'browser' ? sttEngine : null
    socket.sendConfig({
      ...(serverStt ? { stt_provider: serverStt, ...(sttModel ? { stt_model: sttModel } : {}) } : {}),
      // The system voice never reaches the server; `local_sidecar` keeps its config valid.
      tts_provider: ttsEngine === 'system' ? 'local_sidecar' : ttsEngine,
      ...(ttsEngine !== 'system' ? { tts_model: ttsModelId, voice_id: ttsVoice } : {}),
      speed: prefs.rate,
      language: 'pt-BR',
    })
  }, [active, conversationId, sttEngine, sttModel, ttsEngine, ttsModelId, ttsVoice, prefs.rate])

  // Browser recognition runs alongside the microphone (which still drives the meter and barge-in).
  useEffect(() => {
    if (!active || sttEngine !== 'browser') return
    const recognition = new BrowserRecognition({
      onInterim: (text) => {
        setUserText(text)
        setUserSpeaking(true)
      },
      onFinal: (text) => {
        setUserSpeaking(false)
        deliverTranscript(text)
      },
      onError: (code, message) => setIssue({ code: `browser_${code}`, message }),
    })
    recognitionRef.current = recognition
    recognition.start()
    return () => {
      recognition.stop()
      if (recognitionRef.current === recognition) recognitionRef.current = null
    }
  }, [active, sttEngine, deliverTranscript])

  // Capabilities arrive after the socket opens, so "nothing can hear you" is only claimed once they did.
  const missingStt: LiveVoiceIssue | null = micState === 'ready' && capabilities !== null && sttEngine === null
    ? {
        code: 'no_stt',
        message: 'Nenhum jeito de te ouvir está pronto ainda. Abra a configuração de voz para instalar o Whisper local ou usar sua chave OpenAI.',
      }
    : null

  // "Thinking" ends when the turn that answered ends, even if it chose not to speak.
  const wasBusyRef = useRef(assistantBusy)

  // Speak whatever the assistant adds after the Live mode opened.
  useEffect(() => {
    if (!active) return
    if (!wasBusyRef.current && assistantBusy) {
      hasUserSpokenOrBusyRef.current = true
      seededFromEmptyRef.current = false
      manualInterruptRef.current = false
      vadCandidateSuppressRef.current = false
      hasSpokenInTurnRef.current = false
    } else if (assistantBusy) {
      hasUserSpokenOrBusyRef.current = true
      seededFromEmptyRef.current = false
    }
    if (spokenIdsRef.current === null) {
      spokenIdsRef.current = new Set()
      for (const item of assistantSpeech) {
        recordItemSeen(item, true)
      }
      seededFromEmptyRef.current = assistantSpeech.length === 0 && !assistantBusy && !hasUserSpokenOrBusyRef.current
      return
    }
    if (seededFromEmptyRef.current && assistantSpeech.length > 0 && !assistantBusy && !hasUserSpokenOrBusyRef.current) {
      for (const item of assistantSpeech) {
        recordItemSeen(item, true)
      }
      seededFromEmptyRef.current = false
      return
    }
    const spoken = spokenIdsRef.current
    for (const item of assistantSpeech) {
      if (spoken.has(item.id)) continue
      // Skip full-message fallback if we already spoke `speak:` chunks for this turn before an SSE resync/eviction.
      if (
        item.id.startsWith('message:') &&
        ((item.turnId && spokenTurnIdsRef.current.has(item.turnId)) ||
          (item.messageId && spokenMessageIdsRef.current.has(item.messageId)))
      ) {
        recordItemSeen(item, true)
        continue
      }
      // Skip late `speak:` chunks if `message-rem:` was already spoken for this turn.
      if (item.id.startsWith('speak:') && item.turnId && remSpokenTurnIdsRef.current.has(item.turnId)) {
        recordItemSeen(item, true)
        continue
      }
      if (mutedRef.current || manualInterruptRef.current) {
        recordItemSeen(item, true)
        continue
      }
      // If a false-alarm VAD barge-in candidate is currently active (user speaking / transcribing), hold off on marking
      // the item spoken until the VAD segment resolves; if no barge-in is in flight, clear stale candidate suppression.
      if (vadCandidateSuppressRef.current) {
        if (vadRef.current?.speaking || userSpeaking || transcribing) continue
        vadCandidateSuppressRef.current = false
      }
      const cleanToSpeak = textForSpeech(item.text)
      if (!cleanToSpeak) {
        recordItemSeen(item, false)
        continue
      }
      recordItemSeen(item, false)
      if (latest.current.ttsEngine !== 'system' && socketRef.current?.isOpen()) {
        const sent = socketRef.current.sendSpeakText(item.text, latest.current.options.voiceInstructions)
        if (sent === false) {
          if (!speechRef.current?.speaking && pendingTtsCountRef.current === 0) {
            vadRef.current?.setDucking(false)
            recognitionRef.current?.setPaused(false)
          }
        } else {
          setAwaitingReply(true)
          pendingTtsCountRef.current += 1
          vadRef.current?.setDucking(true)
        }
      } else {
        speechRef.current?.speakSystem(item.text)
      }
    }
  }, [active, assistantBusy, assistantSpeech, recordItemSeen, unsuppressSeq, userSpeaking, transcribing])

  useEffect(() => {
    const justFinishedBusy = wasBusyRef.current && !assistantBusy
    if (!wasBusyRef.current && assistantBusy) {
      hasUserSpokenOrBusyRef.current = true
      seededFromEmptyRef.current = false
      manualInterruptRef.current = false
      vadCandidateSuppressRef.current = false
      hasSpokenInTurnRef.current = false
    }
    if (justFinishedBusy) {
      setAwaitingReply(false)
      hasSpokenInTurnRef.current = false
    }
    wasBusyRef.current = assistantBusy

    const effectiveAwaitingReply = awaitingReply && !justFinishedBusy
    const shouldPlayThinkingCue =
      active &&
      !muted &&
      !speaking &&
      !speechRef.current?.speaking &&
      !userSpeaking &&
      !interrupted &&
      !manualInterruptRef.current &&
      (transcribing || effectiveAwaitingReply || assistantBusy)

    if (!shouldPlayThinkingCue) {
      speechRef.current?.stopThinking()
      return
    }
    if (!hasSpokenInTurnRef.current) {
      speechRef.current?.startThinking()
      return
    }
    if (pendingTtsCountRef.current > 0) {
      speechRef.current?.stopThinking()
      return
    }
    const timer = window.setTimeout(() => {
      if (pendingTtsCountRef.current === 0 && !speechRef.current?.speaking) {
        speechRef.current?.startThinking()
      }
    }, 1000)
    return () => window.clearTimeout(timer)
  }, [
    active,
    muted,
    speaking,
    userSpeaking,
    interrupted,
    transcribing,
    awaitingReply,
    assistantBusy,
    assistantSpeech,
    unsuppressSeq,
  ])

  // A transcription that never answers (server restarted, socket dropped) must not freeze the stage.
  useEffect(() => {
    if (!transcribing) return
    const timer = window.setTimeout(() => {
      setTranscribing(false)
      releaseSuppress()
    }, TRANSCRIBE_TIMEOUT_MS)
    return () => window.clearTimeout(timer)
  }, [transcribing, releaseSuppress])
  useEffect(() => {
    if (!awaitingReply) return
    const timer = window.setTimeout(() => setAwaitingReply(false), AWAIT_REPLY_TIMEOUT_MS)
    return () => window.clearTimeout(timer)
  }, [awaitingReply])

  const setMuted = useCallback((next: boolean) => {
    mutedRef.current = next
    setMutedState(next)
    if (next) {
      speechRef.current?.stopThinking()
      vadRef.current?.reset()
      socketRef.current?.sendAudioReset()
      setUserSpeaking(false)
    }
    recognitionRef.current?.setPaused(next)
  }, [])

  const setPushToTalkHeld = useCallback((held: boolean) => {
    pttHeldRef.current = held
    if (!held) {
      const event = vadRef.current?.flush()
      if (event) handleVad(event)
    }
  }, [handleVad])

  let phase: LiveVoicePhase
  if (micState === 'connecting') phase = 'connecting'
  else if (micState === 'error') phase = 'error'
  else if (muted) phase = 'muted'
  else if (interrupted) phase = 'interrupted'
  else if (speaking) phase = 'speaking'
  else if (userSpeaking) phase = 'hearing'
  else if (transcribing) phase = 'transcribing'
  else if (awaitingReply || assistantBusy) phase = assistantBusy && !awaitingReply ? 'working' : 'thinking'
  else phase = 'listening'

  return {
    phase,
    sttEngine,
    ttsEngine,
    ttsModelId,
    ttsVoice,
    userText,
    assistantText,
    assistantCharIndex,
    issue: issue ?? missingStt,
    muted,
    setMuted,
    interrupt,
    setPushToTalkHeld,
    micLevel: () => (mutedRef.current ? 0 : micRef.current?.level ?? 0),
    outputLevel: () => speechRef.current?.level ?? 0,
    thinkingAudio: () => Boolean(speechRef.current?.thinking),
    dismissIssue: () => setIssue(null),
  }
}

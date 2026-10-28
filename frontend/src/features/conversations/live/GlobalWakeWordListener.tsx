import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { ApiClient } from '../../../api/client'
import { ensureFriendConversation, getFriendPersona } from '../../../api/conversations'
import {
  checkWakeWord,
  getLiveVoiceCapabilities,
  UNAVAILABLE_CAPABILITIES,
  type LiveVoiceCapabilities,
} from '../../../api/liveVoice'
import { BrowserRecognition, browserRecognitionSupported } from './browserRecognition'
import { EnergyVad, LIVE_FRAME_MS, MicCapture } from './liveAudio'
import {
  loadLiveVoicePrefs,
  onLiveVoicePrefsChange,
  resolveStt,
  sttModelFor,
  type LiveVoicePrefs,
} from './liveVoicePrefs'
import {
  isLiveVoiceStageOpen,
  matchWakeWord,
  onLiveVoiceStageOpenChange,
  pcm16ToBase64,
  playWakeChime,
} from './wakeWord'

const WAKE_COOLDOWN_MS = 2800
const MIN_WAKE_FRAMES = Math.ceil(200 / LIVE_FRAME_MS)
const MAX_WAKE_FRAMES = Math.ceil(3800 / LIVE_FRAME_MS)

export type OpenFriendLiveDetail = {
  conversationId: string
  commandRemainder: string
}

export function GlobalWakeWordListener({ client }: { client: ApiClient }) {
  const navigate = useNavigate()
  const [prefs, setPrefs] = useState<LiveVoicePrefs>(() => loadLiveVoicePrefs())
  const [stageOpen, setStageOpen] = useState<boolean>(() => isLiveVoiceStageOpen())
  const [caps, setCaps] = useState<LiveVoiceCapabilities>(UNAVAILABLE_CAPABILITIES)
  const [companionName, setCompanionName] = useState<string>('Orin')
  const lastWakeAtRef = useRef<number>(0)
  const checkingRef = useRef<boolean>(false)

  useEffect(() => onLiveVoicePrefsChange(setPrefs), [])
  useEffect(() => onLiveVoiceStageOpenChange(setStageOpen), [])

  useEffect(() => {
    const controller = new AbortController()
    Promise.resolve(getLiveVoiceCapabilities(client, controller.signal))
      .then((value) => {
        if (value) setCaps(value)
      })
      .catch(() => undefined)
    Promise.resolve(getFriendPersona(client, controller.signal))
      .then((persona) => {
        if (persona?.display_name?.trim()) {
          setCompanionName(persona.display_name.trim())
        }
      })
      .catch(() => undefined)
    return () => controller.abort()
  }, [client])

  const triggerWakeIntoFriendLive = useCallback(
    async (commandRemainder = '') => {
      const now = Date.now()
      if (now - lastWakeAtRef.current < WAKE_COOLDOWN_MS) return
      lastWakeAtRef.current = now

      playWakeChime()
      try {
        await window.orinDesktop?.wakeToLiveFriend?.()
      } catch {
        // Ignore if running in browser outside Electron.
      }

      try {
        const friendConv = await ensureFriendConversation(client)
        const targetId = friendConv.conversation_id
        const trimmedRemainder = commandRemainder.trim()
        const search = trimmedRemainder
          ? `?live=1&utterance=${encodeURIComponent(trimmedRemainder)}`
          : '?live=1'
        navigate(`/chats/${encodeURIComponent(targetId)}${search}`, {
          state: {
            openLiveVoice: true,
            initialLiveUtterance: trimmedRemainder || undefined,
          },
        })
        window.dispatchEvent(
          new CustomEvent<OpenFriendLiveDetail>('orin:open-friend-live', {
            detail: {
              conversationId: targetId,
              commandRemainder: trimmedRemainder,
            },
          }),
        )
      } catch {
        // Ignore transient network errors during standby wake.
      }
    },
    [client, navigate],
  )

  // Listen to Electron tray/global shortcut ("Conversa Live (Orin Friend)" / Ctrl+Shift+L)
  // and custom test simulation event (`orin:simulate-wake-word`).
  useEffect(() => {
    const offDesktop = window.orinDesktop?.onToggleLiveVoice?.(() => {
      void triggerWakeIntoFriendLive('')
    })
    const onSimulatedWake = (event: Event) => {
      const detail = (event as CustomEvent<{ transcript?: string; commandRemainder?: string }>).detail
      if (detail?.transcript) {
        const m = matchWakeWord(detail.transcript, [companionName])
        if (m.matched) {
          void triggerWakeIntoFriendLive(m.commandRemainder)
        }
        return
      }
      void triggerWakeIntoFriendLive(detail?.commandRemainder ?? '')
    }
    window.addEventListener('orin:simulate-wake-word', onSimulatedWake)
    return () => {
      if (typeof offDesktop === 'function') offDesktop()
      window.removeEventListener('orin:simulate-wake-word', onSimulatedWake)
    }
  }, [companionName, triggerWakeIntoFriendLive])

  // Background standby microphone + VAD loop (paused whenever LiveVoiceStage is open or wakeWordEnabled is false).
  useEffect(() => {
    if (!prefs.wakeWordEnabled || stageOpen) return
    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) return

    let cancelled = false
    let micInstance: MicCapture | null = null
    let browserRec: BrowserRecognition | null = null

    const browserSupported = browserRecognitionSupported()
    const effectiveStt = resolveStt(prefs.stt, caps, browserSupported)

    const vad = new EnergyVad({
      sensitivity: prefs.sensitivity,
      endSilenceMs: 450,
      startSpeechMs: 64,
      preRollMs: 280,
      maxUtteranceMs: 3800,
    })

    const inspectSpeechFrames = async (frames: Int16Array[]) => {
      if (cancelled || checkingRef.current || isLiveVoiceStageOpen()) return
      if (frames.length < MIN_WAKE_FRAMES || frames.length > MAX_WAKE_FRAMES) return

      checkingRef.current = true
      try {
        const sttProvider =
          effectiveStt && effectiveStt !== 'browser' ? effectiveStt : 'auto'
        const sttModel =
          sttProvider !== 'auto' ? sttModelFor(prefs, caps, sttProvider) : null

        const res = await checkWakeWord(client, {
          pcmBase64: pcm16ToBase64(frames),
          sampleRate: 16000,
          sttProvider,
          sttModel,
          customNames: companionName !== 'Orin' ? [companionName] : [],
        })
        if (cancelled || isLiveVoiceStageOpen()) return
        if (res.matched && res.confidence >= 0.78) {
          await triggerWakeIntoFriendLive(res.command_remainder)
          return
        }
        if (res.transcript) {
          const fallback = matchWakeWord(res.transcript, [companionName])
          if (fallback.matched && fallback.confidence >= 0.78) {
            await triggerWakeIntoFriendLive(fallback.commandRemainder)
          }
        }
      } catch {
        // Ignore standby check errors so background loop stays silent and resilient.
      } finally {
        checkingRef.current = false
      }
    }

    MicCapture.open({
      deviceId: prefs.micDeviceId,
      headphoneMode: false,
      onFrame: (frame, rms) => {
        if (cancelled || isLiveVoiceStageOpen()) return
        const events = vad.process(frame, rms)
        for (const ev of events) {
          if (ev.type === 'speech_end') {
            void inspectSpeechFrames(ev.frames)
          }
        }
      },
    })
      .then((mic) => {
        if (cancelled) {
          mic.close()
          return
        }
        micInstance = mic
      })
      .catch(() => {
        // If mic permission is not granted yet or unavailable, standby waits quietly.
      })

    if (effectiveStt === 'browser' && browserSupported) {
      browserRec = new BrowserRecognition(
        {
          onInterim: () => undefined,
          onFinal: (text: string) => {
            if (cancelled || isLiveVoiceStageOpen()) return
            const matched = matchWakeWord(text, [companionName])
            if (matched.matched && matched.confidence >= 0.78) {
              void triggerWakeIntoFriendLive(matched.commandRemainder)
            }
          },
          onError: () => undefined,
        },
        'pt-BR',
      )
      browserRec.start()
    }

    return () => {
      cancelled = true
      micInstance?.close()
      micInstance = null
      browserRec?.stop()
      browserRec = null
    }
  }, [
    caps,
    client,
    companionName,
    prefs,
    stageOpen,
    triggerWakeIntoFriendLive,
  ])

  return null
}

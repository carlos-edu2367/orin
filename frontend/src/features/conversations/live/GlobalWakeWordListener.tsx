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
const MIN_WAKE_FRAMES = Math.ceil(160 / LIVE_FRAME_MS)
const MAX_WAKE_FRAMES = Math.ceil(5800 / LIVE_FRAME_MS)
const MAX_STANDBY_SENSITIVITY = 0.65
const MIN_VOICED_WAKE_FRAMES = 6
const MIN_VOICED_FRAME_RMS = 0.02
const MIN_WAKE_PEAK_RMS = 0.025

function frameRms(frame: Int16Array): number {
  if (frame.length === 0) return 0
  let sumSq = 0
  for (let i = 0; i < frame.length; i += 1) {
    const norm = frame[i] / 32768
    sumSq += norm * norm
  }
  return Math.sqrt(sumSq / frame.length)
}

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
  const vadRef = useRef<EnergyVad | null>(null)
  const runtimeRef = useRef({
    caps,
    companionName,
    prefs,
  })
  useEffect(() => {
    runtimeRef.current = {
      caps,
      companionName,
      prefs,
    }
  }, [caps, companionName, prefs])

  useEffect(() => onLiveVoicePrefsChange(setPrefs), [])
  useEffect(() => onLiveVoiceStageOpenChange(setStageOpen), [])

  useEffect(() => {
    const effectiveSensitivity = Math.min(prefs.sensitivity, MAX_STANDBY_SENSITIVITY)
    vadRef.current?.setSensitivity(effectiveSensitivity)
  }, [prefs.sensitivity])

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

    const effectiveSensitivity = Math.min(
      runtimeRef.current.prefs.sensitivity,
      MAX_STANDBY_SENSITIVITY,
    )
    const vad = new EnergyVad({
      sensitivity: effectiveSensitivity,
      endSilenceMs: 420,
      startSpeechMs: 160,
      preRollMs: 280,
      maxUtteranceMs: 5000,
    })
    vadRef.current = vad

    const releaseStandbyMic = () => {
      micInstance?.close()
      micInstance = null
      browserRec?.stop()
      browserRec = null
    }

    const inspectSpeechFrames = async (frames: Int16Array[]) => {
      if (cancelled || checkingRef.current || isLiveVoiceStageOpen()) return
      if (frames.length < MIN_WAKE_FRAMES || frames.length > MAX_WAKE_FRAMES) return

      let voicedFrames = 0
      let peakRms = 0
      for (const frame of frames) {
        const rms = frameRms(frame)
        if (rms >= MIN_VOICED_FRAME_RMS) {
          voicedFrames += 1
        }
        if (rms > peakRms) {
          peakRms = rms
        }
      }
      if (voicedFrames < MIN_VOICED_WAKE_FRAMES || peakRms < MIN_WAKE_PEAK_RMS) return

      checkingRef.current = true
      try {
        const {
          caps: currentCaps,
          companionName: currentCompanionName,
          prefs: currentPrefs,
        } = runtimeRef.current
        const effectiveStt = resolveStt(currentPrefs.stt, currentCaps, browserSupported)
        const sttProvider =
          effectiveStt && effectiveStt !== 'browser' ? effectiveStt : 'auto'
        const sttModel =
          sttProvider !== 'auto' ? sttModelFor(currentPrefs, currentCaps, sttProvider) : null

        const res = await checkWakeWord(client, {
          pcmBase64: pcm16ToBase64(frames),
          sampleRate: 16000,
          sttProvider,
          sttModel,
          customNames: currentCompanionName !== 'Orin' ? [currentCompanionName] : [],
        })
        if (cancelled || isLiveVoiceStageOpen()) return
        const localCheck = res.transcript
          ? matchWakeWord(res.transcript, [currentCompanionName])
          : null
        if (res.matched && res.confidence >= 0.75) {
          const remainder = localCheck?.matched
            ? localCheck.commandRemainder
            : res.command_remainder
          releaseStandbyMic()
          await triggerWakeIntoFriendLive(remainder)
          return
        }
        if (localCheck && localCheck.matched && localCheck.confidence >= 0.75) {
          releaseStandbyMic()
          await triggerWakeIntoFriendLive(localCheck.commandRemainder)
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

    const initialStt = resolveStt(
      runtimeRef.current.prefs.stt,
      runtimeRef.current.caps,
      browserSupported,
    )
    if (initialStt === 'browser' && browserSupported) {
      browserRec = new BrowserRecognition(
        {
          onInterim: () => undefined,
          onFinal: (text: string) => {
            if (cancelled || isLiveVoiceStageOpen()) return
            const matched = matchWakeWord(text, [runtimeRef.current.companionName])
            if (matched.matched && matched.confidence >= 0.75) {
              releaseStandbyMic()
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
      vadRef.current = null
      releaseStandbyMic()
    }
  }, [
    client,
    prefs.micDeviceId,
    prefs.wakeWordEnabled,
    stageOpen,
    triggerWakeIntoFriendLive,
  ])

  return null
}

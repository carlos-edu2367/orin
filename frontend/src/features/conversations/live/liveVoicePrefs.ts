import type { LiveSttEngineId, LiveVoiceCapabilities } from '../../../api/liveVoice'

/** Who turns your voice into text. `auto` picks the best engine that can actually run. */
export type SttChoice = 'auto' | LiveSttEngineId | 'browser'
/** Who gives the companion its voice. */
export type TtsChoice = 'auto' | 'system' | 'openai'

export type LiveVoicePrefs = {
  micDeviceId: string
  stt: SttChoice
  tts: TtsChoice
  systemVoiceUri: string | null
  openaiVoice: string
  rate: number
  pitch: number
  /** 0 (ignores quiet sounds) .. 1 (hears a whisper). */
  sensitivity: number
  /** Silence that ends your sentence, in ms. */
  endSilenceMs: number
  /** Speaking over the companion interrupts it. */
  bargeIn: boolean
}

export const DEFAULT_LIVE_VOICE_PREFS: LiveVoicePrefs = {
  micDeviceId: 'default',
  stt: 'auto',
  tts: 'auto',
  systemVoiceUri: null,
  openaiVoice: 'coral',
  rate: 1.05,
  pitch: 1,
  sensitivity: 0.6,
  endSilenceMs: 800,
  bargeIn: true,
}

const STORAGE_KEY = 'orin.live-voice.prefs.v1'
const PREFS_EVENT = 'orin:live-voice-prefs'

function clamp(value: unknown, min: number, max: number, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback
}

export function normalizeLiveVoicePrefs(raw: unknown): LiveVoicePrefs {
  const data = typeof raw === 'object' && raw !== null ? (raw as Record<string, unknown>) : {}
  const stt = data.stt
  const tts = data.tts
  return {
    micDeviceId: typeof data.micDeviceId === 'string' && data.micDeviceId ? data.micDeviceId : 'default',
    stt: stt === 'local_sidecar' || stt === 'openai' || stt === 'groq' || stt === 'browser' ? stt : 'auto',
    tts: tts === 'system' || tts === 'openai' ? tts : 'auto',
    systemVoiceUri: typeof data.systemVoiceUri === 'string' ? data.systemVoiceUri : null,
    openaiVoice: typeof data.openaiVoice === 'string' && data.openaiVoice ? data.openaiVoice : 'coral',
    rate: clamp(data.rate, 0.6, 1.6, DEFAULT_LIVE_VOICE_PREFS.rate),
    pitch: clamp(data.pitch, 0.5, 1.6, DEFAULT_LIVE_VOICE_PREFS.pitch),
    sensitivity: clamp(data.sensitivity, 0, 1, DEFAULT_LIVE_VOICE_PREFS.sensitivity),
    endSilenceMs: clamp(data.endSilenceMs, 400, 2000, DEFAULT_LIVE_VOICE_PREFS.endSilenceMs),
    bargeIn: typeof data.bargeIn === 'boolean' ? data.bargeIn : true,
  }
}

export function loadLiveVoicePrefs(): LiveVoicePrefs {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    return normalizeLiveVoicePrefs(raw ? JSON.parse(raw) : null)
  } catch {
    return { ...DEFAULT_LIVE_VOICE_PREFS }
  }
}

export function saveLiveVoicePrefs(prefs: LiveVoicePrefs): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs))
  } catch {
    // Private window or blocked storage: the choice lasts for this page only.
  }
  try {
    window.dispatchEvent(new CustomEvent(PREFS_EVENT, { detail: prefs }))
  } catch {
    // No window (tests).
  }
}

export function onLiveVoicePrefsChange(listener: (prefs: LiveVoicePrefs) => void): () => void {
  const handler = (event: Event) => listener(normalizeLiveVoicePrefs((event as CustomEvent).detail))
  window.addEventListener(PREFS_EVENT, handler)
  return () => window.removeEventListener(PREFS_EVENT, handler)
}

export type ResolvedStt = LiveSttEngineId | 'browser' | null

export function resolveStt(choice: SttChoice, caps: LiveVoiceCapabilities, browserSupported: boolean): ResolvedStt {
  if (choice === 'browser') return browserSupported ? 'browser' : null
  if (choice !== 'auto') return caps.stt[choice].available ? choice : null
  if (caps.stt.local_sidecar.available) return 'local_sidecar'
  if (caps.stt.openai.available) return 'openai'
  if (caps.stt.groq.available) return 'groq'
  return browserSupported ? 'browser' : null
}

export function resolveTts(choice: TtsChoice, caps: LiveVoiceCapabilities): 'system' | 'openai' {
  if (choice === 'openai') return caps.tts.openai.available ? 'openai' : 'system'
  if (choice === 'system') return 'system'
  return caps.tts.openai.available ? 'openai' : 'system'
}

export const STT_LABELS: Record<LiveSttEngineId | 'browser', string> = {
  local_sidecar: 'Whisper local',
  openai: 'OpenAI',
  groq: 'Groq',
  browser: 'Navegador',
}

import type { LiveSttEngineId, LiveTtsEngineId, LiveVoiceCapabilities, LiveVoiceModel } from '../../../api/liveVoice'

/** Who turns your voice into text. `auto` picks the best engine that can actually run. */
export type SttChoice = 'auto' | LiveSttEngineId | 'browser'
/** Who gives the companion its voice. */
export type TtsChoice = 'auto' | 'system' | LiveTtsEngineId
export type TtsEngine = 'system' | LiveTtsEngineId

export type LiveVoicePrefs = {
  micDeviceId: string
  stt: SttChoice
  /** Chosen model per transcription engine (OpenRouter has many). */
  sttModels: Partial<Record<LiveSttEngineId, string>>
  tts: TtsChoice
  /** Chosen model per voice engine. */
  ttsModels: Partial<Record<LiveTtsEngineId, string>>
  /** Chosen voice per `engine:model` — each model has its own voices. */
  ttsVoices: Record<string, string>
  systemVoiceUri: string | null
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
  sttModels: {},
  tts: 'auto',
  ttsModels: {},
  ttsVoices: {},
  systemVoiceUri: null,
  rate: 1.05,
  pitch: 1,
  sensitivity: 0.6,
  endSilenceMs: 600,
  bargeIn: true,
}

const STORAGE_KEY = 'orin.live-voice.prefs.v1'
const PREFS_EVENT = 'orin:live-voice-prefs'
const STT_CHOICES: SttChoice[] = ['auto', 'local_sidecar', 'openai', 'openrouter', 'groq', 'browser']
const TTS_CHOICES: TtsChoice[] = ['auto', 'system', 'openai', 'openrouter', 'kokoro', 'chatterbox']

const LEGACY_ENGLISH_ONLY_OPENROUTER_MODELS: Record<string, string> = {
  'elevenlabs/eleven-flash-v2': 'elevenlabs/eleven-flash-v2.5',
  'elevenlabs/eleven-turbo-v2': 'elevenlabs/eleven-turbo-v2.5',
}

const PREFERRED_OPENROUTER_TTS_MODELS: readonly string[] = [
  'elevenlabs/eleven-multilingual-v2',
  'elevenlabs/eleven-v3',
  'elevenlabs/eleven-flash-v2.5',
  'elevenlabs/eleven-turbo-v2.5',
  'openai/gpt-4o-mini-tts',
  'google/gemini-2.5-flash-preview-tts',
]

function clamp(value: unknown, min: number, max: number, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback
}

function stringMap(value: unknown): Record<string, string> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return {}
  return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === 'string'))
}

export function normalizeLiveVoicePrefs(raw: unknown): LiveVoicePrefs {
  const data = typeof raw === 'object' && raw !== null ? (raw as Record<string, unknown>) : {}
  const voices = stringMap(data.ttsVoices)
  const ttsModels = stringMap(data.ttsModels)
  // v1 kept a single OpenAI voice; it becomes that model's voice.
  if (typeof data.openaiVoice === 'string' && !voices['openai:gpt-4o-mini-tts']) voices['openai:gpt-4o-mini-tts'] = data.openaiVoice
  if (typeof data.openrouterModel === 'string' && data.openrouterModel && !ttsModels.openrouter) {
    ttsModels.openrouter = data.openrouterModel
  }
  if (ttsModels.openrouter && LEGACY_ENGLISH_ONLY_OPENROUTER_MODELS[ttsModels.openrouter]) {
    const oldModel = ttsModels.openrouter
    const newModel = LEGACY_ENGLISH_ONLY_OPENROUTER_MODELS[oldModel]
    ttsModels.openrouter = newModel
    if (voices[`openrouter:${oldModel}`] && !voices[`openrouter:${newModel}`]) {
      voices[`openrouter:${newModel}`] = voices[`openrouter:${oldModel}`]
    }
  }
  return {
    micDeviceId: typeof data.micDeviceId === 'string' && data.micDeviceId ? data.micDeviceId : 'default',
    stt: STT_CHOICES.find((choice) => choice === data.stt) ?? 'auto',
    sttModels: stringMap(data.sttModels),
    tts: TTS_CHOICES.find((choice) => choice === data.tts) ?? 'auto',
    ttsModels,
    ttsVoices: voices,
    systemVoiceUri: typeof data.systemVoiceUri === 'string' ? data.systemVoiceUri : null,
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
  for (const engine of ['local_sidecar', 'openai', 'openrouter', 'groq'] as const) {
    if (caps.stt[engine].available) return engine
  }
  return browserSupported ? 'browser' : null
}

export function resolveTts(choice: TtsChoice, caps: LiveVoiceCapabilities): TtsEngine {
  if (choice === 'system') return 'system'
  if (choice !== 'auto') return caps.tts[choice].available ? choice : 'system'
  return caps.recommendedTts !== 'browser' && caps.tts[caps.recommendedTts].available ? caps.recommendedTts : 'system'
}

const DEFAULT_VOICE: Partial<Record<LiveTtsEngineId, string>> = { kokoro: 'pf_dora', chatterbox: 'default', openai: 'coral' }

export function chooseDefaultOpenRouterModel(
  models: readonly LiveVoiceModel[],
  preferredModelId?: string | null,
): LiveVoiceModel | null {
  if (preferredModelId && !(preferredModelId in LEGACY_ENGLISH_ONLY_OPENROUTER_MODELS)) {
    const preferred = models.find((item) => item.id === preferredModelId)
    if (preferred) return preferred
  }
  for (const candidateId of PREFERRED_OPENROUTER_TTS_MODELS) {
    const match = models.find((item) => item.id === candidateId)
    if (match) return match
  }
  const nonEnglishOnly = models.find((item) => !(item.id in LEGACY_ENGLISH_ONLY_OPENROUTER_MODELS))
  return nonEnglishOnly ?? models[0] ?? null
}

export function chooseDefaultVoiceForModel(engine: LiveTtsEngineId, voices: readonly string[]): string {
  const fallback = DEFAULT_VOICE[engine]
  if (fallback && (voices.length === 0 || voices.includes(fallback))) {
    return fallback
  }
  const ptMatch = voices.find((v) => PORTUGUESE_VOICE.test(v))
  if (ptMatch) return ptMatch
  for (const preferred of ['sarah', 'aria', 'coral', 'nova', 'brian']) {
    const match = voices.find((v) => v.toLowerCase() === preferred)
    if (match) return match
  }
  return sortVoicesForPortuguese(voices)[0] ?? ''
}

/** The model and voice an engine will use: the person's pick when it still exists, a sensible default otherwise. */
export function ttsSelection(
  prefs: LiveVoicePrefs,
  caps: LiveVoiceCapabilities,
  engine: LiveTtsEngineId,
): { model: LiveVoiceModel | null; modelId: string; voice: string } {
  const status = caps.tts[engine]
  const rawWanted = prefs.ttsModels[engine]
  const wanted =
    engine === 'openrouter' && rawWanted && LEGACY_ENGLISH_ONLY_OPENROUTER_MODELS[rawWanted]
      ? LEGACY_ENGLISH_ONLY_OPENROUTER_MODELS[rawWanted]
      : rawWanted
  const defaultModel =
    engine === 'openrouter'
      ? chooseDefaultOpenRouterModel(status.models, status.modelId)
      : status.models.find((item) => item.id === status.modelId) ?? status.models[0] ?? null
  const model = status.models.find((item) => item.id === wanted) ?? defaultModel
  const fallbackStatusModelId =
    engine === 'openrouter' && status.modelId && LEGACY_ENGLISH_ONLY_OPENROUTER_MODELS[status.modelId]
      ? LEGACY_ENGLISH_ONLY_OPENROUTER_MODELS[status.modelId]
      : status.modelId
  const modelId = model?.id ?? wanted ?? fallbackStatusModelId ?? ''
  const voices = model?.voices.length ? model.voices : status.voices
  const chosen = prefs.ttsVoices[`${engine}:${modelId}`]
  const voice = chosen && (voices.length === 0 || voices.includes(chosen))
    ? chosen
    : chooseDefaultVoiceForModel(engine, voices)
  return { model, modelId, voice }
}

export function sttModelFor(prefs: LiveVoicePrefs, caps: LiveVoiceCapabilities, engine: LiveSttEngineId): string | null {
  const status = caps.stt[engine]
  const wanted = prefs.sttModels[engine]
  if (wanted && (status.models.length === 0 || status.models.some((item) => item.id === wanted))) return wanted
  return status.modelId
}

const PORTUGUESE_VOICE = /(^p[fm]_)|pt[-_]?br|portugu|brazil|brasil/i

/** Portuguese voices first; otherwise the provider's own order. */
export function sortVoicesForPortuguese(voices: readonly string[]): string[] {
  return [...voices].sort((a, b) => Number(PORTUGUESE_VOICE.test(b)) - Number(PORTUGUESE_VOICE.test(a)))
}

const KOKORO_LANGS: Record<string, string> = {
  a: 'inglês EUA', b: 'inglês UK', e: 'espanhol', f: 'francês', h: 'hindi', i: 'italiano', j: 'japonês', p: 'português BR', z: 'chinês',
}

export function voiceLabel(engine: TtsEngine, voice: string): string {
  if (engine === 'kokoro') {
    const match = /^([a-z])([fm])_(.+)$/.exec(voice)
    if (match) {
      const name = match[3].charAt(0).toUpperCase() + match[3].slice(1)
      return `${name} · ${KOKORO_LANGS[match[1]] ?? match[1]} · ${match[2] === 'f' ? 'feminina' : 'masculina'}`
    }
  }
  if (engine === 'openrouter' || engine === 'openai') {
    const bare = voice.replace(/:.*$/, '')
    return /^[a-z]+$/.test(bare) ? bare.charAt(0).toUpperCase() + bare.slice(1) : bare.replace(/_/g, ' ')
  }
  return voice
}

export const STT_LABELS: Record<LiveSttEngineId | 'browser', string> = {
  local_sidecar: 'Whisper local',
  openai: 'OpenAI',
  openrouter: 'OpenRouter',
  groq: 'Groq',
  browser: 'Navegador',
}

export const TTS_LABELS: Record<TtsEngine, string> = {
  system: 'Voz do sistema',
  kokoro: 'Kokoro',
  chatterbox: 'Chatterbox',
  openai: 'OpenAI',
  openrouter: 'OpenRouter',
}

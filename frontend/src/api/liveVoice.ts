import type { ApiClient } from './client'

export type LiveSttEngineId = 'local_sidecar' | 'openai' | 'openrouter' | 'groq'
export type LiveTtsEngineId = 'openai' | 'openrouter' | 'kokoro' | 'chatterbox'
export type LocalEngineId = 'whisper' | 'kokoro' | 'chatterbox'

export type LiveVoiceModel = {
  id: string
  name: string
  vendor: string
  /** Voices this model accepts; empty when the model has no fixed list. */
  voices: string[]
  supportsInstructions: boolean
}

export type EngineInstallStatus = {
  engine: LocalEngineId
  label: string
  installed: boolean
  state: 'idle' | 'running' | 'installed' | 'failed'
  step: string
  message: string
  device: string | null
  sizeHint: string
  gpu: boolean
}

export type LiveEngineStatus = {
  available: boolean
  reason: string
  modelId: string | null
  device?: string | null
  models: LiveVoiceModel[]
  voices: string[]
  install?: EngineInstallStatus
  voiceClips?: Array<{ id: string; name: string }>
}

export type LiveVoiceCapabilities = {
  stt: Record<LiveSttEngineId, LiveEngineStatus>
  tts: Record<LiveTtsEngineId, LiveEngineStatus>
  recommendedStt: LiveSttEngineId | 'browser'
  recommendedTts: LiveTtsEngineId | 'browser'
  hardware: { hasCuda: boolean; deviceName: string | null; freeVramGb: number; totalVramGb: number }
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

export function parseEngineInstall(value: unknown): EngineInstallStatus {
  const data = record(value)
  const state = data.state === 'running' || data.state === 'installed' || data.state === 'failed' ? data.state : 'idle'
  const engine = data.engine === 'kokoro' || data.engine === 'chatterbox' ? data.engine : 'whisper'
  return {
    engine,
    label: typeof data.label === 'string' ? data.label : engine,
    installed: data.installed === true,
    state,
    step: typeof data.step === 'string' ? data.step : '',
    message: typeof data.message === 'string' ? data.message : '',
    device: typeof data.device === 'string' ? data.device : null,
    sizeHint: typeof data.size_hint === 'string' ? data.size_hint : '',
    gpu: data.gpu === true,
  }
}

function model(value: unknown): LiveVoiceModel | null {
  const data = record(value)
  if (typeof data.id !== 'string' || !data.id) return null
  return {
    id: data.id,
    name: typeof data.name === 'string' && data.name ? data.name : data.id,
    vendor: typeof data.vendor === 'string' ? data.vendor : data.id.split('/')[0] ?? '',
    voices: strings(data.voices),
    supportsInstructions: data.supports_instructions === true,
  }
}

function engine(value: unknown): LiveEngineStatus {
  const data = record(value)
  const clips = Array.isArray(data.voice_clips)
    ? data.voice_clips
        .map(record)
        .filter((clip) => typeof clip.id === 'string')
        .map((clip) => ({ id: String(clip.id), name: typeof clip.name === 'string' ? clip.name : String(clip.id) }))
    : undefined
  return {
    available: data.available === true,
    reason: typeof data.reason === 'string' ? data.reason : '',
    modelId: typeof data.model_id === 'string' ? data.model_id : null,
    device: typeof data.device === 'string' ? data.device : null,
    models: Array.isArray(data.models) ? data.models.map(model).filter((item): item is LiveVoiceModel => item !== null) : [],
    voices: strings(data.voices),
    install: data.install ? parseEngineInstall(data.install) : undefined,
    voiceClips: clips,
  }
}

const EMPTY: LiveEngineStatus = { available: false, reason: '', modelId: null, models: [], voices: [] }

export const UNAVAILABLE_CAPABILITIES: LiveVoiceCapabilities = {
  stt: { local_sidecar: EMPTY, openai: EMPTY, openrouter: EMPTY, groq: EMPTY },
  tts: { openai: EMPTY, openrouter: EMPTY, kokoro: EMPTY, chatterbox: EMPTY },
  recommendedStt: 'browser',
  recommendedTts: 'browser',
  hardware: { hasCuda: false, deviceName: null, freeVramGb: 0, totalVramGb: 0 },
}

const STT_IDS: LiveSttEngineId[] = ['local_sidecar', 'openai', 'openrouter', 'groq']
const TTS_IDS: LiveTtsEngineId[] = ['openai', 'openrouter', 'kokoro', 'chatterbox']

export function parseLiveVoiceCapabilities(value: unknown): LiveVoiceCapabilities {
  const data = record(value)
  const stt = record(data.stt)
  const tts = record(data.tts)
  const hardware = record(data.hardware)
  const recommendedStt = STT_IDS.find((id) => id === data.recommended_stt) ?? 'browser'
  const recommendedTts = TTS_IDS.find((id) => id === data.recommended_tts) ?? 'browser'
  return {
    stt: Object.fromEntries(STT_IDS.map((id) => [id, engine(stt[id])])) as Record<LiveSttEngineId, LiveEngineStatus>,
    tts: Object.fromEntries(TTS_IDS.map((id) => [id, engine(tts[id])])) as Record<LiveTtsEngineId, LiveEngineStatus>,
    recommendedStt,
    recommendedTts,
    hardware: {
      hasCuda: hardware.has_cuda === true,
      deviceName: typeof hardware.device_name === 'string' ? hardware.device_name : null,
      freeVramGb: typeof hardware.free_vram_gb === 'number' ? hardware.free_vram_gb : 0,
      totalVramGb: typeof hardware.total_vram_gb === 'number' ? hardware.total_vram_gb : 0,
    },
  }
}

export function getLiveVoiceCapabilities(client: ApiClient, signal?: AbortSignal): Promise<LiveVoiceCapabilities> {
  return client.request({ path: '/v1/live-voice/capabilities', signal, parse: parseLiveVoiceCapabilities })
}

export function startEngineInstall(client: ApiClient, engineId: LocalEngineId): Promise<EngineInstallStatus> {
  return client.request({
    path: `/v1/live-voice/engines/${engineId}/install`,
    method: 'POST',
    body: {},
    expectedStatus: 202,
    intent: client.createMutationIntent(),
    parse: parseEngineInstall,
  })
}

export function uploadChatterboxVoice(
  client: ApiClient,
  input: { name: string; filename: string; dataBase64: string },
): Promise<{ id: string; name: string }> {
  return client.request({
    path: '/v1/live-voice/engines/chatterbox/voices',
    method: 'POST',
    body: { name: input.name, filename: input.filename, data_base64: input.dataBase64 },
    expectedStatus: 201,
    intent: client.createMutationIntent(),
    parse: (value) => {
      const data = record(value)
      return { id: String(data.id ?? ''), name: String(data.name ?? '') }
    },
  })
}

export function deleteChatterboxVoice(client: ApiClient, clipId: string): Promise<void> {
  return client.request({
    path: `/v1/live-voice/engines/chatterbox/voices/${encodeURIComponent(clipId)}`,
    method: 'DELETE',
    intent: client.createMutationIntent(),
    parse: () => undefined,
  })
}

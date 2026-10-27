import type { ApiClient } from './client'

export type LiveSttEngineId = 'local_sidecar' | 'openai' | 'groq'

export type LiveEngineStatus = {
  available: boolean
  reason: string
  modelId: string | null
  device?: string | null
  installCommand?: string | null
}

export type LiveVoiceCapabilities = {
  stt: Record<LiveSttEngineId, LiveEngineStatus>
  tts: { openai: LiveEngineStatus & { voices: string[] } }
  recommendedStt: LiveSttEngineId | 'browser'
  recommendedTts: 'openai' | 'browser'
  hardware: { hasCuda: boolean; deviceName: string | null; freeVramGb: number; totalVramGb: number }
}

export type LocalWhisperInstallStatus = {
  state: 'idle' | 'running' | 'installed' | 'failed'
  message: string
  command: string
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

function engine(value: unknown): LiveEngineStatus {
  const data = record(value)
  return {
    available: data.available === true,
    reason: typeof data.reason === 'string' ? data.reason : '',
    modelId: typeof data.model_id === 'string' ? data.model_id : null,
    device: typeof data.device === 'string' ? data.device : null,
    installCommand: typeof data.install_command === 'string' ? data.install_command : null,
  }
}

export const UNAVAILABLE_CAPABILITIES: LiveVoiceCapabilities = {
  stt: {
    local_sidecar: { available: false, reason: '', modelId: null },
    openai: { available: false, reason: '', modelId: null },
    groq: { available: false, reason: '', modelId: null },
  },
  tts: { openai: { available: false, reason: '', modelId: null, voices: [] } },
  recommendedStt: 'browser',
  recommendedTts: 'browser',
  hardware: { hasCuda: false, deviceName: null, freeVramGb: 0, totalVramGb: 0 },
}

export function parseLiveVoiceCapabilities(value: unknown): LiveVoiceCapabilities {
  const data = record(value)
  const stt = record(data.stt)
  const tts = record(data.tts)
  const openaiTts = record(tts.openai)
  const hardware = record(data.hardware)
  const recommendedStt = data.recommended_stt
  return {
    stt: { local_sidecar: engine(stt.local_sidecar), openai: engine(stt.openai), groq: engine(stt.groq) },
    tts: {
      openai: {
        ...engine(openaiTts),
        voices: Array.isArray(openaiTts.voices) ? openaiTts.voices.filter((v): v is string => typeof v === 'string') : [],
      },
    },
    recommendedStt:
      recommendedStt === 'local_sidecar' || recommendedStt === 'openai' || recommendedStt === 'groq' ? recommendedStt : 'browser',
    recommendedTts: data.recommended_tts === 'openai' ? 'openai' : 'browser',
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

function parseInstallStatus(value: unknown): LocalWhisperInstallStatus {
  const data = record(value)
  const state = data.state === 'running' || data.state === 'installed' || data.state === 'failed' ? data.state : 'idle'
  return {
    state,
    message: typeof data.message === 'string' ? data.message : '',
    command: typeof data.command === 'string' ? data.command : 'uv pip install faster-whisper',
  }
}

export function getLocalWhisperInstall(client: ApiClient, signal?: AbortSignal): Promise<LocalWhisperInstallStatus> {
  return client.request({ path: '/v1/live-voice/local-whisper/install', signal, parse: parseInstallStatus })
}

export function startLocalWhisperInstall(client: ApiClient): Promise<LocalWhisperInstallStatus> {
  return client.request({
    path: '/v1/live-voice/local-whisper/install',
    method: 'POST',
    body: {},
    expectedStatus: 202,
    intent: client.createMutationIntent(),
    parse: parseInstallStatus,
  })
}

/**
 * Microphone capture for the Live voice mode.
 *
 * Audio is taken from `getUserMedia` (with the browser's echo cancellation and
 * noise suppression), measured for the level meter, resampled to 16 kHz mono
 * PCM16 in 32 ms frames, and segmented into utterances by an adaptive energy
 * VAD. Nothing here knows about the server: callers decide where frames go.
 */

export const LIVE_SAMPLE_RATE = 16000
export const LIVE_FRAME_SAMPLES = 512
export const LIVE_FRAME_MS = (LIVE_FRAME_SAMPLES / LIVE_SAMPLE_RATE) * 1000

export type MicPermissionState = 'granted' | 'denied' | 'unavailable' | 'error'

export class MicAccessError extends Error {
  constructor(readonly state: MicPermissionState, message: string) {
    super(message)
    this.name = 'MicAccessError'
  }
}

/** RMS (0..1) mapped onto a perceptual 0..1 meter: -60 dBFS is silence, -12 dBFS is full. */
export function levelFromRms(rms: number): number {
  if (!Number.isFinite(rms) || rms <= 0) return 0
  const db = 20 * Math.log10(rms)
  return Math.max(0, Math.min(1, (db + 60) / 48))
}

export function rmsOf(samples: ArrayLike<number>): number {
  if (samples.length === 0) return 0
  let sum = 0
  for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i]
  return Math.sqrt(sum / samples.length)
}

/** Linear resampler that keeps fractional position across calls, so frame edges stay continuous. */
export class StreamingResampler {
  private position = 0
  private last = 0

  constructor(private readonly fromRate: number, private readonly toRate: number = LIVE_SAMPLE_RATE) {}

  process(input: Float32Array): Float32Array {
    if (this.fromRate === this.toRate) return input.slice()
    if (input.length === 0) return new Float32Array(0)
    const step = this.fromRate / this.toRate
    const out: number[] = []
    // Positions are in input coordinates; -1 is the last sample of the previous block.
    const at = (index: number) => (index < 0 ? this.last : input[index])
    let pos = this.position
    while (Math.floor(pos) + 1 < input.length) {
      const index = Math.floor(pos)
      const frac = pos - index
      out.push(at(index) + (at(index + 1) - at(index)) * frac)
      pos += step
    }
    this.position = pos - input.length
    this.last = input[input.length - 1]
    return Float32Array.from(out)
  }
}

export function floatToPcm16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length)
  for (let i = 0; i < samples.length; i += 1) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff
  }
  return out
}

export type VadOptions = {
  /** 0 (deaf) .. 1 (hears a whisper). */
  sensitivity?: number
  /** Silence that closes an utterance. */
  endSilenceMs?: number
  /** Voice needed before an utterance opens. */
  startSpeechMs?: number
  /** Audio kept from before the start, so first syllables are not clipped. */
  preRollMs?: number
  maxUtteranceMs?: number
}

export type VadEvent =
  | { type: 'speech_start'; preRoll: Int16Array[] }
  | { type: 'speech_frame'; frame: Int16Array }
  | { type: 'speech_end'; frames: Int16Array[]; durationMs: number; silenceMs: number }

export const DUCKING_MULTIPLIER = 3.5
export const DUCKING_MIN_SPEECH_MS = 420

/**
 * Adaptive energy VAD. The noise floor follows the room quickly downwards and
 * slowly upwards; while the assistant speaks ("ducking") the bar is raised so
 * its own voice leaking from the speakers does not open an utterance.
 */
export class EnergyVad {
  private floor = 0.004
  private voicedMs = 0
  private silentMs = 0
  private inSpeech = false
  private speechMs = 0
  private frames: Int16Array[] = []
  private preRoll: Int16Array[] = []
  private ducking = false
  private readonly opts: Required<VadOptions>

  constructor(options: VadOptions = {}) {
    this.opts = {
      sensitivity: options.sensitivity ?? 0.6,
      endSilenceMs: options.endSilenceMs ?? 600,
      startSpeechMs: options.startSpeechMs ?? 96,
      preRollMs: options.preRollMs ?? 320,
      maxUtteranceMs: options.maxUtteranceMs ?? 28000,
    }
  }

  setDucking(ducking: boolean): void {
    this.ducking = ducking
  }

  setSensitivity(sensitivity: number): void {
    this.opts.sensitivity = Math.max(0, Math.min(1, sensitivity))
  }

  get speaking(): boolean {
    return this.inSpeech
  }

  threshold(): number {
    const minimum = 0.004 + (1 - this.opts.sensitivity) * 0.03
    const base = Math.max(this.floor * 3.2, minimum)
    return this.ducking ? base * DUCKING_MULTIPLIER : base
  }

  reset(): void {
    this.inSpeech = false
    this.voicedMs = 0
    this.silentMs = 0
    this.speechMs = 0
    this.frames = []
    this.preRoll = []
  }

  /** Close an open utterance now (push-to-talk released). */
  flush(): VadEvent | null {
    if (!this.inSpeech) {
      this.voicedMs = 0
      return null
    }
    const event: VadEvent = { type: 'speech_end', frames: this.frames, durationMs: this.speechMs, silenceMs: this.opts.endSilenceMs }
    this.inSpeech = false
    this.voicedMs = 0
    this.frames = []
    return event
  }

  process(frame: Int16Array, rms: number): VadEvent[] {
    const events: VadEvent[] = []
    const threshold = this.threshold()
    const voiced = rms >= threshold
    if (!this.inSpeech) {
      if (rms < this.floor) this.floor = this.floor * 0.9 + rms * 0.1
      else this.floor = this.floor * 0.995 + rms * 0.005
      this.floor = Math.max(0.0008, Math.min(this.floor, 0.05))
    }

    if (!this.inSpeech) {
      this.preRoll.push(frame)
      const maxPreRoll = Math.ceil(this.opts.preRollMs / LIVE_FRAME_MS)
      if (this.preRoll.length > maxPreRoll) this.preRoll.shift()
      this.voicedMs = voiced ? this.voicedMs + LIVE_FRAME_MS : 0
      const needed = this.ducking ? Math.max(this.opts.startSpeechMs, DUCKING_MIN_SPEECH_MS) : this.opts.startSpeechMs
      if (this.voicedMs >= needed) {
        this.inSpeech = true
        this.silentMs = 0
        this.frames = [...this.preRoll]
        this.speechMs = this.frames.length * LIVE_FRAME_MS
        events.push({ type: 'speech_start', preRoll: [...this.preRoll] })
        this.preRoll = []
      }
      return events
    }

    this.frames.push(frame)
    this.speechMs += LIVE_FRAME_MS
    events.push({ type: 'speech_frame', frame })
    // Trailing syllables are quieter than onsets, so the bar to stay "in speech" is lower.
    this.silentMs = rms >= threshold * 0.65 ? 0 : this.silentMs + LIVE_FRAME_MS
    if (this.silentMs >= this.opts.endSilenceMs || this.speechMs >= this.opts.maxUtteranceMs) {
      events.push({ type: 'speech_end', frames: this.frames, durationMs: this.speechMs, silenceMs: this.silentMs })
      this.inSpeech = false
      this.voicedMs = 0
      this.frames = []
    }
    return events
  }
}

export function concatPcm(frames: Int16Array[]): Int16Array {
  const total = frames.reduce((sum, frame) => sum + frame.length, 0)
  const out = new Int16Array(total)
  let offset = 0
  for (const frame of frames) {
    out.set(frame, offset)
    offset += frame.length
  }
  return out
}

const WORKLET_SOURCE = `
class OrinMicTap extends AudioWorkletProcessor {
  constructor() { super(); this.buffer = new Float32Array(1024); this.length = 0 }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0]
    if (!channel) return true
    for (let i = 0; i < channel.length; i += 1) {
      this.buffer[this.length++] = channel[i]
      if (this.length === this.buffer.length) {
        this.port.postMessage(this.buffer.slice(0))
        this.length = 0
      }
    }
    return true
  }
}
registerProcessor('orin-mic-tap', OrinMicTap)
`

export type MicCaptureOptions = {
  deviceId?: string
  headphoneMode?: boolean
  /** 16 kHz PCM16 frames of `LIVE_FRAME_SAMPLES`, with their RMS. */
  onFrame?: (frame: Int16Array, rms: number) => void
}

type AudioContextCtor = typeof AudioContext

function audioContextCtor(): AudioContextCtor | null {
  if (typeof window === 'undefined') return null
  const w = window as unknown as { AudioContext?: AudioContextCtor; webkitAudioContext?: AudioContextCtor }
  return w.AudioContext ?? w.webkitAudioContext ?? null
}

export function microphoneSupported(): boolean {
  return typeof navigator !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia) && audioContextCtor() !== null
}

export async function listMicrophones(): Promise<Array<{ deviceId: string; label: string }>> {
  if (typeof navigator === 'undefined' || !navigator.mediaDevices?.enumerateDevices) return []
  const devices = await navigator.mediaDevices.enumerateDevices()
  return devices
    .filter((device) => device.kind === 'audioinput')
    .map((device, index) => ({ deviceId: device.deviceId || `mic-${index}`, label: device.label || `Microfone ${index + 1}` }))
}

export class MicCapture {
  /** Smoothed meter value, 0..1, safe to read every animation frame. */
  level = 0
  private closed = false
  private node: AudioWorkletNode | ScriptProcessorNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private sink: GainNode | null = null
  private pending: number[] = []
  private resampler: StreamingResampler

  private constructor(
    private readonly context: AudioContext,
    private readonly stream: MediaStream,
    private readonly options: MicCaptureOptions,
  ) {
    this.resampler = new StreamingResampler(context.sampleRate)
  }

  static async open(options: MicCaptureOptions = {}): Promise<MicCapture> {
    const Ctor = audioContextCtor()
    if (!microphoneSupported() || !Ctor) {
      throw new MicAccessError('unavailable', 'Este ambiente não oferece acesso ao microfone.')
    }
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          ...(options.deviceId && options.deviceId !== 'default' ? { deviceId: { exact: options.deviceId } } : {}),
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: Boolean(options.headphoneMode),
          channelCount: 1,
        },
      })
    } catch (error) {
      const name = error instanceof DOMException ? error.name : ''
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        throw new MicAccessError('denied', 'O acesso ao microfone foi bloqueado. Libere a permissão e tente de novo.')
      }
      if (name === 'NotFoundError' || name === 'OverconstrainedError') {
        throw new MicAccessError('unavailable', 'Nenhum microfone encontrado com essa configuração.')
      }
      throw new MicAccessError('error', 'Não foi possível abrir o microfone.')
    }
    const context = new Ctor()
    const capture = new MicCapture(context, stream, options)
    await capture.wire()
    return capture
  }

  private async wire(): Promise<void> {
    if (this.context.state === 'suspended') await this.context.resume().catch(() => {})
    this.source = this.context.createMediaStreamSource(this.stream)
    // Processing nodes only run while connected to the destination; a muted
    // gain keeps the graph alive without playing the microphone back.
    this.sink = this.context.createGain()
    this.sink.gain.value = 0
    this.sink.connect(this.context.destination)
    const onSamples = (samples: Float32Array) => this.consume(samples)
    if (this.context.audioWorklet && typeof AudioWorkletNode !== 'undefined') {
      try {
        const url = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: 'application/javascript' }))
        await this.context.audioWorklet.addModule(url)
        URL.revokeObjectURL(url)
        const node = new AudioWorkletNode(this.context, 'orin-mic-tap')
        node.port.onmessage = (event: MessageEvent<Float32Array>) => onSamples(event.data)
        this.source.connect(node)
        node.connect(this.sink)
        this.node = node
        return
      } catch {
        // Fall through to the ScriptProcessor path (older Chromium, strict CSP).
      }
    }
    const processor = this.context.createScriptProcessor(2048, 1, 1)
    processor.onaudioprocess = (event) => onSamples(event.inputBuffer.getChannelData(0).slice())
    this.source.connect(processor)
    processor.connect(this.sink)
    this.node = processor
  }

  private consume(samples: Float32Array): void {
    if (this.closed) return
    const target = levelFromRms(rmsOf(samples))
    // Fast attack, slower release: the meter jumps with a syllable and settles smoothly.
    this.level = target > this.level ? this.level * 0.35 + target * 0.65 : this.level * 0.85 + target * 0.15
    const resampled = this.resampler.process(samples)
    for (let i = 0; i < resampled.length; i += 1) this.pending.push(resampled[i])
    while (this.pending.length >= LIVE_FRAME_SAMPLES) {
      const chunk = Float32Array.from(this.pending.splice(0, LIVE_FRAME_SAMPLES))
      this.options.onFrame?.(floatToPcm16(chunk), rmsOf(chunk))
    }
  }

  get deviceLabel(): string {
    return this.stream.getAudioTracks()[0]?.label ?? ''
  }

  close(): void {
    if (this.closed) return
    this.closed = true
    this.level = 0
    try {
      if (this.node && 'port' in this.node) this.node.port.onmessage = null
      this.node?.disconnect()
      this.source?.disconnect()
      this.sink?.disconnect()
    } catch {
      // Already torn down.
    }
    for (const track of this.stream.getTracks()) track.stop()
    void this.context.close().catch(() => {})
  }
}

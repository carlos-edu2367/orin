/**
 * What the Live voice says out loud.
 *
 * Two engines share one queue: the operating system's own voices through
 * `speechSynthesis` (free, local, always there on Windows/macOS) and PCM chunks
 * synthesized on the server (OpenAI). `level` is the output loudness used to
 * animate the orb — measured for PCM, approximated from word boundaries for
 * the system voice, which exposes no audio samples.
 */

export type SystemVoice = { uri: string; name: string; lang: string; local: boolean }

export function speechSynthesisSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window && typeof window.SpeechSynthesisUtterance === 'function'
}

/** Portuguese voices first (pt-BR before pt-PT), then everything else. */
export function listSystemVoices(): SystemVoice[] {
  if (!speechSynthesisSupported()) return []
  const voices = window.speechSynthesis.getVoices().map((voice) => ({
    uri: voice.voiceURI,
    name: voice.name,
    lang: voice.lang,
    local: voice.localService,
  }))
  const rank = (voice: SystemVoice) => {
    const lang = voice.lang.toLowerCase()
    if (lang === 'pt-br' || lang === 'pt_br') return 0
    if (lang.startsWith('pt')) return 1
    return 2
  }
  return voices.sort((a, b) => rank(a) - rank(b) || Number(b.local) - Number(a.local) || a.name.localeCompare(b.name))
}

export function onSystemVoicesChanged(listener: () => void): () => void {
  if (!speechSynthesisSupported()) return () => {}
  const synth = window.speechSynthesis
  synth.addEventListener?.('voiceschanged', listener)
  return () => synth.removeEventListener?.('voiceschanged', listener)
}

/** Markdown and stage directions are for the screen, not for the voice. */
export function textForSpeech(raw: string): string {
  return raw
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, ' ')
    .replace(/\[(?:hmm|pausa|riso[^\]]*|suspiro|risos?)\]/gi, ' ')
    .replace(/<\/?speak>/gi, ' ')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/[*_~>|#]+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

export type SpeechOutputEvents = {
  onStart?: (text: string) => void
  /** Character offset of the word being spoken in the current text. */
  onProgress?: (text: string, charIndex: number) => void
  onIdle?: () => void
}

export type SystemVoiceSettings = { voiceUri: string | null; rate: number; pitch: number }

type QueueItem =
  | { kind: 'system'; text: string }
  | { kind: 'pcm'; text: string; pcm: Int16Array; sampleRate: number; chunkSeq: number }
  | { kind: 'encoded'; text: string; data: ArrayBuffer; chunkSeq: number }

type AudioContextCtor = typeof AudioContext

export class SpeechOutput {
  level = 0
  private queue: QueueItem[] = []
  private current: QueueItem | null = null
  private context: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private source: AudioBufferSourceNode | null = null
  private startedAt = 0
  private boundaryPulse = 0
  private levelTimer: number | null = null
  private analyserData: Uint8Array<ArrayBuffer> | null = null
  private generation = 0

  constructor(private events: SpeechOutputEvents = {}, private system: SystemVoiceSettings = { voiceUri: null, rate: 1.05, pitch: 1 }) {}

  setEvents(events: SpeechOutputEvents): void {
    this.events = events
  }

  setSystemVoice(settings: SystemVoiceSettings): void {
    this.system = settings
  }

  get speaking(): boolean {
    return this.current !== null || this.queue.length > 0
  }

  get currentText(): string {
    return this.current?.text ?? ''
  }

  /** Chunk sequence and milliseconds played of the PCM chunk now playing, for barge-in receipts. */
  playbackPosition(): { chunkSeq: number; playedMs: number } | null {
    if (!this.current || this.current.kind === 'system' || !this.context) return null
    return { chunkSeq: this.current.chunkSeq, playedMs: Math.max(0, Math.round((this.context.currentTime - this.startedAt) * 1000)) }
  }

  speakSystem(text: string): void {
    const clean = textForSpeech(text)
    if (!clean) return
    this.queue.push({ kind: 'system', text: clean })
    this.pump()
  }

  playPcm(text: string, pcmBase64: string, sampleRate: number, chunkSeq: number): void {
    const pcm = decodePcm16(pcmBase64)
    if (pcm.length === 0) return
    this.queue.push({ kind: 'pcm', text, pcm, sampleRate, chunkSeq })
    this.pump()
  }

  /** MP3/WAV from a cloud voice; the browser decodes it, sample rate included. */
  playEncoded(text: string, base64: string, chunkSeq: number): void {
    const bytes = decodeBase64(base64)
    if (bytes.byteLength === 0) return
    this.queue.push({ kind: 'encoded', text, data: bytes.buffer as ArrayBuffer, chunkSeq })
    this.pump()
  }

  /** Stop at once and drop everything queued (barge-in, mute, close). */
  stop(): void {
    this.generation += 1
    this.queue = []
    const wasSpeaking = this.current !== null
    this.current = null
    if (speechSynthesisSupported()) window.speechSynthesis.cancel()
    try {
      this.source?.stop()
    } catch {
      // Not started yet.
    }
    this.source = null
    this.stopLevelLoop()
    if (wasSpeaking) this.events.onIdle?.()
  }

  dispose(): void {
    this.stop()
    void this.context?.close().catch(() => {})
    this.context = null
  }

  private pump(): void {
    if (this.current || this.queue.length === 0) return
    const item = this.queue.shift()!
    this.current = item
    this.events.onStart?.(item.text)
    this.startLevelLoop()
    const generation = this.generation
    const finish = () => {
      if (generation !== this.generation) return
      this.current = null
      this.source = null
      if (this.queue.length === 0) {
        this.stopLevelLoop()
        this.events.onIdle?.()
      }
      this.pump()
    }
    if (item.kind === 'system') this.playSystem(item.text, finish)
    else if (item.kind === 'pcm') this.playBuffer(item, finish)
    else this.playEncodedItem(item, finish)
  }

  private playSystem(text: string, done: () => void): void {
    if (!speechSynthesisSupported()) {
      done()
      return
    }
    const utterance = new window.SpeechSynthesisUtterance(text)
    const voice = window.speechSynthesis.getVoices().find((candidate) => candidate.voiceURI === this.system.voiceUri)
      ?? window.speechSynthesis.getVoices().find((candidate) => candidate.lang.toLowerCase().startsWith('pt'))
    if (voice) utterance.voice = voice
    utterance.lang = voice?.lang ?? 'pt-BR'
    utterance.rate = this.system.rate
    utterance.pitch = this.system.pitch
    utterance.onboundary = (event) => {
      this.boundaryPulse = 1
      this.events.onProgress?.(text, event.charIndex)
    }
    utterance.onend = done
    utterance.onerror = done
    window.speechSynthesis.speak(utterance)
  }

  private ensureContext(): AudioContext | null {
    if (this.context) return this.context
    const w = window as unknown as { AudioContext?: AudioContextCtor; webkitAudioContext?: AudioContextCtor }
    const Ctor = w.AudioContext ?? w.webkitAudioContext
    if (!Ctor) return null
    const context = new Ctor()
    const analyser = context.createAnalyser()
    analyser.fftSize = 512
    analyser.connect(context.destination)
    this.context = context
    this.analyser = analyser
    this.analyserData = new Uint8Array(new ArrayBuffer(analyser.fftSize))
    return context
  }

  private playBuffer(item: Extract<QueueItem, { kind: 'pcm' }>, done: () => void): void {
    const context = this.ensureContext()
    if (!context || !this.analyser) {
      done()
      return
    }
    const buffer = context.createBuffer(1, item.pcm.length, item.sampleRate)
    const channel = buffer.getChannelData(0)
    for (let i = 0; i < item.pcm.length; i += 1) channel[i] = item.pcm[i] / 0x8000
    this.startBuffer(item, buffer, done)
  }

  private playEncodedItem(item: Extract<QueueItem, { kind: 'encoded' }>, done: () => void): void {
    const context = this.ensureContext()
    if (!context || !this.analyser) {
      done()
      return
    }
    const generation = this.generation
    context
      .decodeAudioData(item.data.slice(0))
      .then((buffer) => {
        if (generation === this.generation && this.current === item) this.startBuffer(item, buffer, done)
      })
      .catch(() => done())
  }

  private startBuffer(item: Extract<QueueItem, { kind: 'pcm' | 'encoded' }>, buffer: AudioBuffer, done: () => void): void {
    const context = this.context
    if (!context || !this.analyser) {
      done()
      return
    }
    void context.resume().catch(() => {})
    const source = context.createBufferSource()
    source.buffer = buffer
    source.connect(this.analyser)
    source.onended = done
    this.source = source
    this.startedAt = context.currentTime
    source.start()
    const durationMs = buffer.duration * 1000
    const words = [...item.text.matchAll(/\S+/g)]
    // Approximate word progress for captions; PCM carries no timing marks.
    const tick = () => {
      if (this.current !== item || !this.context) return
      const elapsed = (this.context.currentTime - this.startedAt) * 1000
      const index = Math.min(words.length - 1, Math.floor((elapsed / durationMs) * words.length))
      if (index >= 0 && words[index]) this.events.onProgress?.(item.text, words[index].index ?? 0)
      if (elapsed < durationMs) window.setTimeout(tick, 120)
    }
    tick()
  }

  private startLevelLoop(): void {
    if (this.levelTimer !== null || typeof window === 'undefined') return
    let phase = 0
    this.levelTimer = window.setInterval(() => {
      let target = 0
      if ((this.current?.kind === 'pcm' || this.current?.kind === 'encoded') && this.analyser && this.analyserData) {
        this.analyser.getByteTimeDomainData(this.analyserData)
        let sum = 0
        for (let i = 0; i < this.analyserData.length; i += 1) {
          const v = (this.analyserData[i] - 128) / 128
          sum += v * v
        }
        target = Math.min(1, Math.sqrt(sum / this.analyserData.length) * 4)
      } else if (this.current?.kind === 'system') {
        phase += 0.55
        this.boundaryPulse *= 0.82
        target = 0.32 + 0.22 * Math.abs(Math.sin(phase)) + 0.38 * this.boundaryPulse
      }
      this.level = this.level * 0.6 + target * 0.4
    }, 33)
  }

  private stopLevelLoop(): void {
    if (this.levelTimer !== null) window.clearInterval(this.levelTimer)
    this.levelTimer = null
    this.level = 0
  }
}

function decodeBase64(base64: string): Uint8Array {
  try {
    const binary = atob(base64)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
    return bytes
  } catch {
    return new Uint8Array(0)
  }
}

export function decodePcm16(base64: string): Int16Array {
  const bytes = decodeBase64(base64)
  const length = bytes.byteLength - (bytes.byteLength % 2)
  return new Int16Array(bytes.buffer, 0, length / 2)
}

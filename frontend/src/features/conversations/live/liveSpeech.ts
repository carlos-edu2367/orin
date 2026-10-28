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
    if (lang.startsWith('pt-br') || lang.startsWith('pt_br')) return 0
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
  thinkingPulseCount = 0
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
  private activeUtterance: SpeechSynthesisUtterance | null = null
  private thinkingActive = false
  private thinkingTimer: number | null = null
  private thinkingPulseTimer: number | null = null
  private thinkingStep = 0
  private thinkingGain: GainNode | null = null
  private thinkingNodes: Array<{ osc: OscillatorNode; gain: GainNode }> = []

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

  get thinking(): boolean {
    return this.thinkingActive
  }

  get currentText(): string {
    return this.current?.text ?? ''
  }

  startThinking(): void {
    if (this.thinkingActive || this.speaking) return
    this.thinkingActive = true
    if (typeof window === 'undefined') return
    const scheduleNext = (delayMs: number) => {
      this.thinkingTimer = window.setTimeout(() => {
        this.thinkingTimer = null
        if (!this.thinkingActive || this.speaking) return
        this.emitThinkingMotif()
        if (this.thinkingActive && !this.speaking) {
          scheduleNext(1350)
        }
      }, delayMs)
    }
    scheduleNext(180)
  }

  stopThinking(): void {
    if (this.thinkingTimer !== null && typeof window !== 'undefined') {
      window.clearTimeout(this.thinkingTimer)
    }
    this.thinkingTimer = null
    if (this.thinkingPulseTimer !== null && typeof window !== 'undefined') {
      window.clearTimeout(this.thinkingPulseTimer)
    }
    this.thinkingPulseTimer = null
    if (!this.thinkingActive) return
    this.thinkingActive = false
    const now = typeof this.context?.currentTime === 'number' ? this.context.currentTime : 0
    if (this.thinkingGain?.gain) {
      try {
        this.thinkingGain.gain.cancelScheduledValues?.(now)
        this.thinkingGain.gain.setValueAtTime?.(Math.max(0.0001, this.thinkingGain.gain.value || 1), now)
        this.thinkingGain.gain.exponentialRampToValueAtTime?.(0.0001, now + 0.03)
      } catch {
        // Context may be minimal mock or closed.
      }
    }
    const nodes = this.thinkingNodes.splice(0)
    for (const { osc, gain } of nodes) {
      try {
        osc.stop?.()
      } catch {
        // Not started or already stopped.
      }
      try {
        osc.disconnect?.()
        gain.disconnect?.()
      } catch {
        // Already disconnected.
      }
    }
    if (!this.speaking) {
      this.level = 0
    }
  }

  private emitThinkingMotif(): void {
    if (!this.thinkingActive || this.speaking) return
    this.thinkingPulseCount += 1
    const step = this.thinkingStep
    this.thinkingStep += 1
    this.level = 0.12
    if (this.thinkingPulseTimer !== null && typeof window !== 'undefined') {
      window.clearTimeout(this.thinkingPulseTimer)
    }
    if (typeof window !== 'undefined') {
      this.thinkingPulseTimer = window.setTimeout(() => {
        this.thinkingPulseTimer = null
        if (this.thinkingActive && !this.speaking) {
          this.level = 0.02
        }
      }, 260)
    }
    const context = this.ensureContext()
    if (!context || typeof context.createOscillator !== 'function' || typeof context.createGain !== 'function') return
    try {
      void context.resume?.()?.catch?.(() => {})
      const now = typeof context.currentTime === 'number' ? context.currentTime : 0
      if (!this.thinkingGain) {
        const master = context.createGain()
        if (master.gain) {
          master.gain.value = 1
          master.gain.setValueAtTime?.(1, now)
        }
        if (context.destination) master.connect?.(context.destination)
        this.thinkingGain = master
      } else if (this.thinkingGain.gain) {
        this.thinkingGain.gain.cancelScheduledValues?.(now)
        this.thinkingGain.gain.value = 1
        this.thinkingGain.gain.setValueAtTime?.(1, now)
      }
      let targetNode: AudioNode = this.thinkingGain
      if (typeof context.createBiquadFilter === 'function') {
        const filter = context.createBiquadFilter()
        filter.type = 'lowpass'
        if (filter.frequency) {
          filter.frequency.value = 880
          filter.frequency.setValueAtTime?.(880, now)
        }
        filter.connect?.(this.thinkingGain)
        targetNode = filter
      }
      const notes: ReadonlyArray<readonly [number, number]> =
        step % 2 === 0
          ? [[329.63, 0], [440.0, 0.18]]
          : [[293.66, 0], [392.0, 0.18]]
      for (const [freq, offsetSec] of notes) {
        const t0 = now + offsetSec
        const osc = context.createOscillator()
        const noteGain = context.createGain()
        osc.type = 'sine'
        if (osc.frequency) {
          osc.frequency.value = freq
          osc.frequency.setValueAtTime?.(freq, t0)
        }
        if (noteGain.gain) {
          noteGain.gain.value = 0.0001
          noteGain.gain.setValueAtTime?.(0.0001, t0)
          noteGain.gain.linearRampToValueAtTime?.(0.032, t0 + 0.025)
          noteGain.gain.exponentialRampToValueAtTime?.(0.0001, t0 + 0.2)
        }
        osc.connect?.(noteGain)
        noteGain.connect?.(targetNode)
        const entry = { osc, gain: noteGain }
        this.thinkingNodes.push(entry)
        osc.onended = () => {
          const idx = this.thinkingNodes.indexOf(entry)
          if (idx >= 0) this.thinkingNodes.splice(idx, 1)
          try {
            osc.disconnect?.()
            noteGain.disconnect?.()
          } catch {
            // Already disconnected.
          }
        }
        osc.start?.(t0)
        osc.stop?.(t0 + 0.21)
      }
    } catch {
      // Synthesizing thinking motif is best-effort and must never crash the voice stage.
    }
  }

  /** Chunk sequence and milliseconds played of the PCM chunk now playing, for barge-in receipts. */
  playbackPosition(): { chunkSeq: number; playedMs: number } | null {
    if (!this.current || this.current.kind === 'system' || !this.context) return null
    return { chunkSeq: this.current.chunkSeq, playedMs: Math.max(0, Math.round((this.context.currentTime - this.startedAt) * 1000)) }
  }

  speakSystem(text: string): void {
    this.stopThinking()
    const clean = textForSpeech(text)
    if (!clean) return
    this.queue.push({ kind: 'system', text: clean })
    this.pump()
  }

  playPcm(text: string, pcmBase64: string, sampleRate: number, chunkSeq: number): void {
    this.stopThinking()
    const pcm = decodePcm16(pcmBase64)
    if (pcm.length === 0) return
    this.queue.push({ kind: 'pcm', text, pcm, sampleRate, chunkSeq })
    this.pump()
  }

  /** MP3/WAV from a cloud voice; the browser decodes it, sample rate included. */
  playEncoded(text: string, base64: string, chunkSeq: number): void {
    this.stopThinking()
    const bytes = decodeBase64(base64)
    if (bytes.byteLength === 0) return
    this.queue.push({ kind: 'encoded', text, data: bytes.buffer as ArrayBuffer, chunkSeq })
    this.pump()
  }

  /** Stop at once and drop everything queued (barge-in, mute, close). */
  stop(): void {
    this.stopThinking()
    this.generation += 1
    this.queue = []
    const wasSpeaking = this.current !== null
    this.current = null
    this.activeUtterance = null
    if (speechSynthesisSupported() && (window.speechSynthesis.speaking || window.speechSynthesis.pending)) {
      window.speechSynthesis.cancel()
    }
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
    this.stopThinking()
    this.stop()
    this.thinkingGain = null
    void this.context?.close?.()?.catch?.(() => {})
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
    const synth = window.speechSynthesis
    if (synth.speaking || synth.pending) {
      synth.cancel()
    }
    const utterance = new window.SpeechSynthesisUtterance(text)
    const voices = synth.getVoices()
    const isPtBr = (lang: string) => {
      const lower = lang.toLowerCase()
      return lower.startsWith('pt-br') || lower.startsWith('pt_br')
    }
    const rankedBestUri = listSystemVoices()[0]?.uri
    const voice =
      (this.system.voiceUri ? voices.find((candidate) => candidate.voiceURI === this.system.voiceUri) : undefined)
      ?? voices.find((candidate) => isPtBr(candidate.lang) && candidate.localService)
      ?? voices.find((candidate) => isPtBr(candidate.lang))
      ?? (rankedBestUri ? voices.find((candidate) => candidate.voiceURI === rankedBestUri) : undefined)
      ?? voices.find((candidate) => candidate.lang.toLowerCase().startsWith('pt'))
    if (voice) utterance.voice = voice
    utterance.lang = voice && isPtBr(voice.lang) ? voice.lang : 'pt-BR'
    utterance.rate = this.system.rate
    utterance.pitch = this.system.pitch
    this.activeUtterance = utterance
    utterance.onboundary = (event) => {
      this.boundaryPulse = 1
      this.events.onProgress?.(text, event.charIndex)
    }
    const finishUtterance = () => {
      if (this.activeUtterance === utterance) this.activeUtterance = null
      done()
    }
    utterance.onend = finishUtterance
    utterance.onerror = finishUtterance
    synth.speak(utterance)
  }

  private ensureContext(): AudioContext | null {
    if (this.context) return this.context
    const w = window as unknown as { AudioContext?: AudioContextCtor; webkitAudioContext?: AudioContextCtor }
    const Ctor = w.AudioContext ?? w.webkitAudioContext
    if (!Ctor) return null
    try {
      const context = new Ctor()
      this.context = context
      if (typeof context.createAnalyser === 'function') {
        const analyser = context.createAnalyser()
        analyser.fftSize = 512
        analyser.connect?.(context.destination)
        this.analyser = analyser
        this.analyserData = new Uint8Array(new ArrayBuffer(analyser.fftSize))
      }
      return context
    } catch {
      return null
    }
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

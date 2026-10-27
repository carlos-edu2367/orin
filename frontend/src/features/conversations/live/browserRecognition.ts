/**
 * The browser's own speech recognition (Chrome/Edge). It needs no setup, but
 * Electron ships without the Google service behind it, so a `network` error
 * on start is reported as "unsupported here" rather than a transient failure.
 */

type RecognitionResultList = ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }>

type RecognitionLike = {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((event: { resultIndex: number; results: RecognitionResultList }) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}

type RecognitionCtor = new () => RecognitionLike

function recognitionCtor(): RecognitionCtor | null {
  if (typeof window === 'undefined') return null
  const w = window as Window & { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export function isElectron(): boolean {
  return typeof navigator !== 'undefined' && /electron/i.test(navigator.userAgent)
}

export function browserRecognitionSupported(): boolean {
  return recognitionCtor() !== null && !isElectron()
}

export type BrowserRecognitionEvents = {
  onInterim: (text: string) => void
  onFinal: (text: string) => void
  onError: (code: string, message: string) => void
}

export class BrowserRecognition {
  private recognition: RecognitionLike | null = null
  private running = false
  private paused = false

  constructor(private readonly events: BrowserRecognitionEvents, private readonly lang = 'pt-BR') {}

  start(): void {
    const Ctor = recognitionCtor()
    if (!Ctor) {
      this.events.onError('unsupported', 'Este navegador não reconhece fala sozinho.')
      return
    }
    const recognition = new Ctor()
    recognition.lang = this.lang
    recognition.continuous = true
    recognition.interimResults = true
    recognition.onresult = (event) => {
      let interim = ''
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i]
        const text = result[0]?.transcript ?? ''
        if (result.isFinal) this.events.onFinal(text.trim())
        else interim += text
      }
      if (interim.trim()) this.events.onInterim(interim.trim())
    }
    recognition.onerror = (event) => {
      if (!this.running || this.recognition !== recognition) return
      if (event.error === 'no-speech' || event.error === 'aborted') return
      if (event.error === 'network') {
        this.running = false
        this.events.onError('network', 'O reconhecimento do navegador não está disponível aqui. Escolha o Whisper local ou a OpenAI.')
        return
      }
      if (event.error === 'not-allowed') {
        this.running = false
        this.events.onError('denied', 'O navegador bloqueou o microfone para o reconhecimento de fala.')
        return
      }
      this.events.onError(event.error, `Reconhecimento de fala falhou (${event.error}).`)
    }
    // Chrome ends continuous recognition after a pause; keep it alive while wanted.
    recognition.onend = () => {
      if (this.running && !this.paused) {
        try {
          recognition.start()
        } catch {
          // Already restarting.
        }
      }
    }
    this.recognition = recognition
    this.running = true
    try {
      recognition.start()
    } catch {
      this.events.onError('start', 'Não consegui iniciar o reconhecimento de fala.')
    }
  }

  setPaused(paused: boolean): void {
    if (this.paused === paused) return
    this.paused = paused
    if (!this.recognition || !this.running) return
    try {
      if (paused) this.recognition.abort()
      else this.recognition.start()
    } catch {
      // Ignore double start/stop.
    }
  }

  stop(): void {
    this.running = false
    try {
      this.recognition?.abort()
    } catch {
      // Already stopped.
    }
    this.recognition = null
  }
}

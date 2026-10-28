/**
 * Multi-accent Wake Word matcher ("Orin") & active Live Stage coordination.
 *
 * Mirrors `src/agentos/live_voice/wake_word.py` so both backend STT and
 * browser/local transcripts share identical accent tolerance and false-positive guards.
 */

export type WakeWordMatch = {
  matched: boolean
  wakeVariant: string
  commandRemainder: string
  confidence: number
}

const VOCATIVE_PREFIXES = new Set([
  'oi',
  'ei',
  'fala',
  'hey',
  'ola',
  'o',
  'oh',
  'ai',
  'ea',
  'eae',
  'opa',
  'ok',
  'okay',
  'escuta',
  'ouve',
  'bora',
  'salve',
])

const TWO_WORD_PREFIXES: ReadonlyArray<readonly [string, string]> = [
  ['e', 'ai'],
  ['bom', 'dia'],
  ['boa', 'tarde'],
  ['boa', 'noite'],
  ['por', 'favor'],
]

const WAKE_VARIANTS_1_TOKEN = new Set([
  'orin',
  'orinz',
  'orins',
  'orim',
  'orimz',
  'orims',
  'oryn',
  'oring',
  'ourin',
  'ourim',
  'aurin',
  'aurim',
  'horin',
  'horim',
  'horyn',
  'orem',
  'oren',
  'ori',
  'orinho',
  'orion',
  'urin',
  'urim',
  'uren',
  'urem',
  'olin',
  'olim',
  'olhin',
  'olhim',
  'arin',
  'arim',
])

const WAKE_VARIANTS_2_TOKENS = new Map<string, number>([
  ['o rin', 0.9],
  ['oh rin', 0.9],
  ['o rim', 0.9],
  ['oh rim', 0.9],
  ['ou rin', 0.88],
  ['ou rim', 0.88],
  ['au rin', 0.86],
  ['all in', 0.85],
  ['or in', 0.88],
  ['aw rin', 0.85],
  ['ohin', 0.85],
])

const STOPWORDS_COLLISION = new Set([
  'olha',
  'olhe',
  'olhar',
  'olho',
  'olhos',
  'hora',
  'horas',
  'horario',
  'agora',
  'ordem',
  'ordens',
  'onde',
  'ontem',
  'ouro',
  'outro',
  'outra',
  'outros',
  'outras',
  'origem',
  'original',
  'orcamento',
  'orelha',
  'oracao',
  'oracoes',
  'oriente',
  'orientacao',
  'orgao',
  'orgulho',
  'orquestra',
])

export function stripAccentsLower(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
}

export function phoneticSkeletonPtBr(word: string): string {
  let w = stripAccentsLower(word).replace(/[^a-z]/g, '')
  if (!w) return ''
  w = w.replace(/^h+/, '')
  w = w.replace(/(?<=[nm])[sz]+$/, '')
  for (const prefix of ['our', 'aur', 'or', 'ur', 'olh', 'ol']) {
    if (w.startsWith(prefix)) {
      w = 'or' + w.slice(prefix.length)
      break
    }
  }
  for (const suffix of ['inho', 'inha', 'ing', 'ion', 'yn', 'im', 'in', 'em', 'en']) {
    if (w.endsWith(suffix) && w.length > suffix.length + 1) {
      w = w.slice(0, -suffix.length) + 'in'
      break
    }
  }
  if (w === 'orin' || w === 'ori') return 'ORN'
  return w.toUpperCase()
}

const TOKEN_RE = /[A-Za-zÀ-ÖØ-öø-ÿ0-9']+/g

type TokenSpan = {
  raw: string
  norm: string
  start: number
  end: number
}

function tokenizeWithSpans(text: string): TokenSpan[] {
  const spans: TokenSpan[] = []
  for (const match of text.matchAll(TOKEN_RE)) {
    const raw = match[0]
    const start = match.index ?? 0
    spans.push({
      raw,
      norm: stripAccentsLower(raw).replace(/^'+|'+$/g, ''),
      start,
      end: start + raw.length,
    })
  }
  return spans
}

function cleanRemainder(
  text: string,
  cutIndex: number,
  customNames?: ReadonlyArray<string>,
  depth = 0,
): string {
  let cleaned = text
    .slice(cutIndex)
    .replace(/^[\s,.:;!?—–\-]+/, '')
    .trim()
  if (depth < 2 && cleaned) {
    const nested = matchWakeWord(cleaned, customNames, depth + 1)
    if (nested.matched) {
      cleaned = nested.commandRemainder
    }
  }
  const normRem = stripAccentsLower(cleaned.replace(/[^\p{L}\p{N}\s]/gu, '')).trim()
  if (VOCATIVE_PREFIXES.has(normRem)) return ''
  return cleaned
}

export function matchWakeWord(
  transcript: string,
  customNames?: ReadonlyArray<string>,
  depth = 0,
): WakeWordMatch {
  const noMatch: WakeWordMatch = {
    matched: false,
    wakeVariant: '',
    commandRemainder: '',
    confidence: 0,
  }
  if (!transcript || !transcript.trim()) return noMatch

  const spans = tokenizeWithSpans(transcript)
  if (spans.length === 0) return noMatch

  const customSingle = new Set<string>()
  const customPairs = new Set<string>()
  for (const name of customNames ?? []) {
    const normTokens = tokenizeWithSpans((name ?? '').slice(0, 64))
      .map((t) => t.norm)
      .filter(Boolean)
    if (normTokens.length === 1) customSingle.add(normTokens[0])
    else if (normTokens.length === 2) customPairs.add(`${normTokens[0]} ${normTokens[1]}`)
  }

  let idx = 0
  if (spans.length >= 2) {
    const firstPair = [spans[0].norm, spans[1].norm] as const
    if (TWO_WORD_PREFIXES.some(([a, b]) => a === firstPair[0] && b === firstPair[1])) {
      idx = 2
    }
  }
  while (idx < spans.length && idx < 2 && VOCATIVE_PREFIXES.has(spans[idx].norm)) {
    if (idx + 1 < spans.length) {
      const pairKey = `${spans[idx].norm} ${spans[idx + 1].norm}`
      if (WAKE_VARIANTS_2_TOKENS.has(pairKey) || customPairs.has(pairKey)) {
        break
      }
    }
    idx += 1
  }

  if (idx >= spans.length) return noMatch

  const candidate = spans[idx]
  const word = candidate.norm

  if (STOPWORDS_COLLISION.has(word) && !customSingle.has(word)) {
    return noMatch
  }

  if (idx + 1 < spans.length) {
    const pairKey = `${word} ${spans[idx + 1].norm}`
    if (customPairs.has(pairKey)) {
      return {
        matched: true,
        wakeVariant: `${candidate.raw} ${spans[idx + 1].raw}`,
        commandRemainder: cleanRemainder(transcript, spans[idx + 1].end, customNames, depth),
        confidence: 0.96,
      }
    }
    const pairConf = WAKE_VARIANTS_2_TOKENS.get(pairKey)
    if (pairConf !== undefined) {
      return {
        matched: true,
        wakeVariant: `${candidate.raw} ${spans[idx + 1].raw}`,
        commandRemainder: cleanRemainder(transcript, spans[idx + 1].end, customNames, depth),
        confidence: pairConf,
      }
    }
  }

  if (customSingle.has(word)) {
    return {
      matched: true,
      wakeVariant: candidate.raw,
      commandRemainder: cleanRemainder(transcript, candidate.end, customNames, depth),
      confidence: 0.98,
    }
  }

  if (WAKE_VARIANTS_1_TOKEN.has(word)) {
    const conf = word === 'orin' ? 1.0 : word === 'orim' || word === 'oryn' ? 0.95 : 0.88
    return {
      matched: true,
      wakeVariant: candidate.raw,
      commandRemainder: cleanRemainder(transcript, candidate.end, customNames, depth),
      confidence: conf,
    }
  }

  if (!STOPWORDS_COLLISION.has(word) && phoneticSkeletonPtBr(word) === 'ORN') {
    return {
      matched: true,
      wakeVariant: candidate.raw,
      commandRemainder: cleanRemainder(transcript, candidate.end, customNames, depth),
      confidence: 0.82,
    }
  }

  return noMatch
}

export function pcm16ToBase64(frames: ReadonlyArray<Int16Array>): string {
  const totalSamples = frames.reduce((acc, frame) => acc + frame.length, 0)
  const merged = new Int16Array(totalSamples)
  let offset = 0
  for (const frame of frames) {
    merged.set(frame, offset)
    offset += frame.length
  }
  const bytes = new Uint8Array(merged.buffer, merged.byteOffset, merged.byteLength)
  let binary = ''
  const chunkSize = 0x8000
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize))
  }
  return window.btoa(binary)
}

export function playWakeChime(): void {
  try {
    const AudioCtx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!AudioCtx) return
    const ctx = new AudioCtx()
    if (typeof ctx.createOscillator !== 'function' || typeof ctx.createGain !== 'function') {
      void ctx.close?.()
      return
    }
    const now = ctx.currentTime ?? 0
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = 'sine'
    osc.frequency.setValueAtTime(528, now)
    osc.frequency.exponentialRampToValueAtTime(792, now + 0.14)
    gain.gain.setValueAtTime(0.0001, now)
    gain.gain.linearRampToValueAtTime(0.08, now + 0.02)
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18)
    osc.connect(gain)
    gain.connect(ctx.destination)
    osc.start(now)
    osc.stop(now + 0.19)
    window.setTimeout(() => {
      void ctx.close?.()
    }, 260)
  } catch {
    // WebAudio may be unavailable in headless tests; ignore silently.
  }
}

const STAGE_ACTIVE_EVENT = 'orin:live-voice-stage-active'
let liveStageOpen = false
let quickSettingsOpen = false

function emitStageActive(): void {
  try {
    window.dispatchEvent(
      new CustomEvent(STAGE_ACTIVE_EVENT, {
        detail: { open: liveStageOpen || quickSettingsOpen },
      }),
    )
  } catch {
    // Ignore in non-DOM environments.
  }
}

export function setLiveVoiceStageOpen(isOpen: boolean): void {
  liveStageOpen = isOpen
  if (!isOpen) quickSettingsOpen = false
  emitStageActive()
}

export function setLiveVoiceQuickSettingsOpen(isOpen: boolean): void {
  quickSettingsOpen = isOpen
  emitStageActive()
}

export function isLiveVoiceStageOpen(): boolean {
  return liveStageOpen || quickSettingsOpen
}

export function onLiveVoiceStageOpenChange(listener: (open: boolean) => void): () => void {
  const handler = (event: Event) => {
    const detail = (event as CustomEvent<{ open?: boolean }>).detail
    listener(Boolean(detail?.open))
  }
  window.addEventListener(STAGE_ACTIVE_EVENT, handler)
  return () => window.removeEventListener(STAGE_ACTIVE_EVENT, handler)
}

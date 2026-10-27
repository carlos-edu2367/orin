/** Anti-echo barriers shared by the Live voice capture and its tests. */

export type LongFormMicConstraints = {
  echoCancellation: boolean
  noiseSuppression: boolean
  autoGainControl: boolean
  channelCount: number
  sampleRate: number
}

export const LONG_FORM_MIC_CONSTRAINTS: LongFormMicConstraints = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: false,
  channelCount: 1,
  sampleRate: 24000,
}

export const LIVE_VOICE_DUCKING_MULTIPLIER = 2.8
export const LIVE_VOICE_DUCKING_MIN_SPEECH_MS = 220
export const LIVE_VOICE_DUCKING_TAIL_MS = 250
export const LIVE_VOICE_ECHO_SIMILARITY_THRESHOLD = 0.65

export type DuckingEvaluationInput = {
  rmsEnergy: number
  baseThreshold: number
  sustainedSpeechMs: number
  isAssistantSpeaking: boolean
  msSinceLastTtsChunk: number
}

export function shouldTriggerDuckingBargeIn(input: DuckingEvaluationInput): boolean {
  const inDuckingWindow =
    input.isAssistantSpeaking || input.msSinceLastTtsChunk <= LIVE_VOICE_DUCKING_TAIL_MS
  if (!inDuckingWindow) {
    return input.rmsEnergy >= input.baseThreshold
  }
  const requiredEnergy = input.baseThreshold * LIVE_VOICE_DUCKING_MULTIPLIER
  return (
    input.rmsEnergy >= requiredEnergy &&
    input.sustainedSpeechMs >= LIVE_VOICE_DUCKING_MIN_SPEECH_MS
  )
}

function normalizeLexicalText(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function charTrigrams(text: string): Map<string, number> {
  const padded = `  ${text}  `
  const counts = new Map<string, number>()
  for (let i = 0; i <= padded.length - 3; i += 1) {
    const gram = padded.slice(i, i + 3)
    counts.set(gram, (counts.get(gram) ?? 0) + 1)
  }
  return counts
}

export function computeEchoSimilarity(transcript: string, referenceTts: string): number {
  const normMic = normalizeLexicalText(transcript)
  const normTts = normalizeLexicalText(referenceTts)
  if (!normMic || !normTts) return 0
  if (normMic === normTts) return 1
  if (normMic.length >= 6 && normTts.includes(normMic)) {
    return 0.92
  }

  const micWords = normMic.split(' ').filter(Boolean)
  const ttsWordsSet = new Set(normTts.split(' ').filter(Boolean))
  if (micWords.length === 0 || ttsWordsSet.size === 0) return 0

  let matchedWords = 0
  for (const w of micWords) {
    if (ttsWordsSet.has(w)) matchedWords += 1
  }
  const wordContainment = matchedWords / micWords.length

  const gramsA = charTrigrams(normMic)
  const gramsB = charTrigrams(normTts)
  let intersection = 0
  let totalA = 0
  let totalB = 0
  for (const [gram, countA] of gramsA.entries()) {
    totalA += countA
    const countB = gramsB.get(gram) ?? 0
    intersection += Math.min(countA, countB)
  }
  for (const countB of gramsB.values()) {
    totalB += countB
  }
  const trigramDice = totalA + totalB > 0 ? (2 * intersection) / (totalA + totalB) : 0

  return Number(Math.min(1, wordContainment * 0.85 + trigramDice * 0.15).toFixed(4))
}

export function isEchoOfRecentTts(
  transcript: string,
  recentTtsSentences: ReadonlyArray<string>,
  threshold = LIVE_VOICE_ECHO_SIMILARITY_THRESHOLD,
): boolean {
  for (const sentence of recentTtsSentences) {
    if (computeEchoSimilarity(transcript, sentence) >= threshold) {
      return true
    }
  }
  return false
}

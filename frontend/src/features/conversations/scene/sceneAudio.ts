export type SceneSoundCue = 'step' | 'type' | 'ding' | 'bonk' | 'envelope'

export const SCENE_SOUND_STORAGE_KEY = 'orin.scene.sound'

const CUE_MIN_INTERVAL_MS: Readonly<Record<SceneSoundCue, number>> = {
  step: 140,
  type: 110,
  ding: 80,
  bonk: 90,
  envelope: 80,
}

let sharedAudioContext: AudioContext | null = null
const lastCuePlayedAt = new Map<SceneSoundCue, number>()

/**
 * Reads the persisted sound preference from `localStorage` (`orin.scene.sound`).
 * Defaults to `false` (off) when unset or when storage is inaccessible.
 */
export function readSceneSoundPreference(): boolean {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return false
    const raw = window.localStorage.getItem(SCENE_SOUND_STORAGE_KEY)
    return raw === 'true' || raw === '1'
  } catch {
    return false
  }
}

/**
 * Persists the user's sound preference to `localStorage` (`orin.scene.sound`).
 */
export function writeSceneSoundPreference(enabled: boolean): void {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return
    window.localStorage.setItem(SCENE_SOUND_STORAGE_KEY, enabled ? 'true' : 'false')
  } catch {
    // Ignore storage quota or sandbox errors
  }
}

function getOrCreateAudioContext(): AudioContext | null {
  if (sharedAudioContext) return sharedAudioContext
  if (typeof window === 'undefined') return null

  const AudioCtx =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (typeof AudioCtx !== 'function') return null

  try {
    sharedAudioContext = new AudioCtx()
    return sharedAudioContext
  } catch {
    return null
  }
}

function nowMs(): number {
  if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
    return performance.now()
  }
  return Date.now()
}

/**
 * Plays a short synthesized pixel-art sound effect via WebAudio.
 * Lazily initializes `AudioContext` on first call, enforces per-cue rate limits,
 * and never throws in headless/jsdom environments.
 */
export function playSceneSound(cue: SceneSoundCue): void {
  try {
    const timestamp = nowMs()
    const minInterval = CUE_MIN_INTERVAL_MS[cue] ?? 80
    const prev = lastCuePlayedAt.get(cue) ?? -Infinity
    if (timestamp - prev < minInterval) return
    lastCuePlayedAt.set(cue, timestamp)

    const ctx = getOrCreateAudioContext()
    if (!ctx) return

    if (ctx.state === 'suspended' && typeof ctx.resume === 'function') {
      void ctx.resume().catch(() => undefined)
    }

    const t0 = ctx.currentTime
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.connect(gain)
    gain.connect(ctx.destination)

    switch (cue) {
      case 'step': {
        osc.type = 'triangle'
        osc.frequency.setValueAtTime(115, t0)
        osc.frequency.exponentialRampToValueAtTime(68, t0 + 0.045)
        gain.gain.setValueAtTime(0.032, t0)
        gain.gain.exponentialRampToValueAtTime(0.001, t0 + 0.048)
        osc.start(t0)
        osc.stop(t0 + 0.05)
        break
      }
      case 'type': {
        osc.type = 'square'
        osc.frequency.setValueAtTime(660, t0)
        osc.frequency.exponentialRampToValueAtTime(480, t0 + 0.025)
        gain.gain.setValueAtTime(0.022, t0)
        gain.gain.exponentialRampToValueAtTime(0.001, t0 + 0.028)
        osc.start(t0)
        osc.stop(t0 + 0.03)
        break
      }
      case 'ding': {
        osc.type = 'sine'
        osc.frequency.setValueAtTime(523.25, t0) // C5
        osc.frequency.setValueAtTime(783.99, t0 + 0.07) // G5
        gain.gain.setValueAtTime(0.048, t0)
        gain.gain.exponentialRampToValueAtTime(0.001, t0 + 0.19)
        osc.start(t0)
        osc.stop(t0 + 0.195)
        break
      }
      case 'bonk': {
        osc.type = 'sawtooth'
        osc.frequency.setValueAtTime(185, t0)
        osc.frequency.linearRampToValueAtTime(72, t0 + 0.15)
        gain.gain.setValueAtTime(0.045, t0)
        gain.gain.exponentialRampToValueAtTime(0.001, t0 + 0.16)
        osc.start(t0)
        osc.stop(t0 + 0.165)
        break
      }
      case 'envelope': {
        osc.type = 'triangle'
        osc.frequency.setValueAtTime(320, t0)
        osc.frequency.exponentialRampToValueAtTime(620, t0 + 0.09)
        gain.gain.setValueAtTime(0.036, t0)
        gain.gain.exponentialRampToValueAtTime(0.001, t0 + 0.095)
        osc.start(t0)
        osc.stop(t0 + 0.1)
        break
      }
    }
  } catch {
    // Never throw if AudioContext or node methods fail
  }
}

/** Resets internal rate-limit and context state (used in unit tests). */
export function resetSceneAudioForTests(): void {
  lastCuePlayedAt.clear()
  sharedAudioContext = null
}

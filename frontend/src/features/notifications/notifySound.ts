export type NotifySoundKind = 'attention' | 'done' | 'failed'

export type NotifySoundOption = boolean | { enabled?: boolean }

let sharedAudioContext: AudioContext | null = null

function isSoundEnabled(option?: NotifySoundOption): boolean {
  if (option === undefined) return true
  if (typeof option === 'boolean') return option
  return option.enabled !== false
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

function scheduleTone(
  ctx: AudioContext,
  type: OscillatorNode['type'],
  startFreq: number,
  endFreq: number | null,
  startTime: number,
  duration: number,
  peakGain: number,
): void {
  const osc = ctx.createOscillator()
  const gain = ctx.createGain()
  osc.connect(gain)
  gain.connect(ctx.destination)

  osc.type = type
  osc.frequency.setValueAtTime(startFreq, startTime)
  if (endFreq !== null && endFreq !== startFreq) {
    osc.frequency.exponentialRampToValueAtTime(endFreq, startTime + duration * 0.9)
  }

  gain.gain.setValueAtTime(peakGain, startTime)
  gain.gain.exponentialRampToValueAtTime(0.001, startTime + duration)

  osc.start(startTime)
  osc.stop(startTime + duration + 0.005)
}

/**
 * Plays a short, pleasant WebAudio synthesized notification sound.
 * Reuses a single `AudioContext` created on demand and never throws in headless/jsdom environments.
 */
export function playNotifySound(kind: NotifySoundKind, enabled: NotifySoundOption = true): boolean {
  if (!isSoundEnabled(enabled)) return false
  try {
    const ctx = getOrCreateAudioContext()
    if (!ctx) return false

    if (ctx.state === 'suspended' && typeof ctx.resume === 'function') {
      void ctx.resume().catch(() => undefined)
    }

    const t0 = ctx.currentTime

    switch (kind) {
      case 'attention': {
        // Two rising notes (D5 -> A5)
        scheduleTone(ctx, 'sine', 587.33, null, t0, 0.09, 0.055)
        scheduleTone(ctx, 'sine', 880.0, null, t0 + 0.095, 0.14, 0.06)
        break
      }
      case 'done': {
        // Soft short major chord (C5 + E5 + G5)
        scheduleTone(ctx, 'sine', 523.25, null, t0, 0.22, 0.04)
        scheduleTone(ctx, 'sine', 659.25, null, t0 + 0.015, 0.22, 0.036)
        scheduleTone(ctx, 'sine', 783.99, null, t0 + 0.03, 0.24, 0.036)
        break
      }
      case 'failed': {
        // Short low descending note (G3 -> C3)
        scheduleTone(ctx, 'triangle', 196.0, 130.81, t0, 0.18, 0.055)
        break
      }
    }
    return true
  } catch {
    return false
  }
}

export const notifySound = playNotifySound

/** Resets the shared AudioContext instance for unit tests. */
export function resetNotifySoundForTests(): void {
  sharedAudioContext = null
}

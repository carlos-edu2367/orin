import charactersUrl from './assets/characters.webp'
import officeUrl from './assets/office.webp'
import { SCENE_ART_FRAMES, type SceneArtFrame } from './assets/sceneArtManifest'
import { seatAt, type SceneSeat } from './sceneLayout'
import { ART_PERSON_LOOKS } from './sceneSprites'
import type { ScenePose, SceneStationId } from './sceneTypes'

/**
 * Painted office art of the Cena: background image plus a character atlas cut from the
 * sprite sheet (`frontend/scripts/scene-art/build_scene_art.py`). The canvas draws with it
 * once both images have loaded and falls back to the pixel matrices of `sceneSprites.ts`
 * before that (and under jsdom, where images never load).
 */

export interface SceneArtImages {
  readonly office: HTMLImageElement
  readonly characters: HTMLImageElement
}

let images: { office: HTMLImageElement; characters: HTMLImageElement } | null = null
let ready = false
let failed = false
const listeners = new Set<() => void>()

/** The loaded art, or null while it is loading (or if it could not load). */
export function getSceneArt(): SceneArtImages | null {
  return ready && images ? images : null
}

/**
 * Starts loading the art once and calls `onReady` when it can be drawn.
 * Returns an unsubscribe function.
 */
export function loadSceneArt(onReady: () => void): () => void {
  if (ready || failed || typeof Image === 'undefined') return () => undefined
  listeners.add(onReady)
  if (!images) {
    const office = new Image()
    const characters = new Image()
    let pending = 2
    const onLoad = () => {
      pending -= 1
      if (pending > 0 || failed) return
      ready = true
      for (const listener of listeners) listener()
      listeners.clear()
    }
    const onError = () => {
      failed = true
      listeners.clear()
    }
    for (const img of [office, characters]) {
      img.decoding = 'async'
      img.addEventListener('load', onLoad, { once: true })
      img.addEventListener('error', onError, { once: true })
    }
    office.src = officeUrl
    characters.src = charactersUrl
    images = { office, characters }
  }
  return () => {
    listeners.delete(onReady)
  }
}

/** What the canvas needs from an animated character to pick its art frame. */
export interface ArtCharacter {
  readonly isMain: boolean
  readonly look: number
  readonly pose: ScenePose
  readonly frame: number
  readonly facing: 'left' | 'right'
  readonly station: SceneStationId
  readonly x: number
  readonly y: number
}

export interface ArtPlacement {
  readonly frame: SceneArtFrame
  /** Top-left corner and size in logical px. */
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
  readonly flip: boolean
  /** Chair the character sits in: its backrest is drawn over the character. */
  readonly seat: SceneSeat | null
}

const ORIN_POSE_FRAMES: Record<ScenePose, string> = {
  idle: 'orin.idle',
  walk: 'orin.move',
  work: 'orin.idle',
  think: 'orin.think',
  raise_hand: 'orin.ask',
  scared: 'orin.scared',
  wait: 'orin.idle',
  deliver: 'orin.sparkle',
  sit: 'orin.sleep',
}

const PERSON_POSE_FRAMES: Record<ScenePose, string> = {
  idle: 'idle',
  walk: 'walk1',
  work: 'idle',
  think: 'think',
  raise_hand: 'ask',
  scared: 'scared',
  wait: 'wait',
  deliver: 'idle',
  sit: 'relax',
}

function frameName(agent: ArtCharacter, seated: boolean, nowMs: number): string {
  if (agent.isMain) {
    // Busy Orin: mostly attentive, now and then a happy squint.
    if (agent.pose === 'work') return Math.floor(nowMs / 1400) % 3 === 2 ? 'orin.happy' : 'orin.idle'
    return ORIN_POSE_FRAMES[agent.pose]
  }
  const person = `p${(Math.abs(agent.look) % ART_PERSON_LOOKS) + 1}`
  if (seated) return `${person}.seated`
  if (agent.pose === 'walk') return `${person}.${agent.frame % 2 === 0 ? 'walk1' : 'walk2'}`
  return `${person}.${PERSON_POSE_FRAMES[agent.pose]}`
}

/** Small vertical bob (logical px) that makes busy characters look alive. */
function bobOffset(agent: ArtCharacter, nowMs: number, reducedMotion: boolean): number {
  if (reducedMotion) return 0
  if (agent.pose === 'walk') return agent.frame % 2 === 0 ? 0 : -0.6
  if (agent.pose === 'work') return Math.floor(nowMs / 260) % 2 === 0 ? 0 : -0.35
  return 0
}

/** Chooses the art frame for a character and where to draw it, or null if it is missing. */
export function placeArtCharacter(
  agent: ArtCharacter,
  nowMs: number,
  reducedMotion: boolean
): ArtPlacement | null {
  const chair = !agent.isMain && agent.pose === 'work' ? seatAt(agent.station, agent.x, agent.y) : null
  const frame = SCENE_ART_FRAMES[frameName(agent, chair !== null, reducedMotion ? 0 : nowMs)]
  if (!frame) return null
  const width = frame.w * frame.s
  const height = frame.h * frame.s
  // A seated person is anchored on the chair: shoulders just below the top of the backrest.
  const footX = chair ? chair.x : agent.x
  const footY = chair ? chair.backTop + 2 : agent.y
  const bob = bobOffset(agent, nowMs, reducedMotion)
  return {
    frame,
    x: footX - frame.ax * frame.s,
    y: footY - frame.ay * frame.s + bob,
    width,
    height,
    flip: agent.pose === 'walk' && agent.facing === 'left',
    seat: chair,
  }
}

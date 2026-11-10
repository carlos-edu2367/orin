import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type JSX,
} from 'react'
import {
  computeAgentNameTagMeta,
  computeNameTagStepY,
  createAnimator,
  drainSoundCues,
  hitTest,
  stepAnimator,
  syncAnimator,
  type ActiveEffect,
  type ActiveTransfer,
  type AnimatedAgent,
  type SceneAnimatorState,
} from './sceneAnimator'
import { playSceneSound } from './sceneAudio'
import { SCENE_ART_RACK_LEDS } from './assets/sceneArtManifest'
import { getSceneArt, loadSceneArt, placeArtCharacter, type SceneArtImages } from './sceneArt'
import {
  computeFallbackIdleBobOffset,
  selectCustomCharacterPose,
} from './sceneCharacters'
import {
  FIGURE_HEIGHT,
  SCENE_HEIGHT,
  SCENE_SCREENS,
  SCENE_TILE,
  SCENE_WIDTH,
  STATION_PROPS,
  type SceneSeat,
} from './sceneLayout'
import { layoutLabels, type PlacedSceneLabelBox, type SceneLabelBox } from './sceneLabels'
import { STATION_LABELS, STATION_SHORT_LABELS } from './sceneStations'
import {
  CHARACTER_H,
  CHARACTER_W,
  SCENE_PALETTE,
  getCharacterSprite,
  getPropDimensions,
  getPropSprite,
} from './sceneSprites'
import { MAIN_AGENT_ID, type SceneState } from './sceneTypes'
import './scene.css'

export interface SceneCanvasProps {
  scene: SceneState
  selectedAgentId?: string | null
  onSelectAgent?: (agentId: string) => void
  onOpenAgentModal?: (agentId: string) => void
  soundEnabled?: boolean
  reducedMotion?: boolean
  className?: string
}

// Reusable scratch buffers for per-frame label/bubble anti-collision
const visibleAgentsScratch: AnimatedAgent[] = []
const nameInputScratch: SceneLabelBox[] = []
const namePlacedScratch: PlacedSceneLabelBox[] = []
const bubbleInputScratch: SceneLabelBox[] = []
const bubblePlacedScratch: PlacedSceneLabelBox[] = []
// Top of each drawn character this frame (logical px), so bubbles sit above the real head.
const headTopScratch = new Map<string, number>()

/** Pixel-art props (papers, sparks...) are drawn larger over the painted office. */
const ART_PROP_SCALE = 1.6

function setScratchBox(
  list: SceneLabelBox[],
  index: number,
  id: string,
  x: number,
  y: number,
  width: number,
  height: number,
  priority: boolean
): void {
  const existing = list[index]
  if (existing) {
    existing.id = id
    existing.x = x
    existing.y = y
    existing.width = width
    existing.height = height
    existing.priority = priority
  } else {
    list[index] = { id, x, y, width, height, priority }
  }
}

function readSystemReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false
  }
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

function getSafe2dContext(canvas: HTMLCanvasElement): CanvasRenderingContext2D | null {
  if (typeof canvas.getContext !== 'function') return null
  const isJsdom =
    typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent ?? '')
  const isMocked =
    'mock' in canvas.getContext ||
    (typeof HTMLCanvasElement !== 'undefined' &&
      'mock' in HTMLCanvasElement.prototype.getContext)
  if (isJsdom && !isMocked) return null

  try {
    return canvas.getContext('2d')
  } catch {
    return null
  }
}

export function buildSceneAriaLabel(scene: SceneState): string {
  if (scene.agents.length === 0) {
    return 'Cena do escritório: nenhum agente em cena'
  }
  const main =
    scene.agents.find((a) => a.isMain || a.id === MAIN_AGENT_ID) ?? scene.agents[0]!
  const mainStation = STATION_LABELS[main.station] ?? main.station
  const mainSummary = `${main.name} ${main.statusLabel} na ${mainStation}`

  const subagents = scene.agents.filter((a) => a.id !== main.id)
  if (subagents.length === 0) {
    return `Cena do escritório: ${mainSummary}`
  }

  const countLabel =
    subagents.length === 1 ? '1 subagente' : `${subagents.length} subagentes`
  const subSummary = subagents
    .map((a) => {
      const st = STATION_LABELS[a.station] ?? a.station
      const charName = a.characterName?.trim()
      const displayLabel =
        charName && charName.toLowerCase() !== a.name.trim().toLowerCase()
          ? `${charName} (${a.name})`
          : a.name
      return `${displayLabel} ${a.statusLabel} (${st})`
    })
    .join(', ')

  return `Cena do escritório: ${mainSummary}; ${countLabel}: ${subSummary}`
}

// ─── Custom character sprite cache (per URL) ─────────────────────────────────

interface CustomSpriteEntry {
  img: HTMLImageElement
  status: 'loading' | 'loaded' | 'error'
}

const customSpriteCache = new Map<string, CustomSpriteEntry>()
const customSpriteListeners = new Set<() => void>()

function notifyCustomSpriteListeners(): void {
  for (const listener of customSpriteListeners) {
    listener()
  }
}

export function subscribeCustomSpriteLoads(listener: () => void): () => void {
  customSpriteListeners.add(listener)
  return () => {
    customSpriteListeners.delete(listener)
  }
}

export function getOrLoadCustomSprite(url: string): HTMLImageElement | null {
  if (!url) return null
  const existing = customSpriteCache.get(url)
  if (existing) {
    return existing.status === 'loaded' ? existing.img : null
  }
  if (typeof Image === 'undefined') return null
  const img = new Image()
  const entry: CustomSpriteEntry = { img, status: 'loading' }
  customSpriteCache.set(url, entry)
  img.decoding = 'async'
  img.addEventListener(
    'load',
    () => {
      entry.status = 'loaded'
      notifyCustomSpriteListeners()
    },
    { once: true },
  )
  img.addEventListener(
    'error',
    () => {
      entry.status = 'error'
    },
    { once: true },
  )
  img.src = url
  if (img.complete && img.naturalWidth > 0) {
    entry.status = 'loaded'
    return img
  }
  return null
}

export function primeCustomSpriteCacheForTest(
  url: string,
  img: HTMLImageElement | null,
): void {
  if (!img) {
    customSpriteCache.delete(url)
    return
  }
  customSpriteCache.set(url, { img, status: 'loaded' })
}

export function clearCustomSpriteCacheForTest(): void {
  customSpriteCache.clear()
}

function drawBackground(ctx: CanvasRenderingContext2D, nowMs: number, live: boolean): void {
  // Base floor
  ctx.fillStyle = SCENE_PALETTE.surface1
  ctx.fillRect(0, 0, SCENE_WIDTH, SCENE_HEIGHT)

  // Top wall (rows 0..2, 36px tall)
  const wallHeight = 36
  ctx.fillStyle = SCENE_PALETTE.surface2
  ctx.fillRect(0, 0, SCENE_WIDTH, wallHeight)

  // Subtle vertical wall panels
  ctx.fillStyle = SCENE_PALETTE.surface3
  for (let x = 0; x < SCENE_WIDTH; x += SCENE_TILE * 2) {
    ctx.fillRect(x, 0, 1, wallHeight - 4)
  }

  // Wall baseboard trim
  ctx.fillStyle = SCENE_PALETTE.wallTrim
  ctx.fillRect(0, wallHeight - 4, SCENE_WIDTH, 3)
  ctx.fillStyle = SCENE_PALETTE.outline
  ctx.fillRect(0, wallHeight - 1, SCENE_WIDTH, 1)

  // Subtle floor tile grid
  ctx.fillStyle = '#17171E'
  for (let y = wallHeight; y < SCENE_HEIGHT; y += SCENE_TILE) {
    for (let x = 0; x < SCENE_WIDTH; x += SCENE_TILE) {
      if (((x / SCENE_TILE + y / SCENE_TILE) & 1) === 0) {
        ctx.fillRect(x, y, SCENE_TILE, SCENE_TILE)
      }
    }
  }

  // Draw station furniture props
  ctx.font = '6px monospace'
  ctx.textBaseline = 'top'
  ctx.textAlign = 'center'

  for (const prop of STATION_PROPS) {
    const sprite = getPropSprite(prop.propId)
    if (sprite) {
      ctx.drawImage(sprite, Math.round(prop.x), Math.round(prop.y))
    } else {
      ctx.fillStyle = SCENE_PALETTE.surface3
      ctx.fillRect(prop.x, prop.y, prop.width, prop.height)
    }

    // Blinking terminal cursor when live
    if (prop.propId === 'terminal' && live && Math.floor(nowMs / 450) % 2 === 0) {
      ctx.fillStyle = SCENE_PALETTE.success
      ctx.fillRect(prop.x + 18, prop.y + 6, 2, 2)
    }

    const propLabel = prop.station
      ? STATION_SHORT_LABELS[prop.station]
      : prop.propId === 'door'
        ? 'Porta'
        : undefined
    if (propLabel) {
      ctx.fillStyle = 'rgba(154, 152, 166, 0.68)'
      ctx.fillText(propLabel, Math.round(prop.x + prop.width / 2), Math.round(prop.y - 7))
    }
  }
}

function drawArtBackground(
  ctx: CanvasRenderingContext2D,
  art: SceneArtImages,
  nowMs: number,
  live: boolean,
  reducedMotion: boolean
): void {
  ctx.drawImage(art.office, 0, 0, SCENE_WIDTH, SCENE_HEIGHT)
  if (!live) return

  const t = reducedMotion ? 0 : nowMs
  const { terminal, browser } = SCENE_SCREENS

  // Screens glow while the team works.
  ctx.save()
  ctx.globalCompositeOperation = 'lighter'
  ctx.globalAlpha = 0.07 + 0.04 * Math.sin(t / 420)
  ctx.fillStyle = SCENE_PALETTE.success
  ctx.fillRect(terminal.x, terminal.y, terminal.width, terminal.height)
  ctx.globalAlpha = 0.06 + 0.04 * Math.sin(t / 560 + 1.7)
  ctx.fillStyle = SCENE_PALETTE.Q
  ctx.fillRect(browser.x, browser.y, browser.width, browser.height)
  ctx.restore()

  // Terminal cursor after the prompt.
  if (Math.floor(t / 500) % 2 === 0) {
    const c = terminal.cursor
    ctx.fillStyle = '#86EFAC'
    ctx.fillRect(c.x, c.y, c.width, c.height)
  }

  // Rack LEDs flicker: a few of them go dark for a moment.
  const tick = Math.floor(t / 350)
  ctx.fillStyle = 'rgba(22, 20, 30, 0.88)'
  for (let i = 0; i < SCENE_ART_RACK_LEDS.length; i += 1) {
    const roll = ((Math.imul(i + 1, 2654435761) ^ Math.imul(tick + 1, 40503)) >>> 0) % 6
    if (roll !== 0) continue
    const led = SCENE_ART_RACK_LEDS[i]!
    ctx.fillRect(led[0], led[1], led[2], led[3])
  }
}

/** Draws the chair backrest again over a seated character. */
function drawBackrest(ctx: CanvasRenderingContext2D, art: SceneArtImages, chair: SceneSeat): void {
  ctx.save()
  ctx.beginPath()
  chair.backrest.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)))
  ctx.closePath()
  ctx.clip()
  ctx.drawImage(art.office, 0, 0, SCENE_WIDTH, SCENE_HEIGHT)
  ctx.restore()
}

function drawSelectionArrow(ctx: CanvasRenderingContext2D, x: number, topY: number, nowMs: number): void {
  const arrowY = Math.round(topY - 7 + (Math.floor(nowMs / 260) % 2))
  ctx.fillStyle = SCENE_PALETTE.orinBase
  ctx.fillRect(x - 2, arrowY, 5, 1)
  ctx.fillRect(x - 1, arrowY + 1, 3, 1)
  ctx.fillRect(x, arrowY + 2, 1, 1)
}

/** Draws a character with the office art; returns the top of the drawing, or null if no frame fits. */
function drawArtCharacter(
  ctx: CanvasRenderingContext2D,
  art: SceneArtImages,
  agent: AnimatedAgent,
  isSelected: boolean,
  nowMs: number,
  reducedMotion: boolean
): number | null {
  const place = placeArtCharacter(agent, nowMs, reducedMotion)
  if (!place) return null
  const f = place.frame

  if (!place.seat) {
    ctx.fillStyle = 'rgba(0, 0, 0, 0.34)'
    ctx.beginPath()
    ctx.ellipse(agent.x, agent.y - 0.5, agent.isMain ? 8 : 7, 2, 0, 0, Math.PI * 2)
    ctx.fill()
  }
  if (isSelected) {
    ctx.strokeStyle = SCENE_PALETTE.orinBase
    ctx.lineWidth = 0.75
    ctx.beginPath()
    ctx.ellipse(agent.x, agent.y - 0.5, agent.isMain ? 11 : 10, 3, 0, 0, Math.PI * 2)
    ctx.stroke()
  }

  if (place.flip) {
    ctx.save()
    ctx.translate(agent.x, 0)
    ctx.scale(-1, 1)
    ctx.drawImage(art.characters, f.x, f.y, f.w, f.h, place.x - agent.x, place.y, place.width, place.height)
    ctx.restore()
  } else {
    ctx.drawImage(art.characters, f.x, f.y, f.w, f.h, place.x, place.y, place.width, place.height)
  }

  if (place.seat) drawBackrest(ctx, art, place.seat)
  if (isSelected) drawSelectionArrow(ctx, agent.x, place.y, nowMs)
  return place.y
}

/**
 * Draws a user-created custom character sprite at the same height as atlas characters (`FIGURE_HEIGHT`),
 * anchored by the feet at `(agent.x, agent.y)` and flipped horizontally when `facing === 'left'`.
 * Returns the top Y of the drawn figure, or `null` while the sprite image has not loaded yet.
 */
function drawCustomCharacter(
  ctx: CanvasRenderingContext2D,
  agent: AnimatedAgent,
  isSelected: boolean,
  live: boolean,
  nowMs: number,
  reducedMotion: boolean,
): number | null {
  if (agent.isMain || !agent.customCharacter?.poses?.idle) return null
  const selected = selectCustomCharacterPose(agent.customCharacter.poses, agent.pose)
  let img = getOrLoadCustomSprite(selected.url)
  let isFallbackIdle = selected.isFallbackIdle
  if (!img && selected.url !== agent.customCharacter.poses.idle) {
    img = getOrLoadCustomSprite(agent.customCharacter.poses.idle)
    isFallbackIdle = agent.pose !== 'idle'
  }
  if (!img) return null

  const targetH = FIGURE_HEIGHT
  const rawW = img.naturalWidth || img.width || 28
  const rawH = img.naturalHeight || img.height || 44
  const aspect = rawW > 0 && rawH > 0 ? rawW / rawH : 28 / 44
  const targetW = Math.max(10, Math.min(44, Math.round(targetH * aspect)))
  const bob = computeFallbackIdleBobOffset(agent.pose, isFallbackIdle, nowMs, reducedMotion)
  const footX = Math.round(agent.x)
  const footY = Math.round(agent.y)
  const drawX = Math.round(footX - targetW / 2)
  const drawY = Math.round(footY - targetH + bob)

  ctx.save()
  ctx.imageSmoothingEnabled = false

  // Ground shadow
  ctx.fillStyle = 'rgba(0, 0, 0, 0.34)'
  if (typeof ctx.ellipse === 'function') {
    ctx.beginPath()
    ctx.ellipse(agent.x, agent.y - 0.5, 7, 2, 0, 0, Math.PI * 2)
    ctx.fill()
  } else {
    ctx.fillRect(footX - 6, footY - 2, 12, 3)
  }

  if (isSelected) {
    ctx.strokeStyle = SCENE_PALETTE.orinBase
    ctx.lineWidth = 0.75
    if (typeof ctx.ellipse === 'function') {
      ctx.beginPath()
      ctx.ellipse(agent.x, agent.y - 0.5, 10, 3, 0, 0, Math.PI * 2)
      ctx.stroke()
    } else {
      ctx.strokeRect(drawX - 2, drawY - 1, targetW + 4, targetH + 2)
    }
  }

  if (agent.facing === 'left') {
    ctx.save()
    ctx.translate(agent.x, 0)
    ctx.scale(-1, 1)
    ctx.drawImage(img, -targetW / 2, drawY, targetW, targetH)
    ctx.restore()
  } else {
    ctx.drawImage(img, drawX, drawY, targetW, targetH)
  }

  if (isSelected) {
    drawSelectionArrow(ctx, agent.x, drawY, nowMs)
  }

  if (agent.pose === 'raise_hand') {
    const signSprite = getPropSprite('question_sign')
    if (signSprite) {
      ctx.drawImage(signSprite, footX + 3, drawY - 10)
    }
  }

  if (agent.pose === 'sit') {
    const zzzSprite = getPropSprite('zzz')
    if (zzzSprite && (!live || agent.status === 'resting')) {
      const floatY = Math.floor(nowMs / 600) % 2
      ctx.drawImage(zzzSprite, footX + 2, drawY - 8 - floatY)
    }
  }

  ctx.restore()
  return drawY
}

function drawCharacter(
  ctx: CanvasRenderingContext2D,
  agent: AnimatedAgent,
  isSelected: boolean,
  live: boolean,
  nowMs: number
): void {
  const footX = Math.round(agent.x)
  const footY = Math.round(agent.y)
  const drawX = Math.round(footX - CHARACTER_W / 2)
  const drawY = Math.round(footY - CHARACTER_H)

  // Ground shadow
  ctx.fillStyle = 'rgba(0, 0, 0, 0.38)'
  ctx.fillRect(footX - 6, footY - 2, 12, 3)

  // Selection highlight ring & floating top arrow
  if (isSelected) {
    ctx.strokeStyle = SCENE_PALETTE.orinBase
    ctx.lineWidth = 1
    ctx.strokeRect(drawX - 2.5, drawY - 1.5, CHARACTER_W + 5, CHARACTER_H + 3)

    const arrowBob = Math.floor(nowMs / 260) % 2
    const arrowY = drawY - 7 + arrowBob
    ctx.fillStyle = SCENE_PALETTE.orinBase
    ctx.fillRect(footX - 2, arrowY, 5, 1)
    ctx.fillRect(footX - 1, arrowY + 1, 3, 1)
    ctx.fillRect(footX, arrowY + 2, 1, 1)
  }

  // Character sprite (flipped horizontally when facing left)
  const sprite = getCharacterSprite(agent.avatar, agent.pose, agent.frame)
  if (sprite) {
    if (agent.facing === 'left') {
      ctx.save()
      ctx.translate(footX, drawY)
      ctx.scale(-1, 1)
      ctx.drawImage(sprite, -CHARACTER_W / 2, 0)
      ctx.restore()
    } else {
      ctx.drawImage(sprite, drawX, drawY)
    }
  } else {
    ctx.fillStyle = agent.isMain ? SCENE_PALETTE.orinBase : SCENE_PALETTE.info
    ctx.fillRect(drawX + 3, drawY + 4, CHARACTER_W - 6, CHARACTER_H - 4)
  }

  // Floating "?" sign prop when waiting for permission/question
  if (agent.pose === 'raise_hand') {
    const signSprite = getPropSprite('question_sign')
    if (signSprite) {
      ctx.drawImage(signSprite, footX + 3, drawY - 10)
    }
  }

  // Floating "zzz" on the sofa when resting
  if (agent.pose === 'sit') {
    const zzzSprite = getPropSprite('zzz')
    if (zzzSprite && (!live || agent.status === 'resting')) {
      const floatY = Math.floor(nowMs / 600) % 2
      ctx.drawImage(zzzSprite, footX + 2, drawY - 8 - floatY)
    }
  }

}

function drawCharacterNameTag(
  ctx: CanvasRenderingContext2D,
  agent: AnimatedAgent,
  box: PlacedSceneLabelBox,
  isSelected: boolean
): void {
  if (!box.visible) return
  const meta = computeAgentNameTagMeta(agent)
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  ctx.fillStyle = isSelected ? 'rgba(180, 164, 239, 0.26)' : 'rgba(16, 16, 20, 0.78)'
  ctx.fillRect(box.x, box.y, box.width, box.height)
  const centerX = Math.round(box.x + box.width / 2)
  ctx.font = '6px monospace'
  ctx.fillStyle = isSelected ? SCENE_PALETTE.orinLight : '#F4F3F8'
  ctx.fillText(meta.primaryLabel, centerX, box.y + 1)
  if (meta.twoLine && meta.secondaryLabel) {
    ctx.font = '4.8px monospace'
    ctx.fillStyle = isSelected ? 'rgba(216, 206, 255, 0.78)' : 'rgba(154, 152, 166, 0.78)'
    ctx.fillText(meta.secondaryLabel, centerX, box.y + 8)
  }
}

function drawTransfer(ctx: CanvasRenderingContext2D, tr: ActiveTransfer, propScale: number): void {
  const propId =
    tr.kind === 'message'
      ? 'envelope'
      : tr.kind === 'failed_delegation'
        ? 'crumpled_paper'
        : 'paper'
  const sprite = getPropSprite(propId)
  const dims = getPropDimensions(propId)
  const width = dims.width * propScale
  const height = dims.height * propScale
  const x = Math.round(tr.x - width / 2)
  const y = Math.round(tr.y - height / 2)

  if (sprite) {
    ctx.drawImage(sprite, x, y, width, height)
  } else {
    ctx.fillStyle = tr.kind === 'failed_delegation' ? SCENE_PALETTE.danger : SCENE_PALETTE.paper
    ctx.fillRect(x, y, width, height)
  }
}

function drawEffect(ctx: CanvasRenderingContext2D, fx: ActiveEffect, propScale: number): void {
  const sprite = getPropSprite(fx.kind)
  const dims = getPropDimensions(fx.kind)
  const width = dims.width * propScale
  const height = dims.height * propScale
  const x = Math.round(fx.x - width / 2)
  const y = Math.round(fx.y - height / 2)

  if (sprite) {
    ctx.drawImage(sprite, x, y, width, height)
  } else {
    ctx.fillStyle = fx.kind === 'check' ? SCENE_PALETTE.success : SCENE_PALETTE.danger
    ctx.fillRect(x, y, width, height)
  }
}

export const MAX_CANVAS_BUBBLE_CHARS = 26

/**
 * Formats bubble text for the canvas (~26 chars) with a single ellipsis:
 * keeps the head with a trailing "…" for speech/error, or the tail with a leading "…" for thoughts.
 */
export function bubbleDisplayText(text: string, maxChars = MAX_CANVAS_BUBBLE_CHARS): string {
  const raw = text.trim()
  if (!raw) return ''
  if (raw === '…') return '…'
  if (raw.startsWith('…')) {
    const cleanTail = raw.replace(/^…+\s*/, '').replace(/\s*…+$/, '')
    if (!cleanTail) return '…'
    const sliceLen = Math.max(1, maxChars - 1)
    if (cleanTail.length <= sliceLen) return `…${cleanTail}`
    return `…${cleanTail.slice(-sliceLen)}`
  }
  if (raw.length <= maxChars) return raw
  const withoutTrailing = raw.replace(/\s*…+$/, '')
  return `${withoutTrailing.slice(0, Math.max(1, maxChars - 1))}…`
}

export function computeBubbleBox(
  agent: Pick<AnimatedAgent, 'x' | 'y' | 'bubble' | 'bubblePopProgress'>,
  headTop: number = agent.y - CHARACTER_H
): {
  boxX: number
  boxY: number
  boxW: number
  boxH: number
} | null {
  const bubble = agent.bubble
  if (!bubble) return null
  const display = bubbleDisplayText(bubble.text)
  if (!display) return null
  const boxW = Math.min(118, Math.max(28, display.length * 4 + 10))
  const boxH = 13
  const popOffset = Math.round((1 - agent.bubblePopProgress) * 3)
  const anchorX = Math.max(
    boxW / 2 + 4,
    Math.min(SCENE_WIDTH - boxW / 2 - 4, Math.round(agent.x))
  )
  const boxX = Math.round(anchorX - boxW / 2)
  const boxY = Math.max(4, Math.round(headTop - boxH - 5 + popOffset))
  return { boxX, boxY, boxW, boxH }
}

/**
 * Hit-tests active speech/thought/error bubbles in the scene.
 * Checks both direct `computeBubbleBox` coordinates and anti-collision placed boxes.
 */
export function hitTestBubble(
  anim: SceneAnimatorState,
  x: number,
  y: number,
  selectedAgentId: string | null = null
): string | null {
  const visible = Array.from(anim.agents.values()).filter(
    (a) => !a.exited && a.bubble !== null
  )
  if (visible.length === 0) return null
  visible.sort((a, b) => a.y - b.y)

  for (let i = visible.length - 1; i >= 0; i -= 1) {
    const agent = visible[i]!
    const recordedTop = headTopScratch.get(agent.id)
    const defaultTop = agent.y - CHARACTER_H
    const tops =
      recordedTop !== undefined && recordedTop !== defaultTop
        ? [recordedTop, defaultTop]
        : [defaultTop]
    for (const top of tops) {
      const box = computeBubbleBox(agent, top)
      if (
        box &&
        x >= box.boxX &&
        x <= box.boxX + box.boxW &&
        y >= box.boxY &&
        y <= box.boxY + box.boxH
      ) {
        return agent.id
      }
    }
  }

  const inputs: SceneLabelBox[] = []
  for (let i = 0; i < visible.length; i += 1) {
    const agent = visible[i]!
    const info = computeBubbleBox(
      agent,
      headTopScratch.get(agent.id) ?? agent.y - CHARACTER_H
    )
    if (!info) continue
    inputs.push({
      id: agent.id,
      x: info.boxX,
      y: info.boxY,
      width: info.boxW,
      height: info.boxH,
      priority: agent.id === selectedAgentId,
    })
  }

  if (inputs.length > 0) {
    const placed = layoutLabels(inputs, {
      minY: 2,
      maxY: SCENE_HEIGHT - 6,
      stepY: 15,
      maxAttempts: 4,
      direction: 'up',
      hideOnOverflow: false,
    })
    for (let i = placed.length - 1; i >= 0; i -= 1) {
      const b = placed[i]!
      if (x >= b.x && x <= b.x + b.width && y >= b.y && y <= b.y + b.height) {
        return b.id
      }
    }
  }

  return null
}

/** White rounded bubble with a dark outline, matching the bubbles of the office art. */
function drawPaintedBubble(
  ctx: CanvasRenderingContext2D,
  agentX: number,
  kind: 'thought' | 'speech' | 'error',
  display: string,
  boxX: number,
  boxY: number,
  boxW: number,
  boxH: number
): void {
  const fill = kind === 'error' ? '#FEE2E2' : '#F4F3F8'
  const outline = kind === 'error' ? '#B91C1C' : SCENE_PALETTE.outline
  const tailX = Math.max(boxX + 5, Math.min(boxX + boxW - 5, agentX))
  const bottom = boxY + boxH

  ctx.save()
  ctx.lineWidth = 0.9
  ctx.strokeStyle = outline
  ctx.fillStyle = fill
  ctx.beginPath()
  if (typeof ctx.roundRect === 'function') {
    ctx.roundRect(boxX, boxY, boxW, boxH, 4)
  } else {
    ctx.rect(boxX, boxY, boxW, boxH)
  }
  ctx.fill()
  ctx.stroke()

  if (kind === 'thought') {
    for (const [dx, dy, r] of [[0, 2.6, 1.4], [1.2, 5.4, 0.9]] as const) {
      ctx.beginPath()
      ctx.arc(tailX + dx, bottom + dy, r, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
    }
  } else {
    // Tail: outline first, then cover the seam with the fill.
    ctx.beginPath()
    ctx.moveTo(tailX - 2.5, bottom - 0.5)
    ctx.lineTo(tailX, bottom + 3.5)
    ctx.lineTo(tailX + 2.5, bottom - 0.5)
    ctx.fill()
    ctx.stroke()
    ctx.fillRect(tailX - 2, bottom - 1.4, 4, 1.2)
  }

  ctx.font = '600 5.5px system-ui, sans-serif'
  ctx.textAlign = 'left'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = kind === 'error' ? '#991B1B' : SCENE_PALETTE.outline
  ctx.fillText(display, boxX + 4, boxY + boxH / 2 + 0.3, boxW - 8)
  ctx.restore()
}

function drawBubble(
  ctx: CanvasRenderingContext2D,
  agent: AnimatedAgent,
  boxX: number,
  boxY: number,
  boxW: number,
  boxH: number,
  painted: boolean
): void {
  const bubble = agent.bubble
  if (!bubble) return
  const display = bubbleDisplayText(bubble.text)
  if (!display) return

  if (painted) {
    drawPaintedBubble(ctx, agent.x, bubble.kind, display, boxX, boxY, boxW, boxH)
    return
  }

  const borderColor =
    bubble.kind === 'error'
      ? SCENE_PALETTE.danger
      : bubble.kind === 'speech'
        ? SCENE_PALETTE.orinBase
        : '#9A98A6'

  // Background & pixel border
  ctx.fillStyle = 'rgba(20, 20, 25, 0.94)'
  ctx.fillRect(boxX, boxY, boxW, boxH)
  ctx.strokeStyle = borderColor
  ctx.lineWidth = 1
  ctx.strokeRect(boxX + 0.5, boxY + 0.5, boxW - 1, boxH - 1)

  // Tail / thought dots
  const tailX = Math.max(boxX + 4, Math.min(boxX + boxW - 4, Math.round(agent.x)))
  if (bubble.kind === 'thought') {
    ctx.fillStyle = borderColor
    ctx.fillRect(tailX - 1, boxY + boxH + 1, 2, 2)
    ctx.fillRect(tailX, boxY + boxH + 4, 1, 1)
  } else {
    ctx.fillStyle = borderColor
    ctx.fillRect(tailX - 1, boxY + boxH, 3, 2)
  }

  ctx.font = '6px monospace'
  ctx.textAlign = 'left'
  ctx.textBaseline = 'middle'
  ctx.fillStyle = bubble.kind === 'error' ? '#FCA5A5' : '#F4F3F8'
  ctx.fillText(display, boxX + 4, Math.round(boxY + boxH / 2 + 0.5), boxW - 8)
}

/**
 * Sizes the backing store for the displayed size and the device, and returns its pixels
 * per scene pixel. With the office art the backing follows the real display size; the
 * hand-drawn fallback keeps whole pixels.
 */
function prepareCanvas(canvas: HTMLCanvasElement, displayScale: number): number {
  const dpr =
    typeof window !== 'undefined' && Number.isFinite(window.devicePixelRatio)
      ? Math.max(1, window.devicePixelRatio)
      : 1
  const pixelsPerUnit = getSceneArt()
    ? Math.max(1, displayScale * dpr)
    : Math.max(1, Math.floor(dpr))
  const targetW = Math.round(SCENE_WIDTH * pixelsPerUnit)
  const targetH = Math.round(SCENE_HEIGHT * pixelsPerUnit)
  if (canvas.width !== targetW || canvas.height !== targetH) {
    canvas.width = targetW
    canvas.height = targetH
  }
  return targetW / SCENE_WIDTH
}

// Every draw goes through here so the sync render and the loop use the same transform.
function drawScaledFrame(
  ctx: CanvasRenderingContext2D,
  pixelsPerUnit: number,
  anim: SceneAnimatorState,
  selectedAgentId: string | null | undefined,
  nowMs: number,
  reducedMotion: boolean
): void {
  ctx.save()
  ctx.setTransform(pixelsPerUnit, 0, 0, pixelsPerUnit, 0, 0)
  renderSceneFrame(ctx, anim, selectedAgentId, nowMs, reducedMotion)
  ctx.restore()
}

function renderSceneFrame(
  ctx: CanvasRenderingContext2D,
  anim: SceneAnimatorState,
  selectedAgentId: string | null | undefined,
  nowMs: number,
  reducedMotion: boolean
): void {
  const art = getSceneArt()
  ctx.save()
  // The painted art is resampled smoothly; the hand-drawn fallback keeps hard pixels.
  ctx.imageSmoothingEnabled = art !== null
  if (art) ctx.imageSmoothingQuality = 'high'

  if (art) {
    drawArtBackground(ctx, art, nowMs, anim.live, reducedMotion)
  } else {
    drawBackground(ctx, nowMs, anim.live)
  }

  visibleAgentsScratch.length = 0
  for (const agent of anim.agents.values()) {
    if (!agent.exited) visibleAgentsScratch.push(agent)
  }
  visibleAgentsScratch.sort((a, b) => a.y - b.y)

  headTopScratch.clear()
  for (let i = 0; i < visibleAgentsScratch.length; i += 1) {
    const agent = visibleAgentsScratch[i]!
    const isSelected = agent.id === selectedAgentId
    const customTop = drawCustomCharacter(
      ctx,
      agent,
      isSelected,
      anim.live,
      nowMs,
      reducedMotion,
    )
    if (customTop !== null) {
      headTopScratch.set(agent.id, customTop)
      continue
    }
    const artTop = art ? drawArtCharacter(ctx, art, agent, isSelected, nowMs, reducedMotion) : null
    if (artTop === null) {
      drawCharacter(ctx, agent, isSelected, anim.live, nowMs)
      headTopScratch.set(agent.id, agent.y - CHARACTER_H)
    } else {
      headTopScratch.set(agent.id, artTop)
    }
  }
  ctx.imageSmoothingEnabled = false

  nameInputScratch.length = visibleAgentsScratch.length
  for (let i = 0; i < visibleAgentsScratch.length; i += 1) {
    const agent = visibleAgentsScratch[i]!
    const meta = computeAgentNameTagMeta(agent)
    setScratchBox(
      nameInputScratch,
      i,
      agent.id,
      meta.x,
      meta.y,
      meta.width,
      meta.height,
      agent.id === selectedAgentId,
    )
  }

  const placedNames = layoutLabels(
    nameInputScratch,
    {
      minY: 2,
      maxY: SCENE_HEIGHT - 1,
      stepY: computeNameTagStepY(nameInputScratch),
      maxAttempts: 3,
      direction: 'down',
      hideOnOverflow: true,
    },
    namePlacedScratch
  )
  for (let i = 0; i < placedNames.length; i += 1) {
    const box = placedNames[i]!
    if (!box.visible) continue
    const agent = anim.agents.get(box.id)
    if (agent) drawCharacterNameTag(ctx, agent, box, agent.id === selectedAgentId)
  }

  const propScale = art ? ART_PROP_SCALE : 1
  for (const tr of anim.activeTransfers) drawTransfer(ctx, tr, propScale)
  for (const fx of anim.activeEffects) drawEffect(ctx, fx, propScale)

  let bubbleCount = 0
  for (let i = 0; i < visibleAgentsScratch.length; i += 1) {
    const agent = visibleAgentsScratch[i]!
    const info = computeBubbleBox(agent, headTopScratch.get(agent.id) ?? agent.y - CHARACTER_H)
    if (!info) continue
    setScratchBox(bubbleInputScratch, bubbleCount, agent.id, info.boxX, info.boxY, info.boxW, info.boxH, agent.id === selectedAgentId)
    bubbleCount += 1
  }
  bubbleInputScratch.length = bubbleCount

  if (bubbleCount > 0) {
    const placedBubbles = layoutLabels(
      bubbleInputScratch,
      { minY: 2, maxY: SCENE_HEIGHT - 6, stepY: 15, maxAttempts: 4, direction: 'up', hideOnOverflow: false },
      bubblePlacedScratch
    )
    for (let i = 0; i < placedBubbles.length; i += 1) {
      const box = placedBubbles[i]!
      const agent = anim.agents.get(box.id)
      if (agent) drawBubble(ctx, agent, box.x, box.y, box.width, box.height, art !== null)
    }
  }

  // Dim overlay when office lights are off
  if (!anim.lightsOn) {
    ctx.fillStyle = art ? 'rgba(8, 8, 18, 0.42)' : 'rgba(10, 10, 16, 0.36)'
    ctx.fillRect(0, 0, SCENE_WIDTH, SCENE_HEIGHT)
  }

  // Brief red/amber blink when a turn fails or is cancelled
  if (anim.alarmBlinkRemainingMs > 0) {
    ctx.fillStyle = 'rgba(248, 113, 113, 0.22)'
    ctx.fillRect(0, 0, SCENE_WIDTH, SCENE_HEIGHT)
  }

  ctx.restore()
}

export function SceneCanvas({
  scene,
  selectedAgentId = null,
  onSelectAgent,
  onOpenAgentModal,
  soundEnabled = false,
  reducedMotion: reducedMotionProp,
  className,
}: SceneCanvasProps): JSX.Element {
  const frameRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const animatorRef = useRef<SceneAnimatorState | null>(null)

  const [systemReducedMotion, setSystemReducedMotion] = useState<boolean>(() =>
    readSystemReducedMotion()
  )
  const [scale, setScale] = useState<number>(2)
  // Latest scale for the scene-sync draw, which must not re-run (and re-sync) on resizes.
  const scaleRef = useRef(scale)
  useEffect(() => {
    scaleRef.current = scale
  }, [scale])
  const [artReady, setArtReady] = useState<boolean>(() => getSceneArt() !== null)
  const [customSpriteRev, setCustomSpriteRev] = useState<number>(0)
  const [hoveringAgent, setHoveringAgent] = useState<boolean>(false)
  const [isIntersecting, setIsIntersecting] = useState<boolean>(true)
  const [isDocumentVisible, setIsDocumentVisible] = useState<boolean>(() =>
    typeof document !== 'undefined' ? !document.hidden : true
  )

  const effectiveReducedMotion =
    typeof reducedMotionProp === 'boolean' ? reducedMotionProp : systemReducedMotion

  // Listen to prefers-reduced-motion media query when prop is omitted
  useEffect(() => {
    if (typeof reducedMotionProp === 'boolean') return
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return

    let mql: MediaQueryList | null = null
    try {
      mql = window.matchMedia('(prefers-reduced-motion: reduce)')
    } catch {
      return
    }
    if (!mql) return

    const onChange = (event: MediaQueryListEvent) => {
      setSystemReducedMotion(Boolean(event.matches))
    }

    if (typeof mql.addEventListener === 'function') {
      mql.addEventListener('change', onChange)
      return () => mql?.removeEventListener('change', onChange)
    }
    if (typeof mql.addListener === 'function') {
      mql.addListener(onChange)
      return () => mql?.removeListener(onChange)
    }
    return undefined
  }, [reducedMotionProp])

  // Load the office art once; until then the hand-drawn office is shown.
  useEffect(() => {
    if (artReady) return undefined
    return loadSceneArt(() => setArtReady(true))
  }, [artReady])

  useEffect(() => {
    return subscribeCustomSpriteLoads(() => {
      setCustomSpriteRev((prev) => prev + 1)
    })
  }, [])

  // Track the display scale via ResizeObserver: the painted office fills the available
  // width; the hand-drawn fallback uses the largest whole factor so its pixels stay even.
  useEffect(() => {
    const container = frameRef.current
    if (!container || typeof ResizeObserver === 'undefined') return

    const updateScale = (width: number) => {
      if (!Number.isFinite(width) || width <= 0) return
      const raw = width / SCENE_WIDTH
      const nextScale = artReady
        ? Math.max(1, Math.floor(raw * 100) / 100)
        : Math.max(1, Math.floor(raw))
      setScale((prev) => (prev === nextScale ? prev : nextScale))
    }

    const rect = container.getBoundingClientRect()
    const initialWidth = rect.width > 0 ? rect.width : container.clientWidth
    updateScale(initialWidth)

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      const measuredWidth =
        entry.contentRect.width > 0
          ? entry.contentRect.width
          : container.getBoundingClientRect().width
      updateScale(measuredWidth)
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [artReady])

  // Track document visibility (pause loop when tab is hidden)
  useEffect(() => {
    if (typeof document === 'undefined') return
    const onVisibilityChange = () => {
      setIsDocumentVisible(!document.hidden)
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [])

  // Track viewport visibility via IntersectionObserver
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || typeof IntersectionObserver === 'undefined') return

    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0]
        if (entry) {
          setIsIntersecting(entry.isIntersecting)
        }
      },
      { threshold: 0.05 }
    )
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [])

  // Sync SceneState into the pure animator and play sound cues if enabled
  useEffect(() => {
    if (!animatorRef.current) {
      animatorRef.current = createAnimator()
    }
    const now = typeof performance !== 'undefined' ? performance.now() : Date.now()
    syncAnimator(animatorRef.current, scene, now, {
      reducedMotion: effectiveReducedMotion,
    })

    const cues = drainSoundCues(animatorRef.current)
    if (soundEnabled) {
      for (const cue of cues) {
        playSceneSound(cue)
      }
    }

    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = getSafe2dContext(canvas)
    if (!ctx) return
    drawScaledFrame(
      ctx,
      prepareCanvas(canvas, scaleRef.current),
      animatorRef.current,
      selectedAgentId,
      now,
      effectiveReducedMotion
    )
  }, [scene, effectiveReducedMotion, soundEnabled, selectedAgentId, artReady, customSpriteRev])

  // Main requestAnimationFrame loop (only runs when 2D context exists and canvas is visible)
  useEffect(() => {
    if (!isDocumentVisible || !isIntersecting) return
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = getSafe2dContext(canvas)
    if (!ctx) return

    if (!animatorRef.current) {
      animatorRef.current = createAnimator()
    }

    const pixelsPerUnit = prepareCanvas(canvas, scale)

    let rafId = 0
    let lastTime = typeof performance !== 'undefined' ? performance.now() : Date.now()

    const tick = (now: number) => {
      const dt = Math.min(100, Math.max(0, now - lastTime))
      lastTime = now

      const anim = animatorRef.current!
      stepAnimator(anim, dt, { reducedMotion: effectiveReducedMotion })

      const cues = drainSoundCues(anim)
      if (soundEnabled) {
        for (const cue of cues) {
          playSceneSound(cue)
        }
      }

      drawScaledFrame(ctx, pixelsPerUnit, anim, selectedAgentId, now, effectiveReducedMotion)

      rafId = window.requestAnimationFrame(tick)
    }

    rafId = window.requestAnimationFrame(tick)
    return () => {
      window.cancelAnimationFrame(rafId)
    }
  }, [
    isDocumentVisible,
    isIntersecting,
    effectiveReducedMotion,
    soundEnabled,
    selectedAgentId,
    scale,
    artReady,
  ])

  const clientToLogical = (
    event: ReactMouseEvent<HTMLCanvasElement>
  ): { x: number; y: number } | null => {
    const canvas = canvasRef.current
    if (!canvas) return null
    const rect = canvas.getBoundingClientRect()
    const width = rect.width > 0 ? rect.width : SCENE_WIDTH * scale
    const height = rect.height > 0 ? rect.height : SCENE_HEIGHT * scale
    const x = ((event.clientX - rect.left) / width) * SCENE_WIDTH
    const y = ((event.clientY - rect.top) / height) * SCENE_HEIGHT
    return { x, y }
  }

  const handleCanvasClick = (event: ReactMouseEvent<HTMLCanvasElement>) => {
    if ((!onSelectAgent && !onOpenAgentModal) || !animatorRef.current) return
    const pt = clientToLogical(event)
    if (!pt) return
    const hitId =
      hitTest(animatorRef.current, pt.x, pt.y, selectedAgentId) ??
      hitTestBubble(animatorRef.current, pt.x, pt.y, selectedAgentId)
    if (hitId) {
      onSelectAgent?.(hitId)
      onOpenAgentModal?.(hitId)
    }
  }

  const handleCanvasKeyDown = (event: ReactKeyboardEvent<HTMLCanvasElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      if (selectedAgentId) {
        event.preventDefault()
        onSelectAgent?.(selectedAgentId)
        onOpenAgentModal?.(selectedAgentId)
      }
    }
  }

  const handlePointerMove = (event: ReactMouseEvent<HTMLCanvasElement>) => {
    if (!animatorRef.current) return
    const pt = clientToLogical(event)
    if (!pt) return
    const hitId =
      hitTest(animatorRef.current, pt.x, pt.y, selectedAgentId) ??
      hitTestBubble(animatorRef.current, pt.x, pt.y, selectedAgentId)
    setHoveringAgent(Boolean(hitId))
  }

  const ariaLabel = buildSceneAriaLabel(scene)
  const frameClassName = `scene-canvas-frame${className ? ` ${className}` : ''}`
  const canvasClassName = `scene-canvas${hoveringAgent ? ' scene-canvas--interactive' : ''}`

  return (
    <div
      ref={frameRef}
      className={frameClassName}
      data-testid="scene-canvas-frame"
    >
      <canvas
        ref={canvasRef}
        role="img"
        tabIndex={0}
        aria-label={ariaLabel}
        data-testid="scene-canvas"
        className={canvasClassName}
        width={SCENE_WIDTH}
        height={SCENE_HEIGHT}
        style={{
          width: `${Math.round(SCENE_WIDTH * scale)}px`,
          height: `${Math.round(SCENE_HEIGHT * scale)}px`,
        }}
        onClick={handleCanvasClick}
        onKeyDown={handleCanvasKeyDown}
        onMouseMove={handlePointerMove}
        onMouseLeave={() => setHoveringAgent(false)}
      />
    </div>
  )
}

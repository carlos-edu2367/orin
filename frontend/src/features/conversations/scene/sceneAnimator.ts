import { DOOR, FIGURE_HALF_WIDTH, figureHeight, stationSlot } from './sceneLayout'
import type { SceneSoundCue } from './sceneAudio'
import {
  ART_PERSON_LOOKS,
  assignAvatars,
  fnv1a32,
  poseFrameCount,
  type SceneAvatar,
} from './sceneSprites'
import {
  MAIN_AGENT_ID,
  type SceneAgentStatus,
  type SceneBubble,
  type SceneEffectKind,
  type ScenePose,
  type SceneState,
  type SceneStationId,
  type SceneTransferKind,
} from './sceneTypes'

export type { SceneSoundCue } from './sceneAudio'

export const WALK_SPEED_PX_PER_SEC = 110
export const MAX_WALK_DURATION_SEC = 1.2
export const TRANSFER_DURATION_MS = 900
export const EFFECT_DURATION_MS = 700
export const BUBBLE_TTL_MS = 5000
export const EXIT_DELAY_MS = 4000
export const EXIT_WALK_START_MS = 1600
export const ALARM_BLINK_DURATION_MS = 600

export interface AnimatorOptions {
  readonly reducedMotion: boolean
}

export interface AnimatedAgent {
  id: string
  isMain: boolean
  name: string
  status: SceneAgentStatus
  statusLabel: string
  station: SceneStationId
  x: number
  y: number
  targetX: number
  targetY: number
  walkSpeed: number
  facing: 'left' | 'right'
  pose: ScenePose
  desiredPose: ScenePose
  frame: number
  frameElapsedMs: number
  avatar: 'orin' | SceneAvatar
  /** Which person of the office art this subagent is (0..ART_PERSON_LOOKS-1); fixed for its lifetime. */
  look: number
  bubble: SceneBubble | null
  lastBubbleEventId: string | null
  bubbleAgeMs: number
  bubblePopProgress: number
  settledElapsedMs: number | null
  exitingToDoor: boolean
  exited: boolean
  currentTool: string | null
}

export interface ActiveTransfer {
  id: string
  kind: SceneTransferKind
  fromId: string
  toId: string
  fromX: number
  fromY: number
  toX: number
  toY: number
  x: number
  y: number
  elapsedMs: number
  durationMs: number
  label?: string
}

export interface ActiveEffect {
  id: string
  kind: SceneEffectKind
  agentId: string
  station: SceneStationId
  x: number
  y: number
  elapsedMs: number
  durationMs: number
}

export interface SceneAnimatorState {
  live: boolean
  lightsOn: boolean
  alarmBlinkRemainingMs: number
  seenAlarmEventId: string | null
  agents: Map<string, AnimatedAgent>
  activeTransfers: ActiveTransfer[]
  activeEffects: ActiveEffect[]
  seenTransferIds: Set<string>
  seenEffectIds: Set<string>
  soundQueue: SceneSoundCue[]
  lastSyncNowMs: number | null
}

/** Creates an empty, pure `SceneAnimatorState` instance. */
export function createAnimator(): SceneAnimatorState {
  return {
    live: false,
    lightsOn: false,
    alarmBlinkRemainingMs: 0,
    seenAlarmEventId: null,
    agents: new Map(),
    activeTransfers: [],
    activeEffects: [],
    seenTransferIds: new Set(),
    seenEffectIds: new Set(),
    soundQueue: [],
    lastSyncNowMs: null,
  }
}

function enqueueSound(anim: SceneAnimatorState, cue: SceneSoundCue): void {
  anim.soundQueue.push(cue)
}

function computeEffectiveWalkSpeed(distPx: number): number {
  if (!Number.isFinite(distPx) || distPx <= 0) {
    return WALK_SPEED_PX_PER_SEC
  }
  return Math.max(WALK_SPEED_PX_PER_SEC, distPx / MAX_WALK_DURATION_SEC)
}

function isTerminalStatus(status: SceneAgentStatus): boolean {
  return status === 'completed' || status === 'failed' || status === 'stopped'
}

/** Picks the agent's preferred look unless someone in the office already wears it. */
function chooseLook(anim: SceneAnimatorState, agentId: string): number {
  const preferred = fnv1a32(agentId) % ART_PERSON_LOOKS
  const inUse = new Set<number>()
  for (const other of anim.agents.values()) {
    if (!other.isMain && !other.exited) inUse.add(other.look)
  }
  for (let i = 0; i < ART_PERSON_LOOKS; i += 1) {
    const look = (preferred + i) % ART_PERSON_LOOKS
    if (!inUse.has(look)) return look
  }
  return preferred
}

/**
 * Synchronizes the pure animator state with a new `SceneState` snapshot.
 * Mutates `anim` in place and also returns it for convenience.
 */
export function syncAnimator(
  anim: SceneAnimatorState,
  scene: SceneState,
  nowMs: number,
  opts: AnimatorOptions
): SceneAnimatorState {
  // The first sync shows the scene as it is now: what already happened is not replayed.
  const isFirstSync = anim.lastSyncNowMs === null
  const syncDeltaMs =
    anim.lastSyncNowMs !== null && nowMs > anim.lastSyncNowMs
      ? nowMs - anim.lastSyncNowMs
      : 0
  anim.lastSyncNowMs = nowMs

  anim.live = scene.live
  anim.lightsOn = scene.lightsOn
  // Someone holds the sign after the turn ended: keep the reducer's station/pose instead of the sofa.
  const keepsPlaces = scene.live || scene.waitingUser

  if (isFirstSync) {
    for (const tr of scene.transfers) anim.seenTransferIds.add(tr.id)
    for (const fx of scene.effects) anim.seenEffectIds.add(fx.id)
    anim.seenAlarmEventId = scene.alarmEventId
  }

  // Blink light once on a new alarmEventId (disabled under reducedMotion)
  if (scene.alarmEventId && scene.alarmEventId !== anim.seenAlarmEventId) {
    anim.seenAlarmEventId = scene.alarmEventId
    if (!opts.reducedMotion) {
      anim.alarmBlinkRemainingMs = ALARM_BLINK_DURATION_MS
    } else {
      anim.alarmBlinkRemainingMs = 0
    }
  }

  // Assign collision-free avatars to all non-main agents
  const subagentIds = scene.agents
    .filter((a) => !a.isMain && a.id !== MAIN_AGENT_ID)
    .map((a) => a.id)
  const avatarMap = assignAvatars(subagentIds)

  // Count station slots in agent order
  const stationCounts = new Map<SceneStationId, number>()
  const presentIds = new Set<string>()

  for (const src of scene.agents) {
    presentIds.add(src.id)

    const isMain = src.isMain || src.id === MAIN_AGENT_ID
    const effectiveStation: SceneStationId = keepsPlaces ? src.station : 'lounge'
    const desiredPose: ScenePose = keepsPlaces ? src.pose : 'sit'

    const existing = anim.agents.get(src.id)

    // Advance settled timer if time jumped between syncAnimator calls
    if (existing && syncDeltaMs > 0 && existing.settledElapsedMs !== null && scene.live) {
      existing.settledElapsedMs += syncDeltaMs
      if (existing.settledElapsedMs >= EXIT_DELAY_MS) {
        existing.exitingToDoor = true
        existing.exited = true
        existing.x = DOOR.x
        existing.y = DOOR.y
      } else if (existing.settledElapsedMs >= EXIT_WALK_START_MS) {
        existing.exitingToDoor = true
      }
    }

    const willExitToDoor =
      scene.live && !isMain && Boolean(existing?.exitingToDoor || existing?.exited)

    let target: { x: number; y: number }
    if (willExitToDoor) {
      target = { x: DOOR.x, y: DOOR.y }
    } else {
      const slotIdx = stationCounts.get(effectiveStation) ?? 0
      stationCounts.set(effectiveStation, slotIdx + 1)
      target = stationSlot(effectiveStation, slotIdx)
    }

    const avatar: 'orin' | SceneAvatar = isMain
      ? 'orin'
      : (avatarMap.get(src.id) ?? avatarMap.values().next().value!)

    if (!existing) {
      // Newly discovered character
      const spawnAtDoor = scene.live && !isMain && !opts.reducedMotion && !isFirstSync
      const startX = spawnAtDoor ? DOOR.x : target.x
      const startY = spawnAtDoor ? DOOR.y : target.y
      const dist = Math.hypot(target.x - startX, target.y - startY)
      const isWalking = !opts.reducedMotion && dist > 0.5
      const playsCue = !isFirstSync

      const initialPose: ScenePose = isWalking ? 'walk' : desiredPose
      const facing: 'left' | 'right' =
        target.x < startX ? 'left' : target.x > startX ? 'right' : 'right'

      const isTerminal = scene.live && !isMain && isTerminalStatus(src.status)

      const created: AnimatedAgent = {
        id: src.id,
        isMain,
        name: src.name,
        status: src.status,
        statusLabel: src.statusLabel,
        station: effectiveStation,
        x: startX,
        y: startY,
        targetX: target.x,
        targetY: target.y,
        walkSpeed: computeEffectiveWalkSpeed(dist),
        facing,
        pose: initialPose,
        desiredPose,
        frame: 0,
        frameElapsedMs: 0,
        avatar,
        look: isMain ? 0 : chooseLook(anim, src.id),
        bubble: src.bubble,
        lastBubbleEventId: src.bubble?.eventId ?? null,
        bubbleAgeMs: 0,
        bubblePopProgress: opts.reducedMotion ? 1 : 0,
        settledElapsedMs: isTerminal ? 0 : null,
        exitingToDoor: false,
        exited: false,
        currentTool: src.currentTool,
      }

      anim.agents.set(src.id, created)

      // Opening the scene is silent.
      if (playsCue && isWalking) {
        enqueueSound(anim, 'step')
      } else if (
        playsCue &&
        desiredPose === 'work' &&
        (effectiveStation === 'terminal' || effectiveStation === 'writing')
      ) {
        enqueueSound(anim, 'type')
      }

      if (spawnAtDoor) {
        anim.activeEffects.push({
          id: `spawn-poof:${src.id}`,
          kind: 'poof',
          agentId: src.id,
          station: effectiveStation,
          x: DOOR.x,
          y: DOOR.y - 10,
          elapsedMs: 0,
          durationMs: EFFECT_DURATION_MS,
        })
      }
      continue
    }

    // Update existing character
    const prevStatus = existing.status
    const prevDesiredPose = existing.desiredPose
    const prevStation = existing.station

    existing.isMain = isMain
    existing.name = src.name
    existing.status = src.status
    existing.statusLabel = src.statusLabel
    existing.station = effectiveStation
    existing.desiredPose = desiredPose
    existing.avatar = avatar
    existing.currentTool = src.currentTool

    // Sound cues on pose / status transitions
    if (
      desiredPose === 'work' &&
      (effectiveStation === 'terminal' || effectiveStation === 'writing') &&
      (prevDesiredPose !== 'work' || prevStation !== effectiveStation)
    ) {
      enqueueSound(anim, 'type')
    }
    if (src.status === 'failed' && prevStatus !== 'failed') {
      enqueueSound(anim, 'bonk')
    }

    // Track terminal status timer for non-Orin agents when live
    if (!scene.live) {
      existing.settledElapsedMs = null
      existing.exitingToDoor = false
      existing.exited = false
    } else if (!isMain && isTerminalStatus(src.status)) {
      if (existing.settledElapsedMs === null) {
        existing.settledElapsedMs = 0
      }
    } else {
      existing.settledElapsedMs = null
      existing.exitingToDoor = false
      existing.exited = false
    }

    const nextTargetX = !existing.exitingToDoor ? target.x : DOOR.x
    const nextTargetY = !existing.exitingToDoor ? target.y : DOOR.y
    const targetChanged =
      Math.abs(nextTargetX - existing.targetX) > 0.5 ||
      Math.abs(nextTargetY - existing.targetY) > 0.5
    existing.targetX = nextTargetX
    existing.targetY = nextTargetY

    const distToTarget = Math.hypot(existing.targetX - existing.x, existing.targetY - existing.y)
    if (targetChanged || existing.pose !== 'walk') {
      existing.walkSpeed = computeEffectiveWalkSpeed(distToTarget)
    }
    if (opts.reducedMotion) {
      existing.x = existing.targetX
      existing.y = existing.targetY
      existing.pose = existing.desiredPose
      existing.frame = 0
      if (existing.exitingToDoor) {
        existing.exited = true
      }
    } else if (distToTarget > 0.5) {
      if (existing.pose !== 'walk') {
        enqueueSound(anim, 'step')
      }
      existing.pose = 'walk'
      if (Math.abs(existing.targetX - existing.x) > 0.2) {
        existing.facing = existing.targetX < existing.x ? 'left' : 'right'
      }
    } else {
      existing.pose = existing.desiredPose
    }

    // Update bubble state
    if (!src.bubble) {
      existing.bubble = null
      existing.lastBubbleEventId = null
      existing.bubbleAgeMs = 0
    } else if (src.bubble.eventId !== existing.lastBubbleEventId) {
      existing.bubble = src.bubble
      existing.lastBubbleEventId = src.bubble.eventId
      existing.bubbleAgeMs = 0
      existing.bubblePopProgress = opts.reducedMotion ? 1 : 0
    } else {
      existing.bubble = src.bubble
    }
  }

  // Remove agents that disappeared from SceneState
  for (const id of Array.from(anim.agents.keys())) {
    if (!presentIds.has(id)) {
      anim.agents.delete(id)
    }
  }

  // Sync one-shot transfers
  if (!scene.live) {
    anim.activeTransfers = []
    anim.activeEffects = []
  } else {
    for (const tr of scene.transfers) {
      if (anim.seenTransferIds.has(tr.id)) continue
      anim.seenTransferIds.add(tr.id)

      const fromAgent = anim.agents.get(tr.fromId)
      const toAgent = anim.agents.get(tr.toId)
      const fromX = fromAgent ? fromAgent.x : stationSlot('meeting', 0).x
      const fromY = fromAgent ? handY(fromAgent) : stationSlot('meeting', 0).y - 12
      const toX = toAgent ? toAgent.x : stationSlot('meeting', 1).x
      const toY = toAgent ? handY(toAgent) : stationSlot('meeting', 1).y - 12

      const midX = (fromX + toX) / 2
      const midY = (fromY + toY) / 2

      anim.activeTransfers.push({
        id: tr.id,
        kind: tr.kind,
        fromId: tr.fromId,
        toId: tr.toId,
        fromX,
        fromY,
        toX,
        toY,
        x: opts.reducedMotion ? midX : fromX,
        y: opts.reducedMotion ? midY : fromY,
        elapsedMs: 0,
        durationMs: opts.reducedMotion ? 350 : TRANSFER_DURATION_MS,
        label: tr.label,
      })

      if (tr.kind === 'failed_delegation') {
        enqueueSound(anim, 'bonk')
      } else {
        enqueueSound(anim, 'envelope')
      }
    }

    // Sync one-shot effects
    for (const fx of scene.effects) {
      if (anim.seenEffectIds.has(fx.id)) continue
      anim.seenEffectIds.add(fx.id)

      if (fx.kind === 'check') {
        enqueueSound(anim, 'ding')
      } else if (fx.kind === 'spark') {
        enqueueSound(anim, 'bonk')
      }

      // Skip particle poofs when reducedMotion is enabled
      if (opts.reducedMotion && fx.kind === 'poof') {
        continue
      }

      const targetAgent = anim.agents.get(fx.agentId)
      const slot = stationSlot(fx.station, 0)
      const x = targetAgent ? targetAgent.x : slot.x
      const y = targetAgent
        ? targetAgent.y - figureHeight(targetAgent.isMain) - 2
        : slot.y - figureHeight(false) - 2

      anim.activeEffects.push({
        id: fx.id,
        kind: fx.kind,
        agentId: fx.agentId,
        station: fx.station,
        x,
        y,
        elapsedMs: 0,
        durationMs: opts.reducedMotion ? 300 : EFFECT_DURATION_MS,
      })
    }
  }

  return anim
}

/**
 * Steps the animation forward by `dtMs` milliseconds.
 * Moves characters toward their targets, updates sprite frames, transfers, effects,
 * bubble TTLs, and exits completed subagents after ~4 seconds.
 */
export function stepAnimator(
  anim: SceneAnimatorState,
  dtMs: number,
  opts: AnimatorOptions
): SceneAnimatorState {
  const safeDt = Math.max(0, dtMs)

  if (anim.lastSyncNowMs !== null) {
    anim.lastSyncNowMs += safeDt
  }

  if (anim.alarmBlinkRemainingMs > 0) {
    anim.alarmBlinkRemainingMs = opts.reducedMotion
      ? 0
      : Math.max(0, anim.alarmBlinkRemainingMs - safeDt)
  }

  for (const agent of anim.agents.values()) {
    // 1. Bubble pop-in and expiration (~5s TTL)
    if (agent.bubble) {
      agent.bubbleAgeMs += safeDt
      agent.bubblePopProgress = opts.reducedMotion
        ? 1
        : Math.min(1, agent.bubblePopProgress + safeDt / 160)
      if (agent.bubbleAgeMs >= BUBBLE_TTL_MS) {
        agent.bubble = null
      }
    }

    // 2. Settled timer for completed/failed subagents (when live)
    if (anim.live && !agent.isMain && agent.settledElapsedMs !== null && !agent.exited) {
      const distBeforeStep = Math.hypot(agent.targetX - agent.x, agent.targetY - agent.y)
      const isAtStationOrExiting = distBeforeStep <= 0.5 || agent.exitingToDoor
      if (isAtStationOrExiting) {
        agent.settledElapsedMs += safeDt
        if (agent.settledElapsedMs >= EXIT_DELAY_MS) {
          agent.exitingToDoor = true
          agent.targetX = DOOR.x
          agent.targetY = DOOR.y
          agent.x = DOOR.x
          agent.y = DOOR.y
          agent.exited = true
          continue
        }
        if (agent.settledElapsedMs >= EXIT_WALK_START_MS && !agent.exitingToDoor) {
          agent.exitingToDoor = true
          agent.targetX = DOOR.x
          agent.targetY = DOOR.y
          agent.walkSpeed = computeEffectiveWalkSpeed(
            Math.hypot(DOOR.x - agent.x, DOOR.y - agent.y)
          )
          if (opts.reducedMotion) {
            agent.x = DOOR.x
            agent.y = DOOR.y
          } else {
            enqueueSound(anim, 'step')
          }
        }
      }
    }

    if (agent.exited) continue

    // 3. Movement toward target
    if (opts.reducedMotion) {
      agent.x = agent.targetX
      agent.y = agent.targetY
      agent.pose = agent.desiredPose
      agent.frame = 0
      if (agent.exitingToDoor && agent.settledElapsedMs !== null && agent.settledElapsedMs >= EXIT_DELAY_MS) {
        agent.exited = true
      }
      continue
    }

    const dx = agent.targetX - agent.x
    const dy = agent.targetY - agent.y
    const dist = Math.hypot(dx, dy)

    if (dist > 0.5) {
      if (agent.pose !== 'walk') {
        agent.pose = 'walk'
        agent.frame = 0
        agent.frameElapsedMs = 0
        enqueueSound(anim, 'step')
      }
      if (Math.abs(dx) > 0.2) {
        agent.facing = dx < 0 ? 'left' : 'right'
      }

      // Use effective walk speed computed on target change so any traversal finishes within <= 1.2s
      const speed = Math.max(WALK_SPEED_PX_PER_SEC, agent.walkSpeed || WALK_SPEED_PX_PER_SEC)
      const stepDist = (speed * safeDt) / 1000
      if (stepDist >= dist) {
        agent.x = agent.targetX
        agent.y = agent.targetY
        if (agent.exitingToDoor) {
          agent.exited = true
        } else {
          agent.pose = agent.desiredPose
          agent.frame = 0
          agent.frameElapsedMs = 0
        }
      } else {
        agent.x += (dx / dist) * stepDist
        agent.y += (dy / dist) * stepDist
      }
    } else {
      agent.x = agent.targetX
      agent.y = agent.targetY
      if (agent.exitingToDoor) {
        agent.exited = true
      } else if (agent.pose !== agent.desiredPose) {
        agent.pose = agent.desiredPose
        agent.frame = 0
        agent.frameElapsedMs = 0
      }
    }

    // 4. Advance sprite frame clock
    const frameCount = poseFrameCount(agent.pose)
    const frameInterval = agent.pose === 'walk' ? 130 : 380
    agent.frameElapsedMs += safeDt
    while (agent.frameElapsedMs >= frameInterval) {
      agent.frameElapsedMs -= frameInterval
      agent.frame = (agent.frame + 1) % frameCount
    }
  }

  // 5. Step active transfers
  const remainingTransfers: ActiveTransfer[] = []
  for (const tr of anim.activeTransfers) {
    tr.elapsedMs += safeDt
    if (tr.elapsedMs >= tr.durationMs) continue

    const fromAgent = anim.agents.get(tr.fromId)
    const toAgent = anim.agents.get(tr.toId)
    if (fromAgent && !fromAgent.exited) {
      tr.fromX = fromAgent.x
      tr.fromY = handY(fromAgent)
    }
    if (toAgent && !toAgent.exited) {
      tr.toX = toAgent.x
      tr.toY = handY(toAgent)
    }

    if (opts.reducedMotion) {
      tr.x = (tr.fromX + tr.toX) / 2
      tr.y = (tr.fromY + tr.toY) / 2
    } else {
      const t = Math.min(1, Math.max(0, tr.elapsedMs / tr.durationMs))
      const arc = Math.sin(t * Math.PI) * 16
      tr.x = tr.fromX + (tr.toX - tr.fromX) * t
      tr.y = tr.fromY + (tr.toY - tr.fromY) * t - arc
    }
    remainingTransfers.push(tr)
  }
  anim.activeTransfers = remainingTransfers

  // 6. Step active effects
  const remainingEffects: ActiveEffect[] = []
  for (const fx of anim.activeEffects) {
    fx.elapsedMs += safeDt
    if (fx.elapsedMs >= fx.durationMs) continue
    const targetAgent = anim.agents.get(fx.agentId)
    if (targetAgent && !targetAgent.exited && fx.kind !== 'poof') {
      fx.x = targetAgent.x
      fx.y = targetAgent.y - figureHeight(targetAgent.isMain) - 2
    }
    remainingEffects.push(fx)
  }
  anim.activeEffects = remainingEffects

  return anim
}

/** Height where a character holds a paper (transfers start and end there). */
function handY(agent: AnimatedAgent): number {
  return agent.y - Math.round(figureHeight(agent.isMain) / 2)
}

/**
 * Returns the `agentId` of the topmost visible character at logical coordinates `(x, y)`,
 * or `null` if no character is hit. Front-most characters (larger `y`) win ties.
 */
export function hitTest(anim: SceneAnimatorState, x: number, y: number): string | null {
  const visible = Array.from(anim.agents.values()).filter((a) => !a.exited)
  // Sort ascending by y, then iterate from end (front-most) to start
  visible.sort((a, b) => a.y - b.y)

  for (let i = visible.length - 1; i >= 0; i -= 1) {
    const agent = visible[i]!
    const halfW = agent.isMain ? FIGURE_HALF_WIDTH + 1 : FIGURE_HALF_WIDTH
    const left = agent.x - halfW
    const right = agent.x + halfW
    const top = agent.y - figureHeight(agent.isMain) - 2
    const bottom = agent.y + 2
    if (x >= left && x <= right && y >= top && y <= bottom) {
      return agent.id
    }
  }

  return null
}

/**
 * Drains and returns all pending sound cues generated since the last drain call.
 */
export function drainSoundCues(anim: SceneAnimatorState): SceneSoundCue[] {
  if (anim.soundQueue.length === 0) return []
  const cues = anim.soundQueue.slice()
  anim.soundQueue.length = 0
  return cues
}

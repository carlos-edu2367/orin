import {
  DEFAULT_FALLBACK_CHARACTER_NAMES,
  type SceneCharacterPoseKey,
} from '../../../api/sceneCharacters'
import {
  MAIN_AGENT_ID,
  type SceneAgent,
  type SceneCustomCharacter,
  type SceneCustomCharacterPoses,
  type ScenePose,
} from './sceneTypes'

export interface SceneCharactersConfigInput {
  names?: readonly string[]
  characters?: readonly SceneCustomCharacter[]
  bindings?: Readonly<Record<string, string>>
}

/** Deterministic 32-bit FNV-1a hash used to pick stable character names from the pool. */
export function fnv1a32(str: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < str.length; i += 1) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

function normalizeCharacterName(raw: string | undefined | null): string {
  if (!raw || typeof raw !== 'string') return ''
  // Strip control characters and trim to max 32 chars
  let cleaned = ''
  for (let i = 0; i < raw.length; i += 1) {
    const code = raw.charCodeAt(i)
    if (code >= 32 && (code < 0x7f || code > 0x9f)) {
      cleaned += raw[i]
    }
  }
  return cleaned.trim().slice(0, 32)
}

function toCustomCharacter(char: SceneCustomCharacter): SceneCustomCharacter {
  return {
    id: char.id,
    name: char.name,
    poses: {
      idle: char.poses.idle,
      ...(char.poses.think ? { think: char.poses.think } : {}),
      ...(char.poses.work ? { work: char.poses.work } : {}),
      ...(char.poses.wait ? { wait: char.poses.wait } : {}),
      ...(char.poses.scared ? { scared: char.poses.scared } : {}),
    },
  }
}

function lookupBoundCharacter(
  agent: SceneAgent,
  bindings: Readonly<Record<string, string>>,
  charsById: ReadonlyMap<string, SceneCustomCharacter>,
): SceneCustomCharacter | undefined {
  const candidates: string[] = []
  const rawName = (agent.name || '').trim().toLowerCase()
  const rawType = (agent.subagentType || '').trim().toLowerCase()
  if (rawName) candidates.push(rawName)
  if (rawType && !candidates.includes(rawType)) candidates.push(rawType)
  if (rawName) {
    const stripped = rawName.replace(/-\d+$/, '')
    if (stripped && !candidates.includes(stripped)) {
      candidates.push(stripped)
    }
  }
  for (const key of candidates) {
    const boundId = bindings[key]
    if (boundId) {
      const found = charsById.get(boundId)
      if (found) return found
    }
  }
  return undefined
}

/**
 * Pure character resolution for all agents in the Cena.
 *
 * For every non-Orin agent, resolves in order:
 *  (a) `characterName` coming from the event;
 *  (b) binding by agent name or subagentType (lowercase);
 *  (c) stable random name from the pool (`names` + custom character names) by FNV-1a hash of `agent.id`,
 *      without repeating inside the same scene while free names remain.
 *
 * If the resolved name matches (case-insensitively) a created custom character, its sprite poses are attached.
 * Orin always remains "Orin" with its built-in pixel art.
 */
export function resolveCharacters(
  agents: readonly SceneAgent[],
  config?: SceneCharactersConfigInput | null,
): SceneAgent[] {
  const customChars = config?.characters ?? []
  const rawBindings = config?.bindings ?? {}
  const normalizedBindings: Record<string, string> = {}
  for (const [k, v] of Object.entries(rawBindings)) {
    const cleanKey = k.trim().toLowerCase()
    const cleanVal = (v || '').trim()
    if (cleanKey && cleanVal) {
      normalizedBindings[cleanKey] = cleanVal
    }
  }

  const charsById = new Map<string, SceneCustomCharacter>()
  const charsByLowerName = new Map<string, SceneCustomCharacter>()
  for (const ch of customChars) {
    if (!ch || !ch.id || !ch.name || !ch.poses?.idle) continue
    const cleanChar = toCustomCharacter(ch)
    charsById.set(cleanChar.id, cleanChar)
    const lower = cleanChar.name.trim().toLowerCase()
    if (lower && !charsByLowerName.has(lower)) {
      charsByLowerName.set(lower, cleanChar)
    }
  }

  const baseNames =
    config === undefined || config === null
      ? DEFAULT_FALLBACK_CHARACTER_NAMES
      : (config.names ?? [])

  const pool: string[] = []
  const poolSeenLower = new Set<string>()
  for (const raw of baseNames) {
    const clean = normalizeCharacterName(raw)
    if (!clean) continue
    const lower = clean.toLowerCase()
    if (!poolSeenLower.has(lower)) {
      poolSeenLower.add(lower)
      pool.push(clean)
    }
  }
  for (const ch of charsById.values()) {
    const clean = normalizeCharacterName(ch.name)
    if (!clean) continue
    const lower = clean.toLowerCase()
    if (!poolSeenLower.has(lower)) {
      poolSeenLower.add(lower)
      pool.push(clean)
    }
  }

  // First pass: resolve (a) event characterName and (b) explicit bindings, tracking used names.
  const usedNamesLower = new Set<string>()
  const preResolved = new Map<
    string,
    { characterName: string; customCharacter?: SceneCustomCharacter }
  >()

  for (const agent of agents) {
    if (agent.isMain || agent.id === MAIN_AGENT_ID) continue

    const eventCharName = normalizeCharacterName(agent.characterName)
    if (eventCharName) {
      const lower = eventCharName.toLowerCase()
      usedNamesLower.add(lower)
      const matchedCustom = charsByLowerName.get(lower)
      preResolved.set(agent.id, {
        characterName: eventCharName,
        ...(matchedCustom ? { customCharacter: matchedCustom } : {}),
      })
      continue
    }

    const boundChar = lookupBoundCharacter(agent, normalizedBindings, charsById)
    if (boundChar) {
      const cleanBoundName = normalizeCharacterName(boundChar.name) || boundChar.name
      usedNamesLower.add(cleanBoundName.toLowerCase())
      preResolved.set(agent.id, {
        characterName: cleanBoundName,
        customCharacter: boundChar,
      })
    }
  }

  // Second pass: resolve (c) from pool without repeating while free names exist.
  return agents.map((agent) => {
    if (agent.isMain || agent.id === MAIN_AGENT_ID) {
      if (!agent.characterName && !agent.customCharacter) return agent
      const rest = { ...agent }
      delete rest.characterName
      delete rest.customCharacter
      return rest
    }

    const fixed = preResolved.get(agent.id)
    if (fixed) {
      return {
        ...agent,
        characterName: fixed.characterName,
        ...(fixed.customCharacter
          ? { customCharacter: fixed.customCharacter }
          : { customCharacter: undefined }),
      }
    }

    if (pool.length === 0) {
      const rest = { ...agent }
      delete rest.characterName
      delete rest.customCharacter
      return rest
    }

    const startIdx = fnv1a32(agent.id) % pool.length
    let chosenName = pool[startIdx]
    for (let step = 0; step < pool.length; step += 1) {
      const candidate = pool[(startIdx + step) % pool.length]
      if (!usedNamesLower.has(candidate.toLowerCase())) {
        chosenName = candidate
        break
      }
    }
    const chosenLower = chosenName.toLowerCase()
    usedNamesLower.add(chosenLower)
    const matchedCustom = charsByLowerName.get(chosenLower)

    return {
      ...agent,
      characterName: chosenName,
      ...(matchedCustom ? { customCharacter: matchedCustom } : { customCharacter: undefined }),
    }
  })
}

/**
 * Maps a ScenePose to the 5 supported custom character sprite keys:
 * think -> think, work -> work, raise_hand & wait -> wait, scared -> scared, others -> idle.
 */
export function mapScenePoseToCustomPose(pose: ScenePose): SceneCharacterPoseKey {
  switch (pose) {
    case 'think':
      return 'think'
    case 'work':
      return 'work'
    case 'raise_hand':
    case 'wait':
      return 'wait'
    case 'scared':
      return 'scared'
    default:
      return 'idle'
  }
}

export interface SelectedCustomPose {
  url: string
  poseKey: SceneCharacterPoseKey
  /** True when the agent is in a non-idle pose whose specific sprite is missing, falling back to `idle`. */
  isFallbackIdle: boolean
}

export function selectCustomCharacterPose(
  poses: SceneCustomCharacterPoses,
  pose: ScenePose,
): SelectedCustomPose {
  const desiredKey = mapScenePoseToCustomPose(pose)
  const desiredUrl = poses[desiredKey]
  if (desiredUrl) {
    return {
      url: desiredUrl,
      poseKey: desiredKey,
      isFallbackIdle: pose !== 'idle' && desiredKey === 'idle',
    }
  }
  return {
    url: poses.idle,
    poseKey: 'idle',
    isFallbackIdle: pose !== 'idle',
  }
}

/**
 * Subtle 1-2 px vertical bob when a custom character uses the `idle` sprite for a missing pose.
 * Disabled when `reducedMotion` is true or when the agent is genuinely in `idle`/`sit`.
 */
export function computeFallbackIdleBobOffset(
  pose: ScenePose,
  isFallbackIdle: boolean,
  timeMs: number,
  reducedMotion: boolean,
): number {
  if (reducedMotion || !isFallbackIdle || pose === 'idle' || pose === 'sit') {
    return 0
  }
  const phase = Math.floor(Math.max(0, timeMs) / 300) % 2
  return phase === 0 ? 0 : -1
}

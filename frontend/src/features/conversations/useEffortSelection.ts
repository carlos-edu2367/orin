import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { getOrchestrationSettings, updateOrchestrationSettings } from '../../api/orchestrationSettings'
import { EFFORT_LEVELS, type EffortLevel } from '../../api/providers'

export { EFFORT_LEVELS, type EffortLevel }

export const EFFORT_RANK: Readonly<Record<EffortLevel, number>> = {
  low: 0,
  medium: 1,
  high: 2,
  xhigh: 3,
  max: 4,
  ultra: 5,
}

export const EFFORT_LABELS: Readonly<Record<EffortLevel, string>> = {
  low: 'Baixo',
  medium: 'Médio',
  high: 'Alto',
  xhigh: 'Muito alto',
  max: 'Máximo',
  ultra: 'Ultra',
}

const STORAGE_KEY = 'orin:effort-by-model'

export function isEffortLevel(value: unknown): value is EffortLevel {
  return typeof value === 'string' && (EFFORT_LEVELS as readonly string[]).includes(value)
}

export function normalizeEffortLevel(value: unknown): EffortLevel | null {
  if (typeof value !== 'string') return null
  const normalized = value.trim().toLowerCase()
  return isEffortLevel(normalized) ? normalized : null
}

export function effortLabel(level: string | null | undefined): string {
  const normalized = normalizeEffortLevel(level)
  return normalized ? EFFORT_LABELS[normalized] : 'Padrão'
}

export function effortModelKey(provider: string | null | undefined, modelId: string | null | undefined): string | null {
  const cleanModel = (modelId ?? '').trim()
  if (!cleanModel) return null
  const cleanProvider = (provider ?? '').trim()
  return cleanProvider ? `${cleanProvider}/${cleanModel}` : cleanModel
}

/**
 * Clamps a requested effort level to the highest level in `supported` that does
 * not exceed the requested rank, matching backend `effort_levels.clamp`.
 */
export function clampEffort(requested: string | null | undefined, supported: readonly string[]): EffortLevel | null {
  const req = normalizeEffortLevel(requested)
  if (!req) return null
  const validSupported = supported
    .map((item) => normalizeEffortLevel(item))
    .filter((item): item is EffortLevel => item !== null)
  if (validSupported.length === 0) return null
  if (validSupported.includes(req)) return req

  const uniqueOrdered = Array.from(new Set(validSupported)).sort((a, b) => EFFORT_RANK[a] - EFFORT_RANK[b])
  const reqRank = EFFORT_RANK[req]
  const lowerOrEq = uniqueOrdered.filter((lvl) => EFFORT_RANK[lvl] <= reqRank)
  return lowerOrEq.length > 0 ? lowerOrEq[lowerOrEq.length - 1] : uniqueOrdered[0]
}

export function nextEffortLevel(
  current: string | null | undefined,
  supported: readonly string[],
  defaultEffort?: string | null,
): EffortLevel | null {
  const validSupported = Array.from(
    new Set(supported.map((item) => normalizeEffortLevel(item)).filter((item): item is EffortLevel => item !== null)),
  ).sort((a, b) => EFFORT_RANK[a] - EFFORT_RANK[b])
  if (validSupported.length === 0) return null
  const base = normalizeEffortLevel(current) ?? normalizeEffortLevel(defaultEffort) ?? validSupported[0]
  const idx = validSupported.indexOf(base)
  if (idx < 0) return validSupported[validSupported.length - 1]
  return validSupported[Math.min(idx + 1, validSupported.length - 1)]
}

function readCachedEffortMap(): Record<string, EffortLevel> {
  try {
    if (typeof window === 'undefined' || !window.localStorage || typeof window.localStorage.getItem !== 'function') {
      return {}
    }
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {}
    const cleaned: Record<string, EffortLevel> = {}
    for (const [key, val] of Object.entries(parsed as Record<string, unknown>)) {
      const level = normalizeEffortLevel(val)
      if (key.trim() && level) cleaned[key.trim()] = level
    }
    return cleaned
  } catch {
    return {}
  }
}

function writeCachedEffortMap(map: Record<string, EffortLevel>): void {
  try {
    if (typeof window === 'undefined' || !window.localStorage || typeof window.localStorage.setItem !== 'function') {
      return
    }
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(map))
  } catch {
    // Ignore storage errors (private browsing, quota, Node experimental webstorage).
  }
}

function parseServerEffortMap(raw: unknown): Record<string, EffortLevel> {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return {}
  const out: Record<string, EffortLevel> = {}
  for (const [key, val] of Object.entries(raw as Record<string, unknown>)) {
    const level = normalizeEffortLevel(val)
    if (key.trim() && level) out[key.trim()] = level
  }
  return out
}

export type UseEffortSelectionArgs = {
  client?: ApiClient | null
  provider?: string | null
  modelId?: string | null
  supportedEfforts?: readonly string[]
  defaultEffort?: string | null
}

export type UseEffortSelectionResult = {
  /** Explicitly chosen (and clamped if needed) effort for the current model, or null when using automatic/default. */
  selectedEffort: EffortLevel | null
  /** Whether the remembered effort was clamped to fit the current model's supported levels. */
  wasClamped: boolean
  /** One-turn boost ("Pensar mais") flag; automatically cleared via `consumeBoost()`. */
  boostNextTurn: boolean
  /** Remembered map of `provider/model -> effort`. */
  effortByModel: Record<string, EffortLevel>
  selectEffort: (level: string | null) => Promise<void>
  toggleBoost: () => void
  setBoostNextTurn: (next: boolean) => void
  consumeBoost: () => void
  /** Returns `{ effort?, boost? }` suitable for spreading into POST bodies without sending defaults. */
  payloadFields: () => { effort?: EffortLevel; boost?: true }
}

export function useEffortSelection({
  client,
  provider,
  modelId,
  supportedEfforts = [],
  defaultEffort = null,
}: UseEffortSelectionArgs): UseEffortSelectionResult {
  const [effortByModel, setEffortByModel] = useState<Record<string, EffortLevel>>(() => readCachedEffortMap())
  const [boostNextTurn, setBoostNextTurn] = useState(false)
  const localDirtyRef = useRef(false)
  const effortMapRef = useRef(effortByModel)

  useEffect(() => {
    if (!client) return
    let cancelled = false
    getOrchestrationSettings(client, { prefix: 'effortByModel' })
      .then((report) => {
        if (cancelled) return
        const item = report.items.find((entry) => entry.path === 'effortByModel')
        if (!item) return
        const serverMap = parseServerEffortMap(item.value)
        if (localDirtyRef.current) return
        effortMapRef.current = serverMap
        setEffortByModel(serverMap)
        writeCachedEffortMap(serverMap)
      })
      .catch(() => {
        // Best-effort sync: fall back to cached localStorage state.
      })
    return () => {
      cancelled = true
    }
  }, [client])

  const key = useMemo(() => effortModelKey(provider, modelId), [provider, modelId])
  const bareModelKey = useMemo(() => (modelId ?? '').trim() || null, [modelId])

  const rememberedRaw = useMemo<EffortLevel | null>(() => {
    if (key && effortByModel[key]) return effortByModel[key]
    if (bareModelKey && effortByModel[bareModelKey]) return effortByModel[bareModelKey]
    return null
  }, [bareModelKey, effortByModel, key])

  const selectedEffort = useMemo<EffortLevel | null>(() => {
    if (!rememberedRaw) return null
    if (supportedEfforts.length === 0) return null
    return clampEffort(rememberedRaw, supportedEfforts)
  }, [rememberedRaw, supportedEfforts])

  const wasClamped = Boolean(rememberedRaw && selectedEffort && rememberedRaw !== selectedEffort)

  const selectEffort = useCallback(
    async (level: string | null) => {
      const targetKey = key ?? bareModelKey
      if (!targetKey) return
      const normalized = level === null ? null : normalizeEffortLevel(level)
      const nextMap: Record<string, EffortLevel> = { ...effortMapRef.current }
      if (normalized) {
        const applied = supportedEfforts.length > 0 ? clampEffort(normalized, supportedEfforts) : normalized
        if (!applied) return
        nextMap[targetKey] = applied
      } else {
        delete nextMap[targetKey]
        if (bareModelKey && bareModelKey !== targetKey) {
          delete nextMap[bareModelKey]
        }
      }
      localDirtyRef.current = true
      effortMapRef.current = nextMap
      setEffortByModel(nextMap)
      writeCachedEffortMap(nextMap)

      if (client) {
        try {
          await updateOrchestrationSettings(client, { effortByModel: nextMap }, { prefix: 'effortByModel' })
        } catch {
          // Local cache already updated; server sync is best-effort.
        }
      }
    },
    [bareModelKey, client, key, supportedEfforts],
  )

  const toggleBoost = useCallback(() => {
    setBoostNextTurn((current) => !current)
  }, [])

  const consumeBoost = useCallback(() => {
    setBoostNextTurn(false)
  }, [])

  const payloadFields = useCallback((): { effort?: EffortLevel; boost?: true } => {
    const out: { effort?: EffortLevel; boost?: true } = {}
    if (supportedEfforts.length > 0) {
      if (selectedEffort) {
        out.effort = selectedEffort
      }
      if (boostNextTurn) {
        out.boost = true
      }
    }
    return out
  }, [boostNextTurn, selectedEffort, supportedEfforts.length])

  // Keep reference to defaultEffort in debug/title flows without unused param warning.
  void defaultEffort

  return {
    selectedEffort,
    wasClamped,
    boostNextTurn,
    effortByModel,
    selectEffort,
    toggleBoost,
    setBoostNextTurn,
    consumeBoost,
    payloadFields,
  }
}

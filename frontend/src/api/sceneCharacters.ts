import { useCallback, useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from './client'
import { list, optStr, rec, str, strings } from './parse'

export type SceneCharacterPoseKey = 'idle' | 'think' | 'work' | 'wait' | 'scared'

export const SCENE_CHARACTER_POSES: readonly SceneCharacterPoseKey[] = [
  'idle',
  'think',
  'work',
  'wait',
  'scared',
] as const

export const OPTIONAL_SCENE_CHARACTER_POSES: readonly SceneCharacterPoseKey[] = [
  'think',
  'work',
  'wait',
  'scared',
] as const

export const DEFAULT_FALLBACK_CHARACTER_NAMES: readonly string[] = [
  'Bento',
  'Cora',
  'Dante',
  'Nina',
  'Otto',
  'Luna',
  'Tico',
  'Mila',
  'Zeca',
  'Iris',
  'Kiko',
  'Lia',
  'Nori',
  'Pipo',
  'Rafa',
  'Sora',
  'Theo',
  'Vivi',
  'Yuri',
  'Bia',
  'Caio',
  'Gabi',
  'Leo',
  'Maju',
] as const

export interface SceneCharacterPoses {
  idle: string
  think?: string
  work?: string
  wait?: string
  scared?: string
}

export interface SceneCharacterRecord {
  id: string
  name: string
  poses: SceneCharacterPoses
  createdAt: string
}

export interface SceneCharactersConfig {
  names: string[]
  defaultNames: string[]
  characters: SceneCharacterRecord[]
  bindings: Record<string, string>
}

export interface CreateSceneCharacterInput {
  name: string
  idle: File | Blob
  think?: File | Blob | null
  work?: File | Blob | null
  wait?: File | Blob | null
  scared?: File | Blob | null
}

export interface UpdateSceneCharacterInput {
  name?: string
  idle?: File | Blob | null
  think?: File | Blob | null
  work?: File | Blob | null
  wait?: File | Blob | null
  scared?: File | Blob | null
  removePoses?: SceneCharacterPoseKey[]
}

export function parseSceneCharacterPoses(value: unknown): SceneCharacterPoses | null {
  const raw = rec(value)
  const idle = str(raw.idle)
  if (!idle) return null
  const poses: SceneCharacterPoses = { idle }
  const think = optStr(raw.think)
  const work = optStr(raw.work)
  const wait = optStr(raw.wait)
  const scared = optStr(raw.scared)
  if (think) poses.think = think
  if (work) poses.work = work
  if (wait) poses.wait = wait
  if (scared) poses.scared = scared
  return poses
}

export function parseSceneCharacterRecord(value: unknown): SceneCharacterRecord | null {
  const raw = rec(value)
  const id = str(raw.id)
  const name = str(raw.name)
  const poses = parseSceneCharacterPoses(raw.poses)
  if (!id || !name || !poses) return null
  return {
    id,
    name,
    poses,
    createdAt: str(raw.created_at) || str(raw.createdAt),
  }
}

export function parseSceneCharacterBindings(value: unknown): Record<string, string> {
  const raw = rec(value)
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(raw)) {
    const key = k.trim().toLowerCase()
    const val = str(v).trim()
    if (key && val) out[key] = val
  }
  return out
}

export function parseSceneCharactersConfig(value: unknown): SceneCharactersConfig {
  const raw = rec(value)
  const defaultNamesRaw = strings(raw.default_names ?? raw.defaultNames, 200)
  const defaultNames =
    defaultNamesRaw.length > 0 ? defaultNamesRaw : [...DEFAULT_FALLBACK_CHARACTER_NAMES]
  const names = Array.isArray(raw.names)
    ? strings(raw.names, 200)
    : [...defaultNames]
  const characters = list(raw.characters, parseSceneCharacterRecord, 50)
  const bindings = parseSceneCharacterBindings(raw.bindings)
  return {
    names,
    defaultNames,
    characters,
    bindings,
  }
}

function parseSingleCharacterResponse(value: unknown): SceneCharacterRecord {
  const raw = rec(value)
  const parsed = parseSceneCharacterRecord(raw.character ?? value)
  if (!parsed) throw new Error('O servidor não retornou um personagem válido.')
  return parsed
}

export function getSceneCharacters(
  client: ApiClient,
  signal?: AbortSignal,
): Promise<SceneCharactersConfig> {
  return client.request({
    path: '/v1/scene/characters',
    signal,
    parse: parseSceneCharactersConfig,
  })
}

export function updateSceneCharacterNames(
  client: ApiClient,
  names: string[],
): Promise<string[]> {
  return client.request({
    path: '/v1/scene/characters/names',
    method: 'PUT',
    body: { names },
    parse: (value) => strings(rec(value).names, 200),
  })
}

function appendFileIfPresent(form: FormData, key: string, file: File | Blob | null | undefined): boolean {
  if (!file) return false
  if (typeof File !== 'undefined' && file instanceof File) {
    form.append(key, file, file.name)
  } else {
    form.append(key, file, `${key}.png`)
  }
  return true
}

export function createSceneCharacter(
  client: ApiClient,
  input: CreateSceneCharacterInput,
): Promise<SceneCharacterRecord> {
  const form = new FormData()
  form.append('name', input.name.trim())
  appendFileIfPresent(form, 'idle', input.idle)
  for (const pose of OPTIONAL_SCENE_CHARACTER_POSES) {
    appendFileIfPresent(form, pose, input[pose])
  }
  return client.upload({
    path: '/v1/scene/characters',
    method: 'POST',
    expectedStatus: 201,
    body: form,
    parse: parseSingleCharacterResponse,
  })
}

export function updateSceneCharacter(
  client: ApiClient,
  id: string,
  input: UpdateSceneCharacterInput,
): Promise<SceneCharacterRecord> {
  const encodedId = encodeURIComponent(id)
  const hasFiles = SCENE_CHARACTER_POSES.some((pose) => Boolean(input[pose]))
  if (hasFiles) {
    const form = new FormData()
    if (input.name !== undefined) {
      form.append('name', input.name.trim())
    }
    if (input.removePoses && input.removePoses.length > 0) {
      form.append('remove_poses', input.removePoses.join(','))
    }
    for (const pose of SCENE_CHARACTER_POSES) {
      appendFileIfPresent(form, pose, input[pose])
    }
    return client.upload({
      path: `/v1/scene/characters/${encodedId}`,
      method: 'PATCH',
      body: form,
      parse: parseSingleCharacterResponse,
    })
  }

  const body: Record<string, unknown> = {}
  if (input.name !== undefined) body.name = input.name.trim()
  if (input.removePoses && input.removePoses.length > 0) {
    body.remove_poses = input.removePoses
  }
  return client.request({
    path: `/v1/scene/characters/${encodedId}`,
    method: 'PATCH',
    body,
    parse: parseSingleCharacterResponse,
  })
}

export function deleteSceneCharacter(
  client: ApiClient,
  id: string,
): Promise<{ deleted: boolean; id: string; bindings: Record<string, string> }> {
  return client.request({
    path: `/v1/scene/characters/${encodeURIComponent(id)}`,
    method: 'DELETE',
    parse: (value) => {
      const raw = rec(value)
      return {
        deleted: Boolean(raw.deleted ?? true),
        id: str(raw.id) || id,
        bindings: parseSceneCharacterBindings(raw.bindings),
      }
    },
  })
}

export function updateSceneCharacterBindings(
  client: ApiClient,
  bindings: Record<string, string>,
): Promise<Record<string, string>> {
  return client.request({
    path: '/v1/scene/characters/bindings',
    method: 'PUT',
    body: { bindings },
    parse: (value) => parseSceneCharacterBindings(rec(value).bindings ?? value),
  })
}

// ─── Shared in-memory store / cache for useSceneCharacters ───────────────────

const EMPTY_CONFIG: SceneCharactersConfig = {
  names: [],
  defaultNames: [...DEFAULT_FALLBACK_CHARACTER_NAMES],
  characters: [],
  bindings: {},
}

let cachedConfig: SceneCharactersConfig | null = null
let inflightLoad: Promise<SceneCharactersConfig> | null = null
const listeners = new Set<(config: SceneCharactersConfig) => void>()

function notifyListeners(next: SceneCharactersConfig): void {
  cachedConfig = next
  for (const listener of listeners) {
    listener(next)
  }
}

export function getCachedSceneCharacters(): SceneCharactersConfig {
  return cachedConfig ?? EMPTY_CONFIG
}

export function seedSceneCharactersCache(config: SceneCharactersConfig | null): void {
  cachedConfig = config
  inflightLoad = null
  if (config) {
    for (const listener of listeners) {
      listener(config)
    }
  }
}

export function resetSceneCharactersCache(): void {
  cachedConfig = null
  inflightLoad = null
}

export interface UseSceneCharactersResult {
  config: SceneCharactersConfig
  loading: boolean
  error: string | null
  refresh: () => Promise<SceneCharactersConfig>
  saveNames: (names: string[]) => Promise<string[]>
  createCharacter: (input: CreateSceneCharacterInput) => Promise<SceneCharacterRecord>
  updateCharacter: (id: string, input: UpdateSceneCharacterInput) => Promise<SceneCharacterRecord>
  deleteCharacter: (id: string) => Promise<void>
  saveBindings: (bindings: Record<string, string>) => Promise<Record<string, string>>
}

export function useSceneCharacters(customClient?: ApiClient): UseSceneCharactersResult {
  const client = useMemo(
    () => customClient ?? createBrowserApiClient(),
    [customClient],
  )
  const [config, setConfig] = useState<SceneCharactersConfig>(
    () => cachedConfig ?? EMPTY_CONFIG,
  )
  const [loading, setLoading] = useState<boolean>(() => cachedConfig === null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const listener = (next: SceneCharactersConfig) => {
      setConfig(next)
    }
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  }, [])

  const refresh = useCallback(async (): Promise<SceneCharactersConfig> => {
    setLoading(true)
    setError(null)
    try {
      const fresh = await getSceneCharacters(client)
      notifyListeners(fresh)
      setLoading(false)
      return fresh
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Falha ao carregar personagens.'
      setError(message)
      setLoading(false)
      throw err
    }
  }, [client])

  useEffect(() => {
    // A cached config already seeded the state (useState above) and the listener keeps it current.
    if (cachedConfig !== null) return
    if (!inflightLoad) {
      inflightLoad = getSceneCharacters(client).finally(() => {
        inflightLoad = null
      })
    }
    let active = true
    inflightLoad
      .then((loaded) => {
        notifyListeners(loaded)
        if (active) {
          setLoading(false)
          setError(null)
        }
      })
      .catch((err) => {
        if (active) {
          setLoading(false)
          setError(err instanceof Error ? err.message : 'Falha ao carregar personagens.')
        }
      })
    return () => {
      active = false
    }
  }, [client])

  const saveNames = useCallback(
    async (names: string[]): Promise<string[]> => {
      const saved = await updateSceneCharacterNames(client, names)
      const base = cachedConfig ?? config
      notifyListeners({ ...base, names: saved })
      return saved
    },
    [client, config],
  )

  const createCharacter = useCallback(
    async (input: CreateSceneCharacterInput): Promise<SceneCharacterRecord> => {
      const created = await createSceneCharacter(client, input)
      const base = cachedConfig ?? config
      notifyListeners({
        ...base,
        characters: [...base.characters.filter((c) => c.id !== created.id), created],
      })
      return created
    },
    [client, config],
  )

  const updateCharacter = useCallback(
    async (id: string, input: UpdateSceneCharacterInput): Promise<SceneCharacterRecord> => {
      const updated = await updateSceneCharacter(client, id, input)
      const base = cachedConfig ?? config
      notifyListeners({
        ...base,
        characters: base.characters.map((c) => (c.id === id ? updated : c)),
      })
      return updated
    },
    [client, config],
  )

  const deleteCharacter = useCallback(
    async (id: string): Promise<void> => {
      const res = await deleteSceneCharacter(client, id)
      const base = cachedConfig ?? config
      const nextBindings =
        Object.keys(res.bindings).length > 0 || Object.values(base.bindings).includes(id)
          ? res.bindings
          : Object.fromEntries(
              Object.entries(base.bindings).filter(([, charId]) => charId !== id),
            )
      notifyListeners({
        ...base,
        characters: base.characters.filter((c) => c.id !== id),
        bindings: nextBindings,
      })
    },
    [client, config],
  )

  const saveBindings = useCallback(
    async (bindings: Record<string, string>): Promise<Record<string, string>> => {
      const saved = await updateSceneCharacterBindings(client, bindings)
      const base = cachedConfig ?? config
      notifyListeners({ ...base, bindings: saved })
      return saved
    },
    [client, config],
  )

  return {
    config,
    loading,
    error,
    refresh,
    saveNames,
    createCharacter,
    updateCharacter,
    deleteCharacter,
    saveBindings,
  }
}

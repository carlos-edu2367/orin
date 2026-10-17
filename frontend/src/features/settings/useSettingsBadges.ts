import { useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { getInstallationStatus } from '../../api/installation'
import { listManagedMemories } from '../../api/memory'
import { listMcpServers } from '../../api/mcp'
import { listPlugins } from '../../api/plugins'
import { PROVIDER_NAMES, inspectProvider } from '../../api/providers'
import { listScheduledChats } from '../../api/schedules'
import { createSkillsClient } from '../../api/skills'
import type { BadgeMap } from './SettingsNav'

const CACHE_TTL_MS = 30_000

let sessionCache: BadgeMap | null = null
let sessionCacheAt = 0
let sessionRequest: Promise<BadgeMap> | null = null

export type UseSettingsBadgesOptions = {
  mode?: 'full' | 'pending-only'
  enabled?: boolean
}

export function useSettingsBadges(
  providedClient?: ApiClient,
  options?: UseSettingsBadgesOptions,
): BadgeMap {
  const client = useMemo(() => providedClient ?? createBrowserApiClient(), [providedClient])
  const mode = options?.mode ?? 'full'
  const enabled = options?.enabled ?? true
  const [badges, setBadges] = useState<BadgeMap>(() => sessionCache ?? {})

  useEffect(() => {
    if (!enabled) return undefined
    let active = true
    const now = Date.now()
    if (sessionCache !== null && now - sessionCacheAt < CACHE_TTL_MS) {
      const cached = sessionCache
      void Promise.resolve(cached).then((value) => {
        if (active) setBadges(value)
      })
      return () => {
        active = false
      }
    }

    const request =
      sessionRequest ??
      (sessionRequest = (mode === 'pending-only' ? loadPendingBadges(client) : loadBadges(client))
        .catch(() => sessionCache ?? {})
        .then((value) => {
          sessionCache = { ...(sessionCache ?? {}), ...value }
          sessionCacheAt = Date.now()
          return sessionCache
        })
        .finally(() => {
          sessionRequest = null
        }))

    void request.then((value) => {
      if (active) setBadges(value)
    })

    return () => {
      active = false
    }
  }, [client, mode, enabled])

  return badges
}

async function loadPendingBadges(client: ApiClient): Promise<BadgeMap> {
  const result: BadgeMap = {}
  const [mcp, plugins] = await Promise.allSettled([
    listMcpServers(client),
    listPlugins(client),
  ])
  if (mcp.status === 'fulfilled') {
    result.mcp = {
      value: String(mcp.value.filter((item) => item.state === 'active').length),
      pending: mcp.value.some((item) => item.state === 'pending_approval'),
    }
  }
  if (plugins.status === 'fulfilled') {
    result.plugins = {
      value: String(plugins.value.filter((item) => item.state === 'active').length),
      pending: plugins.value.some((item) => item.state === 'pending_approval'),
    }
  }
  return result
}

async function loadBadges(client: ApiClient): Promise<BadgeMap> {
  const result: BadgeMap = {}
  const [memory, providers, skills, mcp, plugins, schedules, installation] = await Promise.allSettled([
    listManagedMemories(client, { scope: 'user' }),
    Promise.all(PROVIDER_NAMES.map((provider) => inspectProvider(client, provider).catch(() => null))),
    createSkillsClient(client).list(),
    listMcpServers(client),
    listPlugins(client),
    listScheduledChats(client),
    getInstallationStatus(client),
  ])
  if (memory.status === 'fulfilled') result.memory = { value: String(memory.value.items.length) }
  if (providers.status === 'fulfilled') result.providers = { value: String(providers.value.filter((state) => state?.enabled === true).length) }
  if (skills.status === 'fulfilled') result.skills = { value: String(skills.value.items.length) }
  if (mcp.status === 'fulfilled') result.mcp = { value: String(mcp.value.filter((item) => item.state === 'active').length), pending: mcp.value.some((item) => item.state === 'pending_approval') }
  if (plugins.status === 'fulfilled') result.plugins = { value: String(plugins.value.filter((item) => item.state === 'active').length), pending: plugins.value.some((item) => item.state === 'pending_approval') }
  if (schedules.status === 'fulfilled') result.schedules = { value: String(schedules.value.items.filter((item) => item.state === 'ACTIVE').length) }
  if (installation.status === 'fulfilled') result.version = { value: `v${installation.value.current_version}` }
  return result
}

export function resetSettingsBadgeCache(): void {
  sessionCache = null
  sessionCacheAt = 0
  sessionRequest = null
}

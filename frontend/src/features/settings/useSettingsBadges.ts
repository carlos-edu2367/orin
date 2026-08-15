import { useEffect, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { getInstallationStatus } from '../../api/installation'
import { listManagedMemories } from '../../api/memory'
import { listMcpServers } from '../../api/mcp'
import { listPlugins } from '../../api/plugins'
import { PROVIDER_NAMES, inspectProvider } from '../../api/providers'
import { listScheduledChats } from '../../api/schedules'
import { createSkillsClient } from '../../api/skills'
import type { BadgeMap } from './SettingsNav'

let sessionCache: BadgeMap | null = null
let sessionRequest: Promise<BadgeMap> | null = null

export function useSettingsBadges(client: ApiClient = createBrowserApiClient()): BadgeMap {
  const [badges, setBadges] = useState<BadgeMap>(() => sessionCache ?? {})
  useEffect(() => {
    let active = true
    const request = sessionRequest ?? (sessionRequest = loadBadges(client).catch(() => sessionCache ?? {}).then((value) => { sessionCache = value; return value }))
    request.then((value) => { if (active) setBadges(value) })
    return () => { active = false }
  }, [client])
  return badges
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
  sessionRequest = null
}

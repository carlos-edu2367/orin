import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type InstalledVersion = { version: string; is_current: boolean; removable: boolean }
export type LatestRelease = { version: string; url: string; published_at?: string }
export type InstallationStatus = {
  installation_kind: 'development' | 'installed'
  current_version: string
  installed_versions: InstalledVersion[]
  latest_release: LatestRelease | null
  latest_release_error: 'unavailable' | null
  update_available: boolean
  checked_at: string
}

function parseStatus(value: unknown): InstallationStatus {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  if (data.installation_kind !== 'development' && data.installation_kind !== 'installed') throw invalidResponseError()
  if (typeof data.current_version !== 'string' || typeof data.checked_at !== 'string') throw invalidResponseError()
  if (!Array.isArray(data.installed_versions)) throw invalidResponseError()
  const installed_versions = data.installed_versions.map((item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw invalidResponseError()
    const version = item as Record<string, unknown>
    if (typeof version.version !== 'string' || typeof version.is_current !== 'boolean' || typeof version.removable !== 'boolean') throw invalidResponseError()
    return { version: version.version, is_current: version.is_current, removable: version.removable }
  })
  let latest_release: LatestRelease | null = null
  if (data.latest_release !== null) {
    if (!data.latest_release || typeof data.latest_release !== 'object' || Array.isArray(data.latest_release)) throw invalidResponseError()
    const release = data.latest_release as Record<string, unknown>
    if (typeof release.version !== 'string' || typeof release.url !== 'string') throw invalidResponseError()
    latest_release = { version: release.version, url: release.url, ...(typeof release.published_at === 'string' ? { published_at: release.published_at } : {}) }
  }
  if (data.latest_release_error !== null && data.latest_release_error !== 'unavailable') throw invalidResponseError()
  if (typeof data.update_available !== 'boolean') throw invalidResponseError()
  return {
    installation_kind: data.installation_kind, current_version: data.current_version, installed_versions,
    latest_release, latest_release_error: data.latest_release_error, update_available: data.update_available,
    checked_at: data.checked_at,
  }
}

export function getInstallationStatus(client: ApiClient, signal?: AbortSignal): Promise<InstallationStatus> {
  return client.request({ path: '/v1/installation/status', signal, parse: parseStatus })
}

export function removeInstalledVersion(client: ApiClient, version: string, intent = client.createMutationIntent()): Promise<{ removed_version: string }> {
  return client.request({ path: `/v1/installation/versions/${encodeURIComponent(version)}`, method: 'DELETE', intent, parse: (value) => {
    if (!value || typeof value !== 'object' || Array.isArray(value) || typeof (value as Record<string, unknown>).removed_version !== 'string') throw invalidResponseError()
    return { removed_version: (value as Record<string, unknown>).removed_version as string }
  } })
}

export function installLatestRelease(client: ApiClient, intent = client.createMutationIntent()): Promise<{ started: boolean }> {
  return client.request({ path: '/v1/installation/update', method: 'POST', intent, parse: (value) => {
    if (!value || typeof value !== 'object' || Array.isArray(value) || (value as Record<string, unknown>).started !== true) throw invalidResponseError()
    return { started: true }
  } })
}

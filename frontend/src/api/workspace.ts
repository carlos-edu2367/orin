import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type WorkspaceRisk = 'none' | 'drive_root' | 'system' | 'home_root' | 'orin_data'

export type WorkspaceState = {
  kind: 'managed' | 'local'
  path: string | null
  folderName: string | null
  scope: 'chat' | 'project'
  projectName: string | null
}

export type FolderInspection = {
  kind: 'folder'
  path: string
  exists: boolean
  isDirectory: boolean
  writable: boolean
  entryCount: number
  entriesTruncated: boolean
  risk: WorkspaceRisk
}

/** The dialog was closed without a choice, or it could not be opened at all. */
export type InspectionOutcome = FolderInspection | { kind: 'cancelled' } | { kind: 'unavailable' }

export function inspectWorkspaceFolder(client: ApiClient, conversationId: string, path: string | null, intent = client.createMutationIntent()): Promise<InspectionOutcome> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/workspace/inspect`, method: 'POST', intent,
    body: { path },
    parse: parseInspection,
  })
}

export function attachWorkspaceFolder(client: ApiClient, conversationId: string, path: string, acknowledgedRisk: boolean, intent = client.createMutationIntent()): Promise<WorkspaceState> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/workspace`, method: 'PUT', intent,
    body: { path, acknowledged_risk: acknowledgedRisk },
    parse: parseWorkspaceState,
  })
}

export function detachWorkspaceFolder(client: ApiClient, conversationId: string, intent = client.createMutationIntent()): Promise<WorkspaceState> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/workspace`, method: 'DELETE', intent,
    parse: parseWorkspaceState,
  })
}

export function parseWorkspaceState(value: unknown): WorkspaceState {
  const data = record(value)
  const kind = data.kind === 'local' ? 'local' : 'managed'
  return {
    kind,
    path: typeof data.path === 'string' ? data.path : null,
    folderName: typeof data.folder_name === 'string' ? data.folder_name : null,
    scope: data.scope === 'project' ? 'project' : 'chat',
    projectName: typeof data.project_name === 'string' ? data.project_name : null,
  }
}

function parseInspection(value: unknown): InspectionOutcome {
  const data = record(value)
  if (data.cancelled === true) return { kind: 'cancelled' }
  if (data.dialog_unavailable === true) return { kind: 'unavailable' }
  if (typeof data.path !== 'string') throw invalidResponseError()
  return {
    kind: 'folder',
    path: data.path,
    exists: data.exists === true,
    isDirectory: data.is_directory === true,
    writable: data.writable === true,
    entryCount: typeof data.entry_count === 'number' ? data.entry_count : 0,
    entriesTruncated: data.entries_truncated === true,
    risk: RISKS.includes(data.risk as WorkspaceRisk) ? (data.risk as WorkspaceRisk) : 'none',
  }
}

const RISKS: WorkspaceRisk[] = ['none', 'drive_root', 'system', 'home_root', 'orin_data']

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}

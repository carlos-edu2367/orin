import type { ApiClient } from './client'
import { invalidResponseError } from './errors'
import { parseWorkspaceState, type WorkspaceState } from './workspace'

export type ProjectChat = { conversation_id: string; title: string; state: string; updated_at: string | null; mode?: 'chat' | 'code' }
export type ProjectSidebarChat = ProjectChat
export type Project = {
  project_id: string
  name: string
  description: string | null
  workspace_id: string
  workspace?: WorkspaceState
  created_at: string | null
  updated_at: string | null
  archived_at: string | null
}
export type ProjectSidebarItem = Pick<Project, 'project_id' | 'name' | 'description' | 'workspace'> & { chats: ProjectChat[] }

export type CreateProjectInput = {
  name: string
  description?: string | null
  workspace_path?: string | null
  workspace_acknowledged_risk?: boolean
  source_conversation_id?: string | null
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}
function text(value: unknown): string { if (typeof value !== 'string' || !value) throw invalidResponseError(); return value }
function optionalText(value: unknown): string | null { return typeof value === 'string' ? value : null }
function project(value: unknown): Project {
  const item = record(value)
  return {
    project_id: text(item.project_id),
    name: text(item.name),
    description: optionalText(item.description),
    workspace_id: text(item.workspace_id),
    ...(item.workspace && typeof item.workspace === 'object' ? { workspace: parseWorkspaceState(item.workspace) } : {}),
    created_at: optionalText(item.created_at),
    updated_at: optionalText(item.updated_at),
    archived_at: optionalText(item.archived_at),
  }
}

export function listProjectSidebar(client: ApiClient): Promise<{ items: ProjectSidebarItem[] }> {
  return client.request({ path: '/v1/projects/sidebar', parse: (value) => {
    const data = record(value)
    if (!Array.isArray(data.items)) throw invalidResponseError()
    return { items: data.items.map((value) => {
      const item = record(value)
      if (!Array.isArray(item.chats)) throw invalidResponseError()
      return {
        project_id: text(item.project_id),
        name: text(item.name),
        description: optionalText(item.description),
        ...(item.workspace && typeof item.workspace === 'object' ? { workspace: parseWorkspaceState(item.workspace) } : {}),
        chats: item.chats.map((chat) => {
          const row = record(chat)
          const mode: 'chat' | 'code' = row.mode === 'code' ? 'code' : 'chat'
          return {
            conversation_id: text(row.conversation_id),
            title: text(row.title),
            state: text(row.state),
            updated_at: optionalText(row.updated_at),
            mode,
          }
        }),
      }
    }) }
  } })
}

export function createProject(client: ApiClient, input: CreateProjectInput, intent = client.createMutationIntent()): Promise<Project> {
  return client.request({
    path: '/v1/projects',
    method: 'POST',
    expectedStatus: 201,
    intent,
    body: {
      name: input.name,
      description: input.description ?? null,
      ...(input.workspace_path !== undefined ? { workspace_path: input.workspace_path } : {}),
      ...(input.workspace_acknowledged_risk !== undefined ? { workspace_acknowledged_risk: input.workspace_acknowledged_risk } : {}),
      ...(input.source_conversation_id !== undefined ? { source_conversation_id: input.source_conversation_id } : {}),
    },
    parse: project,
  })
}

export function attachProjectWorkspace(
  client: ApiClient,
  projectId: string,
  path: string,
  acknowledgedRisk = true,
  intent = client.createMutationIntent(),
): Promise<WorkspaceState> {
  return client.request({
    path: `/v1/projects/${encodeURIComponent(projectId)}/workspace`,
    method: 'PUT',
    intent,
    body: { path, acknowledged_risk: acknowledgedRisk },
    parse: parseWorkspaceState,
  })
}

export function detachProjectWorkspace(
  client: ApiClient,
  projectId: string,
  intent = client.createMutationIntent(),
): Promise<WorkspaceState> {
  return client.request({
    path: `/v1/projects/${encodeURIComponent(projectId)}/workspace`,
    method: 'DELETE',
    intent,
    parse: parseWorkspaceState,
  })
}

export function listProjects(client: ApiClient): Promise<{ items: Project[] }> {
  return client.request({ path: '/v1/projects', parse: (value) => {
    const data = record(value)
    if (!Array.isArray(data.items)) throw invalidResponseError()
    return { items: data.items.map(project) }
  } })
}

export function createProjectConversation(client: ApiClient, projectId: string, input: { message: string; provider: string; model_id: string }, intent = client.createMutationIntent()): Promise<{ conversation_id: string }> {
  return client.request({ path: `/v1/projects/${encodeURIComponent(projectId)}/conversations`, method: 'POST', expectedStatus: 201, intent, body: { message: input.message, selection: { provider: input.provider, model_id: input.model_id } }, parse: (value) => ({ conversation_id: text(record(value).conversation_id) }) })
}

export function listProjectMemories(client: ApiClient, projectId: string): Promise<{ items: Array<{ memory_id: string; fact: string; tags: string[]; updated_at: string | null }> }> {
  return client.request({ path: `/v1/projects/${encodeURIComponent(projectId)}/memories`, parse: (value) => { const data = record(value); if (!Array.isArray(data.items)) throw invalidResponseError(); return { items: data.items.map((item) => { const row = record(item); return { memory_id: text(row.memory_id), fact: text(row.fact), tags: Array.isArray(row.tags) ? row.tags.filter((tag): tag is string => typeof tag === 'string') : [], updated_at: optionalText(row.updated_at) } }) } } })
}

export function deleteProjectMemory(client: ApiClient, projectId: string, memoryId: string, intent = client.createMutationIntent()): Promise<void> {
  return client.request({ path: `/v1/projects/${encodeURIComponent(projectId)}/memories/${encodeURIComponent(memoryId)}`, method: 'DELETE', expectedStatus: 204, intent, parse: () => undefined })
}

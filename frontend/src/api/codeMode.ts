import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type CodePermissionMode = 'accept_edits' | 'plan_mode' | 'ask_permissions' | 'full_autonomy'
export type PermissionMode = CodePermissionMode
export type CodeAutonomy =
  | 'approval_required'
  | 'code_autonomy'
  | 'full_autonomy'
  | 'plan_mode'
  | 'ask_permissions'
  | 'accept_edits'

export type CodeModeSettings = {
  autonomy: CodeAutonomy
  system_notifications: boolean
  monitoring_enabled: boolean
}

export const CODE_PERMISSION_MODES: ReadonlyArray<{
  mode: CodePermissionMode
  autonomy: CodeAutonomy
  label: string
  shortLabel: string
}> = [
  { mode: 'accept_edits', autonomy: 'code_autonomy', label: 'Aceitar edições automaticamente', shortLabel: 'Aceitar edições' },
  { mode: 'plan_mode', autonomy: 'plan_mode', label: 'Modo Plano', shortLabel: 'Plano' },
  { mode: 'ask_permissions', autonomy: 'approval_required', label: 'Perguntar permissões', shortLabel: 'Perguntar' },
  { mode: 'full_autonomy', autonomy: 'full_autonomy', label: 'Autonomia total', shortLabel: 'Autonomia total' },
]

const VALID_AUTONOMY = new Set<string>([
  'approval_required',
  'code_autonomy',
  'full_autonomy',
  'plan_mode',
  'ask_permissions',
  'accept_edits',
])

export function normalizeCodePermissionMode(value: string | undefined | null): CodePermissionMode {
  switch (value) {
    case 'approval_required':
    case 'ask_permissions':
      return 'ask_permissions'
    case 'code_autonomy':
    case 'accept_edits':
      return 'accept_edits'
    case 'plan_mode':
      return 'plan_mode'
    case 'full_autonomy':
      return 'full_autonomy'
    default:
      return 'ask_permissions'
  }
}

export function permissionModeToAutonomy(mode: CodePermissionMode): CodeAutonomy {
  switch (mode) {
    case 'ask_permissions':
      return 'approval_required'
    case 'accept_edits':
      return 'code_autonomy'
    case 'plan_mode':
      return 'plan_mode'
    case 'full_autonomy':
      return 'full_autonomy'
  }
}

export function nextCodePermissionMode(current: CodePermissionMode): CodePermissionMode {
  if (current === 'full_autonomy') return 'ask_permissions'
  const order: CodePermissionMode[] = ['accept_edits', 'plan_mode', 'ask_permissions']
  const idx = order.indexOf(current)
  return order[(idx === -1 ? 0 : idx + 1) % order.length]
}

function parse(value: unknown): CodeModeSettings {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  const autonomy = data.autonomy
  if (typeof autonomy !== 'string' || !VALID_AUTONOMY.has(autonomy)) throw invalidResponseError()
  if (typeof data.system_notifications !== 'boolean' || typeof data.monitoring_enabled !== 'boolean') throw invalidResponseError()
  return {
    autonomy: autonomy as CodeAutonomy,
    system_notifications: data.system_notifications,
    monitoring_enabled: data.monitoring_enabled,
  }
}

export function getCodeModeSettings(client: ApiClient, signal?: AbortSignal): Promise<CodeModeSettings> {
  return client.request({ path: '/v1/code-mode/settings', signal, parse })
}

export function setCodeModeSettings(client: ApiClient, settings: CodeModeSettings, intent = client.createMutationIntent()): Promise<CodeModeSettings> {
  return client.request({ path: '/v1/code-mode/settings', method: 'PUT', body: settings, intent, parse })
}

export type WorktreeInfo = {
  name: string
  path: string
  branch: string
  head_commit: string
  is_main: boolean
  copied_files: string[]
}

export type ConversationWorktreeResponse = {
  is_git_repo: boolean
  active: boolean
  worktree: WorktreeInfo | null
  worktrees: WorktreeInfo[]
}

export type ManageWorktreeInput = {
  action?: 'enter' | 'exit' | 'create' | 'remove' | 'status' | 'list'
  name?: string | null
  base_branch?: string | null
  cleanup?: 'keep' | 'remove'
  force?: boolean
  enabled?: boolean
}

function parseWorktreeInfo(value: unknown): WorktreeInfo | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const data = value as Record<string, unknown>
  if (typeof data.name !== 'string' || typeof data.path !== 'string') return null
  return {
    name: data.name,
    path: data.path,
    branch: typeof data.branch === 'string' ? data.branch : '',
    head_commit: typeof data.head_commit === 'string' ? data.head_commit : '',
    is_main: data.is_main === true,
    copied_files: Array.isArray(data.copied_files)
      ? data.copied_files.filter((item): item is string => typeof item === 'string')
      : [],
  }
}

function parseWorktreeResponse(value: unknown): ConversationWorktreeResponse {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  const worktrees = Array.isArray(data.worktrees)
    ? data.worktrees.map(parseWorktreeInfo).filter((item): item is WorktreeInfo => item !== null)
    : []
  return {
    is_git_repo: data.is_git_repo === true,
    active: data.active === true,
    worktree: parseWorktreeInfo(data.worktree),
    worktrees,
  }
}

export function listWorkspaceFiles(
  client: ApiClient,
  conversationId: string,
  query = '',
  limit = 20,
  signal?: AbortSignal,
): Promise<string[]> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/workspace-files`,
    query: {
      ...(query ? { query } : {}),
      limit,
    },
    signal,
    parse: (value) => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
      const data = value as Record<string, unknown>
      const raw = Array.isArray(data.items) ? data.items : Array.isArray(data.files) ? data.files : []
      return raw.filter((item): item is string => typeof item === 'string')
    },
  })
}

export function getConversationWorktree(
  client: ApiClient,
  conversationId: string,
  signal?: AbortSignal,
): Promise<ConversationWorktreeResponse> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/worktree`,
    signal,
    parse: parseWorktreeResponse,
  })
}

export function manageConversationWorktree(
  client: ApiClient,
  conversationId: string,
  input: ManageWorktreeInput,
  intent = client.createMutationIntent(),
): Promise<ConversationWorktreeResponse> {
  return client.request({
    path: `/v1/conversations/${encodeURIComponent(conversationId)}/worktree`,
    method: 'POST',
    intent,
    body: input,
    parse: parseWorktreeResponse,
  })
}


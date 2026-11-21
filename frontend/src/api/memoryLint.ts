import type { ApiClient, MutationIntent } from './client'
import { invalidResponseError } from './errors'

export type LintContradiction = {
  memory_id: string
  memory_fact: string
  file: string
  line: number
  quoted_line: string
  reason: string
}

export type LintReport = {
  contradictions: LintContradiction[]
  files_checked: string[]
  memories_checked: number
  estimated_calls: number
  dry_run: boolean
  warning: string | null
}

export type RunMemoryLintOptions = {
  workspaceRoot?: string
  projectId?: string
  dryRun?: boolean
}

function parseContradiction(value: unknown): LintContradiction {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw invalidResponseError()
  }
  const source = value as Record<string, unknown>
  if (
    typeof source.memory_id !== 'string' ||
    typeof source.memory_fact !== 'string' ||
    typeof source.file !== 'string' ||
    typeof source.line !== 'number' ||
    typeof source.quoted_line !== 'string' ||
    typeof source.reason !== 'string'
  ) {
    throw invalidResponseError()
  }
  return {
    memory_id: source.memory_id,
    memory_fact: source.memory_fact,
    file: source.file,
    line: source.line,
    quoted_line: source.quoted_line,
    reason: source.reason,
  }
}

function parseLintReport(value: unknown): LintReport {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw invalidResponseError()
  }
  const source = value as Record<string, unknown>
  if (
    !Array.isArray(source.contradictions) ||
    !Array.isArray(source.files_checked) ||
    typeof source.memories_checked !== 'number' ||
    typeof source.estimated_calls !== 'number' ||
    typeof source.dry_run !== 'boolean'
  ) {
    throw invalidResponseError()
  }
  const warning =
    source.warning === null || source.warning === undefined
      ? null
      : typeof source.warning === 'string'
        ? source.warning
        : null

  return {
    contradictions: source.contradictions.map(parseContradiction),
    files_checked: source.files_checked.filter((item): item is string => typeof item === 'string'),
    memories_checked: source.memories_checked,
    estimated_calls: source.estimated_calls,
    dry_run: source.dry_run,
    warning,
  }
}

export function runMemoryInstructionLint(
  client: ApiClient,
  options: RunMemoryLintOptions = {},
  intent: MutationIntent = client.createMutationIntent(),
): Promise<LintReport> {
  const body: Record<string, unknown> = {
    dry_run: Boolean(options.dryRun),
  }
  if (options.workspaceRoot && options.workspaceRoot.trim()) {
    body.workspace_root = options.workspaceRoot.trim()
  }
  if (options.projectId && options.projectId.trim()) {
    body.project_id = options.projectId.trim()
  }

  return client.request({
    path: '/v1/memory/lint',
    method: 'POST',
    body,
    intent,
    parse: parseLintReport,
  })
}

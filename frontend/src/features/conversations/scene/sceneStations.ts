import type { SceneStationId } from './sceneTypes'

/**
 * Single mapping table of normalized tool names to office stations in the Cena.
 */
export const TOOL_STATION_TABLE: Record<string, SceneStationId> = {
  // Terminal station
  run_command: 'terminal',
  powershell: 'terminal',
  power_shell: 'terminal',
  verify_project: 'terminal',
  bash: 'terminal',
  bash_output: 'terminal',
  task_output: 'terminal',
  task_stop: 'terminal',
  kill_shell: 'terminal',
  read_process_output: 'terminal',
  stop_process: 'terminal',
  monitor: 'terminal',
  manage_task: 'terminal',
  enter_worktree: 'terminal',
  exit_worktree: 'terminal',

  // Files station
  read_file: 'files',
  list_files: 'files',
  glob_files: 'files',
  search_files: 'files',
  search_code: 'files',
  view_file: 'files',
  transcribe_pdf: 'files',
  grep: 'files',
  glob: 'files',
  project_map: 'files',
  read: 'files',
  ls: 'files',

  // Writing station
  write_file: 'writing',
  edit_file: 'writing',
  notebook_edit: 'writing',
  multi_edit: 'writing',
  write: 'writing',
  edit: 'writing',

  // Browser station
  fetch_url: 'browser',
  web_fetch: 'browser',
  web_search: 'browser',
  browse_page: 'browser',
  preview_start: 'browser',
  preview_stop: 'browser',
  preview_open: 'browser',
  preview_logs: 'browser',

  // Planning station
  write_contract: 'planning',
  todo_write: 'planning',
  task_create: 'planning',
  task_update: 'planning',
  task_list: 'planning',
  task_get: 'planning',
  enter_plan_mode: 'planning',
  exit_plan_mode: 'planning',
  remember: 'planning',
  recall: 'planning',

  // Meeting station
  run_subagent: 'meeting',
  ask_agent: 'meeting',
  ask_agents: 'meeting',
  send_message: 'meeting',
  agent: 'meeting',
  task: 'meeting',
  ask_user: 'meeting',
  ask_user_question: 'meeting',
  check_subagent: 'meeting',
  create_agent: 'meeting',

  // Extensions station
  use_skill: 'extensions',
  skill: 'extensions',
  search_skills: 'extensions',
  list_skills: 'extensions',
  read_skill_resource: 'extensions',
  create_skill: 'extensions',
  edit_skill: 'extensions',
  install_plugin: 'extensions',
  search_plugin: 'extensions',
  inspect_plugin: 'extensions',
  list_plugins: 'extensions',
  uninstall_plugin: 'extensions',
  configure_mcp: 'extensions',
  list_mcp_catalog: 'extensions',
  list_mcp_servers: 'extensions',
  test_mcp_server: 'extensions',
  tool_search: 'extensions',
  call_mcp_tool: 'extensions',
  inspect_mcp_server: 'extensions',
  refresh_mcp_tools: 'extensions',
  search_mcp_tools: 'extensions',
  list_mcp_resources: 'extensions',
  read_mcp_resource: 'extensions',
  read_mcp_resource_dir: 'extensions',
  list_resources: 'extensions',
  read_resource: 'extensions',
}

export const STATION_LABELS: Record<SceneStationId, string> = {
  terminal: 'Mesa do terminal',
  files: 'Estante de arquivos',
  writing: 'Bancada de escrita',
  browser: 'Janela do navegador',
  planning: 'Quadro de planos',
  meeting: 'Mesa de reunião',
  extensions: 'Painel de extensões',
  generic: 'Mesa geral',
  lounge: 'Sofá',
}

export const STATION_SHORT_LABELS: Record<SceneStationId, string> = {
  terminal: 'Terminal',
  files: 'Arquivos',
  writing: 'Escrita',
  browser: 'Navegador',
  planning: 'Planos',
  meeting: 'Reunião',
  extensions: 'Extensões',
  generic: 'Mesa geral',
  lounge: 'Sofá',
}


const AGENT_DELEGATION_TOOLS = new Set([
  'run_subagent',
  'ask_agent',
  'ask_agents',
  'send_message',
  'agent',
  'task',
  'create_agent',
])

export function normalizeToolName(raw: string): string {
  const trimmed = raw.trim()
  if (!trimmed) return ''
  if (trimmed.toLowerCase().startsWith('mcp__')) {
    return trimmed.toLowerCase()
  }
  return trimmed
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1_$2')
    .replace(/[-\s]+/g, '_')
    .toLowerCase()
}

export function isAgentTool(toolName?: string | null, toolKind?: string | null): boolean {
  if (typeof toolKind === 'string' && toolKind.trim().toLowerCase() === 'agent') {
    return true
  }
  if (typeof toolName === 'string' && toolName.trim()) {
    const normalized = normalizeToolName(toolName)
    if (AGENT_DELEGATION_TOOLS.has(normalized)) return true
  }
  return false
}

export function stationForTool(
  toolName?: string | null,
  toolKind?: string | null,
): SceneStationId {
  if (typeof toolName === 'string' && toolName.trim()) {
    const lower = toolName.trim().toLowerCase()
    const normalized = normalizeToolName(toolName)

    if (
      lower.startsWith('mcp__') ||
      lower.startsWith('mcp_') ||
      normalized.startsWith('mcp__') ||
      normalized.startsWith('mcp_') ||
      normalized.includes('_mcp_')
    ) {
      return 'extensions'
    }

    const fromTable = TOOL_STATION_TABLE[normalized] ?? TOOL_STATION_TABLE[lower]
    if (fromTable) {
      return fromTable
    }

    if (normalized.startsWith('browser_') || normalized.startsWith('preview_')) {
      return 'browser'
    }
    if (normalized.startsWith('task_')) {
      return 'planning'
    }
    if (
      normalized.startsWith('skill_') ||
      normalized.endsWith('_skill') ||
      normalized.endsWith('_plugin') ||
      normalized.endsWith('_mcp')
    ) {
      return 'extensions'
    }
  }

  if (typeof toolKind === 'string' && toolKind.trim()) {
    const kind = toolKind.trim().toLowerCase()
    switch (kind) {
      case 'terminal':
      case 'shell':
        return 'terminal'
      case 'filesystem':
        return 'files'
      case 'web':
      case 'browser':
        return 'browser'
      case 'agent':
      case 'user_input':
        return 'meeting'
      case 'memory':
      case 'planning':
        return 'planning'
      case 'mcp':
      case 'skill':
      case 'plugin':
        return 'extensions'
      case 'artifact':
        return 'writing'
      default:
        return 'generic'
    }
  }

  return 'generic'
}

/**
 * The settings the interface edits (W7): effective settings with provenance, permission rules per layer, hooks,
 * agents, output styles, keybindings and trusted folders. Local profile only: a shared server answers 404.
 */
import type { ApiClient } from './client'
import { bool, list, num, optStr, rec, str, strings } from './parse'

export type SettingsLayerName = 'user' | 'project' | 'local' | 'cli' | 'managed'
export type WritableLayer = 'user' | 'project' | 'local'
export type RuleKind = 'allow' | 'ask' | 'deny' | 'additionalDirectories'

export type RuleEntry = { rule: string; layer: SettingsLayerName; file: string | null; duplicate: boolean }
export type SettingsFile = { path: string; exists: boolean }

export type EffectiveSettings = {
  projectRoot: string | null
  trusted: boolean
  values: Record<string, unknown>
  provenance: Record<string, string>
  layers: Array<{ name: SettingsLayerName; file: string | null; data: Record<string, unknown> }>
  files: Record<string, SettingsFile[]>
  writable: Record<WritableLayer, string | null>
  permissions: Record<RuleKind, RuleEntry[]>
  errors: Array<{ file: string; message: string }>
  untrustedIgnored: string[]
}

const LAYERS: readonly SettingsLayerName[] = ['user', 'project', 'local', 'cli', 'managed']
const KINDS: readonly RuleKind[] = ['allow', 'ask', 'deny', 'additionalDirectories']

function layerName(value: unknown): SettingsLayerName | null {
  return LAYERS.includes(value as SettingsLayerName) ? (value as SettingsLayerName) : null
}

function ruleEntry(value: unknown): RuleEntry | null {
  const data = rec(value)
  const layer = layerName(data.layer)
  const rule = str(data.rule)
  return layer && rule ? { rule, layer, file: optStr(data.file), duplicate: bool(data.duplicate) } : null
}

function permissionsOf(value: unknown): Record<RuleKind, RuleEntry[]> {
  const data = rec(value)
  return Object.fromEntries(KINDS.map((kind) => [kind, list(data[kind], ruleEntry)])) as Record<RuleKind, RuleEntry[]>
}

function stringMap(value: unknown): Record<string, string> {
  return Object.fromEntries(Object.entries(rec(value)).filter((entry): entry is [string, string] => typeof entry[1] === 'string'))
}

export function parseEffectiveSettings(value: unknown): EffectiveSettings {
  const data = rec(value)
  const writable = rec(data.writable)
  return {
    projectRoot: optStr(data.project_root),
    trusted: bool(data.trusted),
    values: rec(data.values),
    provenance: stringMap(data.provenance),
    layers: list(data.layers, (item) => {
      const layer = rec(item)
      const name = layerName(layer.name)
      return name ? { name, file: optStr(layer.file), data: rec(layer.data) } : null
    }),
    files: Object.fromEntries(Object.entries(rec(data.files)).map(([layer, items]) => [
      layer, list(items, (item) => { const file = rec(item); return str(file.path) ? { path: str(file.path), exists: bool(file.exists) } : null }),
    ])),
    writable: { user: optStr(writable.user), project: optStr(writable.project), local: optStr(writable.local) },
    permissions: permissionsOf(data.permissions),
    errors: list(data.errors, (item) => { const error = rec(item); return { file: str(error.file), message: str(error.message) } }),
    untrustedIgnored: strings(data.untrusted_ignored),
  }
}

function query(conversationId?: string | null) {
  return conversationId ? { conversation_id: conversationId } : undefined
}

export function getEffectiveSettings(client: ApiClient, conversationId?: string | null): Promise<EffectiveSettings> {
  return client.request({ path: '/v1/settings/effective', query: query(conversationId), parse: parseEffectiveSettings })
}

export type RuleChange = { layer: WritableLayer; kind: RuleKind; rule: string; op: 'add' | 'remove'; conversationId?: string | null }

export function changePermissionRule(client: ApiClient, change: RuleChange): Promise<Record<RuleKind, RuleEntry[]>> {
  return client.request({
    path: '/v1/settings/permissions',
    method: 'PUT',
    body: { layer: change.layer, kind: change.kind, rule: change.rule, op: change.op, ...(change.conversationId ? { conversation_id: change.conversationId } : {}) },
    parse: (value) => permissionsOf(rec(value).permissions),
  })
}

export type HookSpec = { event: string; matcher: string; type: string; source: string; label: string; timeout: number }
export type HooksReport = {
  disabled: boolean
  effective: HookSpec[]
  editable: Partial<Record<WritableLayer, { file: string; hooks: Record<string, unknown> }>>
  errors: Array<{ source: string; message: string }>
}

export function parseHooksReport(value: unknown): HooksReport {
  const data = rec(value)
  const editable: HooksReport['editable'] = {}
  for (const [layer, entry] of Object.entries(rec(data.editable))) {
    if (layer === 'user' || layer === 'project' || layer === 'local') {
      const item = rec(entry)
      editable[layer] = { file: str(item.file), hooks: rec(item.hooks) }
    }
  }
  return {
    disabled: bool(data.disabled),
    effective: list(data.effective, (item) => {
      const spec = rec(item)
      return { event: str(spec.event), matcher: str(spec.matcher), type: str(spec.type), source: str(spec.source), label: str(spec.label), timeout: num(spec.timeout) }
    }),
    editable,
    errors: list(data.errors, (item) => { const error = rec(item); return { source: str(error.source), message: str(error.message) } }),
  }
}

export function getHooks(client: ApiClient, conversationId?: string | null): Promise<HooksReport> {
  return client.request({ path: '/v1/settings/hooks', query: query(conversationId), parse: parseHooksReport })
}

export function saveHooks(client: ApiClient, layer: WritableLayer, hooks: Record<string, unknown>, conversationId?: string | null): Promise<HooksReport> {
  return client.request({
    path: '/v1/settings/hooks', method: 'PUT',
    body: { layer, hooks, ...(conversationId ? { conversation_id: conversationId } : {}) },
    parse: parseHooksReport,
  })
}

export type AgentDefinition = {
  name: string
  description: string
  origin: string
  path: string | null
  editable: boolean
  prompt: string
  tools: string[] | null
  disallowedTools: string[]
  model: string | null
  permissionMode: string | null
  maxTurns: number | null
  effort: string | null
}
export type AgentsReport = {
  agents: AgentDefinition[]
  errors: Array<{ file: string; message: string }>
  shadowed: Array<{ name: string; kept: string; ignored: string }>
}

export function parseAgentsReport(value: unknown): AgentsReport {
  const data = rec(value)
  return {
    agents: list(data.agents, (item) => {
      const agent = rec(item)
      if (!str(agent.name)) return null
      return {
        name: str(agent.name), description: str(agent.description), origin: str(agent.origin), path: optStr(agent.path),
        editable: bool(agent.editable), prompt: str(agent.prompt),
        tools: Array.isArray(agent.tools) ? strings(agent.tools) : null, disallowedTools: strings(agent.disallowed_tools),
        model: optStr(agent.model), permissionMode: optStr(agent.permission_mode),
        maxTurns: typeof agent.max_turns === 'number' ? agent.max_turns : null, effort: optStr(agent.effort),
      }
    }, 200),
    errors: list(data.errors, (item) => { const error = rec(item); return { file: str(error.file), message: str(error.message) } }),
    shadowed: list(data.shadowed, (item) => { const entry = rec(item); return { name: str(entry.name), kept: str(entry.kept), ignored: str(entry.ignored) } }),
  }
}

export function getAgents(client: ApiClient, conversationId?: string | null): Promise<AgentsReport> {
  return client.request({ path: '/v1/settings/agents', query: query(conversationId), parse: parseAgentsReport })
}

export type AgentDraft = {
  scope: 'user' | 'project'
  description: string
  prompt: string
  tools: string[] | null
  disallowedTools: string[]
  model: string | null
  permissionMode: string | null
  maxTurns: number | null
  effort: string | null
}

export function saveAgent(client: ApiClient, name: string, draft: AgentDraft, conversationId?: string | null): Promise<AgentsReport> {
  return client.request({
    path: `/v1/settings/agents/${encodeURIComponent(name)}`,
    method: 'PUT',
    body: {
      scope: draft.scope, description: draft.description, prompt: draft.prompt, tools: draft.tools,
      disallowed_tools: draft.disallowedTools, ...(draft.model ? { model: draft.model } : {}),
      ...(draft.permissionMode ? { permission_mode: draft.permissionMode } : {}),
      ...(draft.maxTurns ? { max_turns: draft.maxTurns } : {}), ...(draft.effort ? { effort: draft.effort } : {}),
      ...(conversationId ? { conversation_id: conversationId } : {}),
    },
    parse: parseAgentsReport,
  })
}

export function deleteAgent(client: ApiClient, name: string, scope: 'user' | 'project', conversationId?: string | null): Promise<AgentsReport> {
  return client.request({
    path: `/v1/settings/agents/${encodeURIComponent(name)}`, method: 'DELETE',
    query: { scope, ...(conversationId ? { conversation_id: conversationId } : {}) },
    parse: parseAgentsReport,
  })
}

export type OutputStylesReport = { styles: Array<{ key: string; name: string; description: string; source: string }>; active: string; activeLayer: string | null }

export function parseOutputStyles(value: unknown): OutputStylesReport {
  const data = rec(value)
  return {
    styles: list(data.styles, (item) => {
      const style = rec(item)
      return str(style.key) ? { key: str(style.key), name: str(style.name), description: str(style.description), source: str(style.source) } : null
    }),
    active: str(data.active, 'default') || 'default',
    activeLayer: optStr(data.active_layer),
  }
}

export function getOutputStyles(client: ApiClient, conversationId?: string | null): Promise<OutputStylesReport> {
  return client.request({ path: '/v1/settings/output-styles', query: query(conversationId), parse: parseOutputStyles })
}

export function chooseOutputStyle(client: ApiClient, layer: 'user' | 'local', style: string, conversationId?: string | null): Promise<OutputStylesReport> {
  return client.request({
    path: '/v1/settings/output-styles', method: 'PUT',
    body: { layer, style, ...(conversationId ? { conversation_id: conversationId } : {}) },
    parse: parseOutputStyles,
  })
}

export type KeybindingsFile = { path: string; text: string | null; error: string | null }
export type KeybindingsFiles = { orin: KeybindingsFile; claude: KeybindingsFile }

export function parseKeybindingsFiles(value: unknown): KeybindingsFiles {
  const data = rec(value)
  const file = (item: unknown): KeybindingsFile => {
    const entry = rec(item)
    return { path: str(entry.path), text: typeof entry.text === 'string' ? entry.text : null, error: optStr(entry.error) }
  }
  return { orin: file(data.orin), claude: file(data.claude) }
}

export function getKeybindingsFiles(client: ApiClient): Promise<KeybindingsFiles> {
  return client.request({ path: '/v1/settings/keybindings', parse: parseKeybindingsFiles })
}

export function saveKeybindings(client: ApiClient, text: string): Promise<KeybindingsFiles> {
  return client.request({ path: '/v1/settings/keybindings', method: 'PUT', body: { text }, parse: parseKeybindingsFiles })
}

export function getTrustedFolders(client: ApiClient): Promise<string[]> {
  return client.request({ path: '/v1/settings/trusted-folders', parse: (value) => strings(rec(value).folders) })
}

export function revokeTrustedFolder(client: ApiClient, path: string): Promise<string[]> {
  return client.request({ path: '/v1/settings/trusted-folders/revoke', method: 'POST', body: { path }, parse: (value) => strings(rec(value).folders) })
}

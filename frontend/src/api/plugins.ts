import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type PluginContribution = { kind: string; reference: string; display_name: string; enabled?: boolean }
export type PluginCommand = { command_id: string; slug: string; plugin_id: string; description: string; argument_hint: string; qualified: boolean }
export type PluginSummary = { plugin_id: string; version: string; display_name: string; description: string; author: string; homepage: string | null; state: 'pending_approval' | 'active' | 'disabled' | 'rejected'; warnings: string[]; contribution_count: number; install_path?: string }
export type PluginInspectionResult = PluginSummary & { skills: PluginContribution[]; mcp_servers: PluginContribution[]; agents: PluginContribution[]; commands: PluginContribution[]; package_digest: string }
export type MarketplaceEntry = { name: string; reference: string; owner?: string }
export type PluginLibraryEntry = { name: string; description: string; source_url: string; origin: 'registry' | 'web'; installable_kind: 'plugin' | 'mcp_raw' | 'unknown' }
export type PluginLibraryResult = { entries: PluginLibraryEntry[]; web_search_available: boolean }
export type McpLaunchGuess = { display_name: string; transport: 'stdio' | 'http' | null; command: string | null; args: string[]; url: string | null; secret_names: string[]; confidence: 'structured' | 'none' }

export function listPlugins(client: ApiClient, signal?: AbortSignal): Promise<PluginSummary[]> { return client.request({ path: '/v1/plugins', signal, parse: parseList }) }
export function inspectPlugin(client: ApiClient, reference: string, intent = client.createMutationIntent()): Promise<PluginInspectionResult> { return client.request({ path: '/v1/plugins/inspect', method: 'POST', body: { reference }, intent, parse: parseInspection }) }
export function approvePlugin(client: ApiClient, pluginId: string, intent = client.createMutationIntent()): Promise<PluginSummary> { return client.request({ path: pluginPath(pluginId) + '/approve', method: 'POST', intent, parse: parseSummary }) }
export function setPluginEnabled(client: ApiClient, pluginId: string, enabled: boolean, intent = client.createMutationIntent()): Promise<PluginSummary> { return client.request({ path: pluginPath(pluginId) + '/enabled', method: 'PUT', body: { enabled }, intent, parse: parseSummary }) }
export function removePlugin(client: ApiClient, pluginId: string, intent = client.createMutationIntent()): Promise<void> { return client.request({ path: pluginPath(pluginId), method: 'DELETE', expectedStatus: 204, intent, parse: () => undefined }) }
export function listMarketplaces(client: ApiClient, signal?: AbortSignal): Promise<MarketplaceEntry[]> { return client.request({ path: '/v1/plugins/marketplaces', signal, parse: parseMarketplaces }) }
export function addMarketplace(client: ApiClient, reference: string, intent = client.createMutationIntent()): Promise<MarketplaceEntry> { return client.request({ path: '/v1/plugins/marketplaces', method: 'POST', body: { reference }, expectedStatus: 201, intent, parse: parseMarketplace }) }
export function fetchPluginLibrary(client: ApiClient, refresh = false, query?: string, signal?: AbortSignal): Promise<PluginLibraryResult> { return client.request({ path: '/v1/plugins/library', query: { refresh: refresh || undefined, q: query?.trim() || undefined }, signal, parse: parseLibrary }) }
export function inferMcpLaunch(client: ApiClient, sourceUrl: string, intent = client.createMutationIntent()): Promise<McpLaunchGuess> { return client.request({ path: '/v1/plugins/library/infer-mcp', method: 'POST', body: { source_url: sourceUrl }, intent, parse: parseLaunchGuess }) }
export function listPluginCommands(client: ApiClient, signal?: AbortSignal): Promise<PluginCommand[]> { return client.request({ path: '/v1/plugins/commands', signal, parse: parseCommands }) }

function pluginPath(id: string): string { if (!id.trim()) throw new TypeError('A plugin id is required'); return `/v1/plugins/${encodeURIComponent(id)}` }
function record(value: unknown): Record<string, unknown> { if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError(); return value as Record<string, unknown> }
function text(value: unknown): string { if (typeof value !== 'string') throw invalidResponseError(); return value }
function array(value: unknown): string[] { if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) throw invalidResponseError(); return value }
function summary(value: unknown): PluginSummary { const data = record(value); const state = text(data.state); if (!['pending_approval', 'active', 'disabled', 'rejected'].includes(state)) throw invalidResponseError(); return { plugin_id: text(data.plugin_id), version: text(data.version), display_name: text(data.display_name), description: text(data.description ?? ''), author: text(data.author ?? ''), homepage: data.homepage === null || data.homepage === undefined ? null : text(data.homepage), state: state as PluginSummary['state'], warnings: array(data.warnings ?? []), contribution_count: typeof data.contribution_count === 'number' ? data.contribution_count : 0, install_path: data.install_path === undefined ? undefined : text(data.install_path) } }
function parseList(value: unknown): PluginSummary[] { if (!Array.isArray(value)) throw invalidResponseError(); return value.map(summary) }
function contribution(value: unknown): PluginContribution { const data = record(value); return { kind: text(data.kind ?? 'mcp_server'), reference: text(data.reference ?? data.slug ?? ''), display_name: text(data.display_name ?? data.name ?? ''), enabled: data.enabled === undefined ? undefined : data.enabled === true } }
function parseInspection(value: unknown): PluginInspectionResult { const data = record(value); return { ...summary(data), package_digest: text(data.package_digest), skills: Array.isArray(data.skills) ? data.skills.map(contribution) : [], mcp_servers: Array.isArray(data.mcp_servers) ? data.mcp_servers.map(contribution) : [], agents: Array.isArray(data.agents) ? data.agents.map(contribution) : [], commands: Array.isArray(data.commands) ? data.commands.map((item) => { const row = record(item); return { kind: 'command', reference: text(row.command_id), display_name: text(row.slug) } }) : [] } }
function command(value: unknown): PluginCommand { const data = record(value); return { command_id: text(data.command_id), slug: text(data.slug), plugin_id: text(data.plugin_id), description: text(data.description ?? ''), argument_hint: text(data.argument_hint ?? ''), qualified: data.qualified === true } }
function parseCommands(value: unknown): PluginCommand[] { if (!Array.isArray(value)) throw invalidResponseError(); return value.map(command) }
function parseSummary(value: unknown): PluginSummary { return summary(value) }
function parseMarketplace(value: unknown): MarketplaceEntry { const data = record(value); return { name: text(data.name), reference: text(data.reference), owner: data.owner === undefined ? undefined : text(data.owner) } }
function parseMarketplaces(value: unknown): MarketplaceEntry[] { if (!Array.isArray(value)) throw invalidResponseError(); return value.map(parseMarketplace) }
function libraryEntry(value: unknown): PluginLibraryEntry { const data = record(value); const origin = text(data.origin); if (origin !== 'registry' && origin !== 'web') throw invalidResponseError(); return { name: text(data.name), description: text(data.description ?? ''), source_url: text(data.source_url), origin, installable_kind: installableKind(data.installable_kind) } }
function installableKind(value: unknown): PluginLibraryEntry['installable_kind'] { if (value === 'plugin' || value === 'mcp_raw' || value === 'unknown') return value; throw invalidResponseError() }
function parseLibrary(value: unknown): PluginLibraryResult { const data = record(value); if (!Array.isArray(data.entries)) throw invalidResponseError(); return { entries: data.entries.map(libraryEntry), web_search_available: data.web_search_available === true } }
function parseLaunchGuess(value: unknown): McpLaunchGuess {
  const data = record(value)
  return {
    display_name: text(data.display_name),
    transport: nullableTransport(data.transport),
    command: data.command === null || data.command === undefined ? null : text(data.command),
    args: array(data.args ?? []),
    url: data.url === null || data.url === undefined ? null : text(data.url),
    secret_names: array(data.secret_names ?? []),
    confidence: confidenceValue(data.confidence),
  }
}
function nullableTransport(value: unknown): 'stdio' | 'http' | null { if (value === null || value === undefined) return null; if (value === 'stdio' || value === 'http') return value; throw invalidResponseError() }
function confidenceValue(value: unknown): 'structured' | 'none' { if (value === 'structured' || value === 'none') return value; throw invalidResponseError() }

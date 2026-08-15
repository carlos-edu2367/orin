import type { ApiClient, MutationIntent } from './client'
import { invalidResponseError } from './errors'

export type McpTransport = 'stdio' | 'http'
export type McpServerState = 'pending_approval' | 'active' | 'disabled' | 'error'

export type McpSecretRequirement = { name: string; label: string; how_to_obtain: string }

export type McpCatalogEntry = {
  catalog_id: string
  display_name: string
  summary: string
  transport: McpTransport
  setup_instructions: string
  arguments: string[]
  secrets: McpSecretRequirement[]
}

export type McpServerSummary = {
  server_id: string
  slug: string
  display_name: string
  transport: McpTransport
  command: string | null
  args: string[]
  url: string | null
  secret_names: string[]
  catalog_id: string | null
  state: McpServerState
  state_reason: string
  protocol_version: string
  tool_count: number
}

export type CreateMcpServerInput = {
  display_name: string
  catalog_id?: string
  slug?: string
  transport?: McpTransport
  command?: string
  args?: string[]
  url?: string
  secret_names?: string[]
}

export type McpTestResult = { connected: boolean; protocol_version: string; tools: string[]; error: string | null }

export function listMcpCatalog(client: ApiClient, query = '', signal?: AbortSignal): Promise<McpCatalogEntry[]> {
  return client.request({
    path: '/v1/mcp/catalog',
    query: { query: query.trim() || undefined },
    signal,
    parse: parseCatalogEntries,
  })
}

export function listMcpServers(client: ApiClient, signal?: AbortSignal): Promise<McpServerSummary[]> {
  return client.request({ path: '/v1/mcp/servers', signal, parse: parseServerList })
}

export function getMcpServer(client: ApiClient, serverId: string, signal?: AbortSignal): Promise<McpServerSummary> {
  return client.request({ path: serverPath(serverId), signal, parse: parseServer })
}

export function createMcpServer(client: ApiClient, input: CreateMcpServerInput, intent = client.createMutationIntent()): Promise<McpServerSummary> {
  return client.request({ path: '/v1/mcp/servers', method: 'POST', body: input, intent, expectedStatus: 201, parse: parseServer })
}

export function approveMcpServer(client: ApiClient, serverId: string, secrets: Record<string, string>, intent = client.createMutationIntent()): Promise<McpServerSummary> {
  return client.request({ path: `${serverPath(serverId)}/approve`, method: 'POST', body: { secrets }, intent, parse: parseServer })
}

export function testMcpServer(client: ApiClient, serverId: string, intent = client.createMutationIntent()): Promise<McpTestResult> {
  return client.request({ path: `${serverPath(serverId)}/test`, method: 'POST', intent, parse: parseTestResult })
}

export function setMcpServerEnabled(client: ApiClient, serverId: string, enabled: boolean, intent = client.createMutationIntent()): Promise<McpServerSummary> {
  return client.request({ path: `${serverPath(serverId)}/enabled`, method: 'PUT', body: { enabled }, intent, parse: parseServer })
}

export function setMcpToolEnabled(client: ApiClient, serverId: string, toolName: string, enabled: boolean, intent = client.createMutationIntent()): Promise<McpServerSummary> {
  if (!toolName.trim()) throw new TypeError('A tool name is required')
  return client.request({ path: `${serverPath(serverId)}/tools/${encodeURIComponent(toolName)}/enabled`, method: 'PUT', body: { enabled }, intent, parse: parseServer })
}

export function deleteMcpServer(client: ApiClient, serverId: string, intent = client.createMutationIntent()): Promise<void> {
  return client.request({ path: serverPath(serverId), method: 'DELETE', expectedStatus: 204, intent, parse: () => undefined })
}

function serverPath(serverId: string): string {
  if (!serverId.trim()) throw new TypeError('An MCP server id is required')
  return `/v1/mcp/servers/${encodeURIComponent(serverId)}`
}

function parseCatalogEntries(value: unknown): McpCatalogEntry[] {
  const data = record(value)
  if (!Array.isArray(data.entries)) throw invalidResponseError()
  return data.entries.map(parseCatalogEntry)
}

function parseCatalogEntry(value: unknown): McpCatalogEntry {
  const data = record(value)
  if (!Array.isArray(data.secrets) || !Array.isArray(data.arguments)) throw invalidResponseError()
  return {
    catalog_id: text(data.catalog_id), display_name: text(data.display_name), summary: text(data.summary),
    transport: mcpTransport(data.transport), setup_instructions: text(data.setup_instructions),
    arguments: textArray(data.arguments), secrets: data.secrets.map(parseSecretRequirement),
  }
}

function parseSecretRequirement(value: unknown): McpSecretRequirement {
  const data = record(value)
  return { name: text(data.name), label: text(data.label), how_to_obtain: text(data.how_to_obtain) }
}

function parseServerList(value: unknown): McpServerSummary[] {
  if (!Array.isArray(value)) throw invalidResponseError()
  return value.map(parseServer)
}

function parseServer(value: unknown): McpServerSummary {
  const data = record(value)
  return {
    server_id: text(data.server_id), slug: text(data.slug), display_name: text(data.display_name),
    transport: mcpTransport(data.transport), command: nullableText(data.command), args: textArray(data.args),
    url: nullableText(data.url), secret_names: textArray(data.secret_names), catalog_id: nullableText(data.catalog_id),
    state: mcpServerState(data.state), state_reason: text(data.state_reason ?? ''), protocol_version: text(data.protocol_version ?? ''),
    tool_count: number(data.tool_count),
  }
}

function parseTestResult(value: unknown): McpTestResult {
  const data = record(value)
  return {
    connected: data.connected === true, protocol_version: text(data.protocol_version ?? ''),
    tools: textArray(data.tools), error: nullableText(data.error),
  }
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}

function text(value: unknown): string {
  if (typeof value !== 'string') throw invalidResponseError()
  return value
}

function nullableText(value: unknown): string | null {
  return value === null || value === undefined ? null : text(value)
}

function textArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) throw invalidResponseError()
  return value
}

function number(value: unknown): number {
  if (typeof value !== 'number' || Number.isNaN(value)) throw invalidResponseError()
  return value
}

function mcpTransport(value: unknown): McpTransport {
  if (value === 'stdio' || value === 'http') return value
  throw invalidResponseError()
}

function mcpServerState(value: unknown): McpServerState {
  if (value === 'pending_approval' || value === 'active' || value === 'disabled' || value === 'error') return value
  throw invalidResponseError()
}

export type { MutationIntent }

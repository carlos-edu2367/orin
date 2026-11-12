import type { ApiClient, MutationIntent } from './client'
import { invalidResponseError } from './errors'

export type SkillSummary = {
  id: string
  name: string
  description: string
  version: string
  tags: string[]
  source: string
  available: boolean
}

export type SkillDetail = SkillSummary & {
  instructions: string
  dependencies: string[]
  requires_tools: string[]
  versions: string[]
}

export type SkillListOptions = {
  query?: string
  source?: string
  limit?: number
  cursor?: string
}

export type SkillList = { items: SkillSummary[]; next_cursor: string | null }

export type AgentSkillMode = 'auto' | 'pinned'

export type AgentSkillAssociation = { mode: AgentSkillMode; items: SkillSummary[] }

export type AgentSkillAssociationInput = { mode: AgentSkillMode; skill_ids: string[] }

export type SkillAgent = { agent_id: string; mode: AgentSkillMode }

export type CreateSkillInput = {
  name: string
  description: string
  version: string
  tags: string[]
  instructions: string
}

export type UpdateSkillInput = Partial<CreateSkillInput>

export type SkillProposalAction = 'create' | 'edit'

export type SkillProposalStatus = 'pending' | 'approved' | 'dismissed'

export type SkillProposal = {
  proposal_id: string
  action: SkillProposalAction
  target_skill_id: string | null
  name: string
  description: string
  when_to_use: string[]
  when_not_to_use: string[]
  requires_tools: string[]
  tags: string[]
  instructions: string
  status: SkillProposalStatus
  created_at: string
  updated_at: string
  conversation_id?: string | null
}

export type SkillProposalList = { items: SkillProposal[] }

export type SkillProposalListOptions = {
  status?: SkillProposalStatus
}

export type UpdateSkillProposalInput = {
  name?: string
  description?: string
  instructions?: string
  when_to_use?: string[]
  when_not_to_use?: string[]
  requires_tools?: string[]
  tags?: string[]
}

export type ApproveSkillProposalResult = {
  proposal: SkillProposal
  skill: SkillSummary
}

export type DismissSkillProposalResult = {
  proposal_id: string
  status: string
}

export interface SkillsClient {
  list(options?: SkillListOptions, signal?: AbortSignal): Promise<SkillList>
  get(skillId: string, signal?: AbortSignal): Promise<SkillDetail>
  create(input: CreateSkillInput, intent?: MutationIntent): Promise<SkillSummary>
  update(skillId: string, input: UpdateSkillInput, intent?: MutationIntent): Promise<SkillDetail>
  removeVersion(skillId: string, version: string, intent?: MutationIntent): Promise<SkillDetail>
  getAgentSkills(agentId: string, signal?: AbortSignal): Promise<AgentSkillAssociation>
  setAgentSkills(agentId: string, input: AgentSkillAssociationInput, intent?: MutationIntent): Promise<AgentSkillAssociation>
  listSkillAgents(skillId: string, signal?: AbortSignal): Promise<SkillAgent[]>
  listProposals?(options?: SkillProposalListOptions, signal?: AbortSignal): Promise<SkillProposalList>
  updateProposal?(proposalId: string, input: UpdateSkillProposalInput, intent?: MutationIntent): Promise<SkillProposal>
  approveProposal?(proposalId: string, input?: UpdateSkillProposalInput, intent?: MutationIntent): Promise<ApproveSkillProposalResult>
  dismissProposal?(proposalId: string, intent?: MutationIntent): Promise<DismissSkillProposalResult>
}

export function listSkills(client: ApiClient, options: SkillListOptions = {}, signal?: AbortSignal): Promise<SkillList> {
  return client.request({
    path: '/v1/skills',
    query: {
      query: options.query?.trim() || undefined,
      source: options.source?.trim() || undefined,
      limit: options.limit,
      cursor: options.cursor,
    },
    signal,
    parse: parseSkillList,
  })
}

export function getSkill(client: ApiClient, skillId: string, signal?: AbortSignal): Promise<SkillDetail> {
  return client.request({
    path: skillPath(skillId),
    signal,
    parse: parseSkillDetail,
  })
}

export function createSkill(client: ApiClient, input: CreateSkillInput, intent = client.createMutationIntent()): Promise<SkillSummary> {
  return client.request({ path: '/v1/skills', method: 'POST', body: input, intent, expectedStatus: 201, parse: parseSkillSummary })
}

export function updateSkill(client: ApiClient, skillId: string, input: UpdateSkillInput, intent = client.createMutationIntent()): Promise<SkillDetail> {
  return client.request({ path: skillPath(skillId), method: 'PUT', body: input, intent, parse: parseSkillDetail })
}

export function removeSkillVersion(client: ApiClient, skillId: string, version: string, intent = client.createMutationIntent()): Promise<SkillDetail> {
  if (!version.trim()) throw new TypeError('A skill version is required')
  return client.request({ path: `${skillPath(skillId)}/versions/${encodeURIComponent(version)}`, method: 'DELETE', intent, parse: parseSkillDetail })
}

export function getAgentSkills(client: ApiClient, agentId: string, signal?: AbortSignal): Promise<AgentSkillAssociation> {
  return client.request({ path: agentSkillsPath(agentId), signal, parse: parseAgentSkillAssociation })
}

export function setAgentSkills(client: ApiClient, agentId: string, input: AgentSkillAssociationInput, intent = client.createMutationIntent()): Promise<AgentSkillAssociation> {
  return client.request({ path: agentSkillsPath(agentId), method: 'PUT', body: input, intent, parse: parseAgentSkillAssociation })
}

export function listSkillAgents(client: ApiClient, skillId: string, signal?: AbortSignal): Promise<SkillAgent[]> {
  return client.request({ path: `${skillPath(skillId)}/agents`, signal, parse: parseSkillAgents })
}

export function listSkillProposals(
  client: ApiClient,
  options: SkillProposalListOptions = { status: 'pending' },
  signal?: AbortSignal,
): Promise<SkillProposalList> {
  return client.request({
    path: '/v1/skill-proposals',
    query: {
      status: options.status,
    },
    signal,
    parse: parseSkillProposalList,
  })
}

export function updateSkillProposal(
  client: ApiClient,
  proposalId: string,
  input: UpdateSkillProposalInput,
  intent = client.createMutationIntent(),
): Promise<SkillProposal> {
  return client.request({
    path: skillProposalPath(proposalId),
    method: 'PATCH',
    body: input,
    intent,
    parse: parseSkillProposal,
  })
}

export function approveSkillProposal(
  client: ApiClient,
  proposalId: string,
  input: UpdateSkillProposalInput = {},
  intent = client.createMutationIntent(),
): Promise<ApproveSkillProposalResult> {
  return client.request({
    path: `${skillProposalPath(proposalId)}/approve`,
    method: 'POST',
    body: input,
    intent,
    parse: parseApproveSkillProposalResult,
  })
}

export function dismissSkillProposal(
  client: ApiClient,
  proposalId: string,
  intent = client.createMutationIntent(),
): Promise<DismissSkillProposalResult> {
  return client.request({
    path: skillProposalPath(proposalId),
    method: 'DELETE',
    intent,
    parse: parseDismissSkillProposalResult,
  })
}

export function createSkillsClient(client: ApiClient): SkillsClient {
  return {
    list: (options, signal) => listSkills(client, options, signal),
    get: (skillId, signal) => getSkill(client, skillId, signal),
    create: (input, intent) => createSkill(client, input, intent),
    update: (skillId, input, intent) => updateSkill(client, skillId, input, intent),
    removeVersion: (skillId, version, intent) => removeSkillVersion(client, skillId, version, intent),
    getAgentSkills: (agentId, signal) => getAgentSkills(client, agentId, signal),
    setAgentSkills: (agentId, input, intent) => setAgentSkills(client, agentId, input, intent),
    listSkillAgents: (skillId, signal) => listSkillAgents(client, skillId, signal),
    listProposals: (options, signal) => listSkillProposals(client, options, signal),
    updateProposal: (proposalId, input, intent) => updateSkillProposal(client, proposalId, input, intent),
    approveProposal: (proposalId, input, intent) => approveSkillProposal(client, proposalId, input, intent),
    dismissProposal: (proposalId, intent) => dismissSkillProposal(client, proposalId, intent),
  }
}

function skillPath(skillId: string): string {
  if (!skillId.trim()) throw new TypeError('A skill id is required')
  return `/v1/skills/${encodeURIComponent(skillId)}`
}

function skillProposalPath(proposalId: string): string {
  if (!proposalId.trim()) throw new TypeError('A skill proposal id is required')
  return `/v1/skill-proposals/${encodeURIComponent(proposalId)}`
}

function agentSkillsPath(agentId: string): string {
  if (!agentId.trim()) throw new TypeError('An agent id is required')
  return `/v1/agents/${encodeURIComponent(agentId)}/skills`
}

function parseSkillList(value: unknown): SkillList {
  const data = record(value)
  if (!Array.isArray(data.items)) throw invalidResponseError()
  return { items: data.items.map(parseSkillSummary), next_cursor: nullableText(data.next_cursor) }
}

function parseSkillDetail(value: unknown): SkillDetail {
  const data = record(value)
  return {
    ...parseSkillSummary(data),
    instructions: text(data.instructions),
    dependencies: textArray(data.dependencies),
    requires_tools: textArray(data.requires_tools),
    versions: textArray(data.versions),
  }
}

function parseAgentSkillAssociation(value: unknown): AgentSkillAssociation {
  const data = record(value)
  if (!Array.isArray(data.items)) throw invalidResponseError()
  return { mode: agentSkillMode(data.mode), items: data.items.map(parseSkillSummary) }
}

function parseSkillAgents(value: unknown): SkillAgent[] {
  const data = record(value)
  if (!Array.isArray(data.items)) throw invalidResponseError()
  return data.items.map((item) => {
    const agent = record(item)
    return { agent_id: text(agent.agent_id), mode: agentSkillMode(agent.mode) }
  })
}

function parseSkillSummary(value: unknown): SkillSummary {
  const data = record(value)
  return {
    id: text(data.id), name: text(data.name), description: text(data.description), version: text(data.version),
    tags: textArray(data.tags), source: text(data.source), available: data.available === true,
  }
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}

function text(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) throw invalidResponseError()
  return value
}

function nullableText(value: unknown): string | null {
  return value === null || value === undefined ? null : text(value)
}

function textArray(value: unknown): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string' || !item.trim())) throw invalidResponseError()
  return value
}

function agentSkillMode(value: unknown): AgentSkillMode {
  if (value === 'auto' || value === 'pinned') return value
  throw invalidResponseError()
}

function skillProposalAction(value: unknown): SkillProposalAction {
  if (value === 'create' || value === 'edit') return value
  throw invalidResponseError()
}

function skillProposalStatus(value: unknown): SkillProposalStatus {
  if (value === 'pending' || value === 'approved' || value === 'dismissed') return value
  throw invalidResponseError()
}

function optionalTextArray(value: unknown): string[] {
  if (value === undefined || value === null) return []
  return textArray(value)
}

function parseSkillProposal(value: unknown): SkillProposal {
  const data = record(value)
  return {
    proposal_id: text(data.proposal_id),
    action: skillProposalAction(data.action),
    target_skill_id: nullableText(data.target_skill_id),
    name: text(data.name),
    description: text(data.description),
    when_to_use: optionalTextArray(data.when_to_use),
    when_not_to_use: optionalTextArray(data.when_not_to_use),
    requires_tools: optionalTextArray(data.requires_tools),
    tags: optionalTextArray(data.tags),
    instructions: text(data.instructions),
    status: skillProposalStatus(data.status),
    created_at: typeof data.created_at === 'string' ? data.created_at : '',
    updated_at: typeof data.updated_at === 'string' ? data.updated_at : '',
    conversation_id: nullableText(data.conversation_id),
  }
}

function parseSkillProposalList(value: unknown): SkillProposalList {
  const data = record(value)
  if (!Array.isArray(data.items)) throw invalidResponseError()
  return { items: data.items.map(parseSkillProposal) }
}

function parseApproveSkillProposalResult(value: unknown): ApproveSkillProposalResult {
  const data = record(value)
  return {
    proposal: parseSkillProposal(data.proposal),
    skill: parseSkillSummary(data.skill),
  }
}

function parseDismissSkillProposalResult(value: unknown): DismissSkillProposalResult {
  const data = record(value)
  return {
    proposal_id: text(data.proposal_id),
    status: text(data.status),
  }
}

import { ApiClient } from './client'
import { invalidResponseError } from './errors'
import {
  executionStates,
  type ControlExecutionInput,
  type CreateExecutionInput,
  type ExecutionCommandReceipt,
  type ExecutionInput,
  type ExecutionState,
  type ExecutionView,
} from './types'

const stateSet = new Set<string>(executionStates)

export function createExecution(client: ApiClient, input: CreateExecutionInput, intent = client.createMutationIntent()): Promise<ExecutionCommandReceipt> {
  return client.request({
    path: '/v1/executions',
    method: 'POST',
    body: input,
    intent,
    parse: parseExecutionReceipt,
  })
}

export function getExecution(client: ApiClient, executionId: string, signal?: AbortSignal): Promise<ExecutionView> {
  return client.request({
    path: `/v1/executions/${encodeURIComponent(executionId)}`,
    signal,
    parse: parseExecutionView,
  })
}

export function listExecutions(client: ApiClient, signal?: AbortSignal): Promise<ExecutionView[]> {
  return client.request({
    path: '/v1/executions',
    signal,
    parse: (value) => {
      if (!Array.isArray(value)) throw invalidResponseError()
      return value.map(parseExecutionView)
    },
  })
}

export function controlExecution(
  client: ApiClient,
  executionId: string,
  input: ControlExecutionInput,
  intent = client.createMutationIntent(),
): Promise<ExecutionCommandReceipt> {
  return client.request({
    path: `/v1/executions/${encodeURIComponent(executionId)}/control`,
    method: 'POST',
    body: {
      action: input.action,
      expected_state_version: input.expected_state_version,
      reason: input.reason ?? 'user_requested',
    },
    intent,
    parse: parseExecutionReceipt,
  })
}

export function provideExecutionInput(
  client: ApiClient,
  executionId: string,
  input: ExecutionInput,
  intent = client.createMutationIntent(),
): Promise<ExecutionCommandReceipt> {
  return client.request({
    path: `/v1/executions/${encodeURIComponent(executionId)}/input`,
    method: 'POST',
    body: input,
    intent,
    parse: parseExecutionReceipt,
  })
}

export function parseExecutionView(value: unknown): ExecutionView {
  const item = record(value)
  const state = string(item.state)
  const result = item.result === null ? null : parseResult(item.result)
  const failure = item.failure === null ? null : parseFailure(item.failure)
  if (!stateSet.has(state)) throw invalidResponseError()

  return {
    execution_id: string(item.execution_id),
    agent_id: string(item.agent_id),
    state: state as ExecutionState,
    state_version: positiveInteger(item.state_version),
    parent_execution_id: nullableString(item.parent_execution_id),
    created_at: string(item.created_at),
    updated_at: string(item.updated_at),
    finished_at: nullableString(item.finished_at),
    result,
    failure,
  }
}

function parseExecutionReceipt(value: unknown): ExecutionCommandReceipt {
  const item = record(value)
  return {
    outcome: string(item.outcome),
    execution_id: string(item.execution_id),
    state_version: positiveInteger(item.state_version),
  }
}

function parseResult(value: unknown): ExecutionView['result'] {
  const item = record(value)
  const result: NonNullable<ExecutionView['result']> = { result_ref: string(item.result_ref) }
  if (item.display_text !== undefined) result.display_text = string(item.display_text)
  return result
}

function parseFailure(value: unknown): ExecutionView['failure'] {
  return { code: string(record(value).code) }
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}

function string(value: unknown): string {
  if (typeof value !== 'string' || value.length === 0) throw invalidResponseError()
  return value
}

function nullableString(value: unknown): string | null {
  return value === null ? null : string(value)
}

function positiveInteger(value: unknown): number {
  if (!Number.isInteger(value) || (value as number) < 1) throw invalidResponseError()
  return value as number
}

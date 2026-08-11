import { ApiClient } from './client'
import { invalidResponseError } from './errors'
import type { ClientEvent, OpenStreamResponse, ReadStreamResponse } from './types'

export function openEventStream(client: ApiClient, executionIds: string[], signal?: AbortSignal): Promise<OpenStreamResponse> {
  if (executionIds.length === 0 || new Set(executionIds).size !== executionIds.length) throw new TypeError('executionIds must be non-empty and unique')
  return client.request({
    path: '/v1/events/streams',
    method: 'POST',
    body: { execution_ids: executionIds },
    signal,
    parse: parseOpenStream,
  })
}

export function readEventStream(
  client: ApiClient,
  streamId: string,
  cursor: string,
  maximumEvents = 100,
  signal?: AbortSignal,
): Promise<ReadStreamResponse> {
  return client.request({
    path: `/v1/events/streams/${encodeURIComponent(streamId)}/read`,
    method: 'POST',
    body: { cursor, maximum_events: maximumEvents },
    signal,
    parse: parseReadStream,
  })
}

function parseOpenStream(value: unknown): OpenStreamResponse {
  const item = record(value)
  return {
    stream_id: string(item.stream_id),
    cursor: string(item.cursor),
    stream_binding_digest: string(item.stream_binding_digest),
    revocation_epoch: nonNegativeInteger(item.revocation_epoch),
  }
}

function parseReadStream(value: unknown): ReadStreamResponse {
  const item = record(value)
  if (!Array.isArray(item.events)) throw invalidResponseError()
  return { events: item.events.map(parseClientEvent), cursor: string(item.cursor) }
}

function parseClientEvent(value: unknown): ClientEvent {
  const item = record(value)
  return {
    event_id: string(item.event_id),
    event_type: string(item.event_type),
    execution_id: string(item.execution_id),
    sequence: positiveInteger(item.sequence),
    occurred_at: string(item.occurred_at),
    payload: { ...record(item.payload) },
  }
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}

function string(value: unknown): string {
  if (typeof value !== 'string' || value.length === 0) throw invalidResponseError()
  return value
}

function positiveInteger(value: unknown): number {
  if (!Number.isInteger(value) || (value as number) < 1) throw invalidResponseError()
  return value as number
}

function nonNegativeInteger(value: unknown): number {
  if (!Number.isInteger(value) || (value as number) < 0) throw invalidResponseError()
  return value as number
}

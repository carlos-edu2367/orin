# Frontend ↔ backend contract

The client talks to the API over JSON on the same origin, plus one long-lived SSE
stream per open conversation. This document is the contract those two surfaces
share.

## Authentication

In the local profile (`LOCALHOST_TRUST_ENABLED=true`) there is no credential:
the gateway verifies that the TCP peer is a loopback address and rejects
everything else. Requests must send **no** `Authorization` header and no session
cookie — supplying one is treated as a misconfiguration and refused.

Mutating requests must carry an `Idempotency-Key` header. Retrying a create with
the same key resolves to the record already committed rather than creating a
second one.

## Conversations

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/conversations` | Create a conversation and queue its first turn. |
| `GET` | `/v1/conversations` | List conversations, newest first. |
| `GET` | `/v1/conversations/{id}` | Full durable snapshot: messages, turns, activity, cursor. |
| `POST` | `/v1/conversations/{id}/messages` | Queue another turn in an existing conversation. |
| `POST` | `/v1/conversations/{id}/cancel` | Ask the worker to stop every live turn. |
| `GET` | `/v1/conversations/{id}/overview` | Aggregated agents, tools, agent messages, errors, timing. |
| `GET` | `/v1/conversations/{id}/events?after={cursor}` | SSE activity stream. |

`POST /v1/conversations` answers `201` with:

```json
{ "conversation_id": "chat_…", "title": "…", "turn_id": "turn_…",
  "message_id": "msg_…", "state": "queued" }
```

The response never contains the prompt reference, the provider credential, or
any internal agent identifier.

### Snapshot

`GET /v1/conversations/{id}` returns messages (`user` / `assistant` with
`status` and `retryable`), turns, an `activities` array and an `activity_cursor`.

The snapshot **omits `assistant.delta` events**: the assistant message already
carries that text, and replaying thousands of token events would only make a
reopened conversation slower. The cursor still advances past them, so attaching
the stream at `activity_cursor` resumes at the true end of the log.

### Terminal states

A turn ends in exactly one of `completed`, `failed`, `cancelled`. `cancelled` is
distinct from `failed` on purpose: the UI must not offer a retry for something
the user chose to stop. A turn that already reached a terminal state is never
rewritten by a late projection.

## Activity events

Every observable fact is one event in an append-only per-conversation log.

```json
{
  "event_id": "activity:turn_…:7",
  "event_type": "tool.finished",
  "sequence": 7,
  "summary": "Escreveu relatorio.md",
  "payload": { "tool_name": "write_file", "tool_kind": "filesystem",
               "status": "succeeded", "label": "relatorio.md",
               "invocation_id": "call_…", "error_code": null },
  "occurred_at": "2026-08-10T22:14:03.221+00:00",
  "turn_id": "turn_…", "execution_id": "exe_…",
  "agent_id": "agent:chat_…:main", "parent_agent_id": null,
  "cursor": "a.eyJ…"
}
```

| `event_type` | Meaning |
| --- | --- |
| `turn.started` | The turn was queued, started, or resumed after a retry. |
| `tool.requested` | The model asked for one or more tools. |
| `tool.started` / `tool.finished` | One tool call; `status` on the finished event is `succeeded`, `failed` or `cancelled`. |
| `assistant.delta` | A chunk of assistant text (`payload.content`, `payload.message_id`). Live stream only. |
| `agent.created` | A subagent now exists; `agent_id` is the subagent, `parent_agent_id` its creator. |
| `agent.message_sent` / `agent.message_received` | A message crossing between agents; `payload.content` is a bounded preview. |
| `delegation.failed` | A subagent did not finish its task. |
| `turn.completed` / `turn.failed` | Terminal. `payload.error_code` is `TURN_CANCELLED` for a user stop. |

Payloads are redacted and size-bounded when the event is constructed, so a client
may render any field it recognizes without further filtering.

**Clients must tolerate unknown event types and unknown payload keys.** The
reference client renders an unrecognized type as a plain lifecycle line. Failing
closed on an unknown field would turn a harmless backend addition into a blank
chat.

## Streaming

`GET /v1/conversations/{id}/events?after={cursor}` is a long-lived
`text/event-stream`. It emits activity events as the worker produces them, a
`heartbeat` event carrying the current cursor while idle, and closes after a
bounded lifetime so a client that vanished cannot hold a connection forever.

```
id: activity:turn_…:7
event: tool.finished
data: {"event_id":"activity:turn_…:7", …}

event: heartbeat
data: {"cursor":"a.eyJ…"}
```

Start at `after=0` for the whole log, or at the snapshot's `activity_cursor` to
resume. Reconnect with the last cursor you saw.

### Cursors and resync

Cursors are opaque and HMAC-signed against the conversation and user. A forged,
truncated, or foreign cursor yields `409` with code `cursor_invalid`, or a
`resync` event on an open stream. The client must then drop its cursor, refetch
the snapshot, and reattach at `0`.

The signing key comes from `AGENTOS_ACTIVITY_CURSOR_SECRET`; when it is unset,
both the API and the worker derive the same stable value from the database URL,
so cursors survive a restart.

## Providers

| Method | Path | Purpose |
| --- | --- | --- |
| `PUT` | `/v1/providers/{provider}` | Store a credential (encrypted at rest). |
| `GET` | `/v1/providers/{provider}` | Whether it is enabled and when its catalog was refreshed. |
| `DELETE` | `/v1/providers/{provider}` | Revoke. |
| `POST` | `/v1/providers/{provider}/models:refresh` | Refresh the model catalog. |
| `GET` | `/v1/providers/{provider}/models` | Authorized models. |
| `PUT`/`DELETE` | `/v1/providers/{provider}/favorites/{model_id}` | Favourite a model. |

An API key is never returned by any endpoint, and never appears in an event, log,
metric or error body.

## Errors

```json
{ "error": { "code": "resource_not_found", "category": "NOT_FOUND",
             "message_key": "resource_not_found", "correlation_id": "corr_…",
             "retryable": false, "retry_after": null } }
```

`category` is one of `VALIDATION`, `AUTHENTICATION`, `AUTHORIZATION`,
`RATE_LIMITED`, `CONFLICT`, `INDETERMINATE`, `NOT_FOUND`, `PROVIDER`,
`INTERNAL`. `retryable` says whether repeating the request can succeed;
`retry_after` is seconds when the server can be specific.

Messages shown to a person should be written by the client from `code`; the
server's strings are keys, not copy.

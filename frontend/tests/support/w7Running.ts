/** A running Code turn waiting on a permission for a file edit (W7 browser tests). */
import { conversation } from './w7Chat'

export const RUNNING_TURN_ID = 't2'

export function runningOverride(permission?: { requestId: string; toolName: string; path?: string; command?: string; rule?: string }) {
  const activities = permission ? [{
    event_id: 'ev-perm', cursor: '2', turn_id: RUNNING_TURN_ID, agent_id: 'agent:main', event_type: 'code_mode.decision_required',
    summary: 'Permissão necessária', occurred_at: '2026-10-20T12:01:02.000Z',
    payload: {
      stage: 'waiting_decision', state: 'waiting_user', tool_name: permission.toolName, permission_request_id: permission.requestId,
      ...(permission.rule ? { suggested_rule: permission.rule, rule_suggestion: permission.rule } : {}), permission_mode: 'ask_permissions',
      ...(permission.command ? { command: permission.command } : {}), ...(permission.path ? { path: permission.path } : {}),
      permission_request: {
        request_id: permission.requestId, status: 'pending', tool_name: permission.toolName, permission_mode: 'ask_permissions', turn_id: RUNNING_TURN_ID,
        ...(permission.command ? { command: permission.command } : {}), ...(permission.path ? { path: permission.path } : {}),
        ...(permission.rule ? { suggested_rule: permission.rule, rule_suggestion: permission.rule } : {}),
        structured_patch: permission.path ? [{ oldStart: 1, oldLines: 1, newStart: 1, newLines: 1, lines: ['-TAXA = 0.02', '+TAXA = 0.025'] }] : undefined,
        added_lines: permission.path ? 1 : undefined, removed_lines: permission.path ? 1 : undefined,
      },
    },
  }] : []
  return {
    state: 'running',
    messages: [
      ...conversation.messages,
      { message_id: 'm-user-2', turn_id: RUNNING_TURN_ID, role: 'user', content: 'Ajuste a taxa de cobrança para 2,5%.', status: 'completed', retryable: false, attachments: [], command: null },
      { message_id: 'm-asst-2', turn_id: RUNNING_TURN_ID, role: 'assistant', content: '', status: 'streaming', retryable: false, attachments: [], command: null },
    ],
    turns: [...conversation.turns, { turn_id: RUNNING_TURN_ID, state: 'running', created_at: '2026-10-20T12:01:00.000Z', started_at: '2026-10-20T12:01:01.000Z', finished_at: null, scheduled_by_schedule_id: null, code_mode: 'code' }],
    activities, activity_cursor: '2',
  }
}

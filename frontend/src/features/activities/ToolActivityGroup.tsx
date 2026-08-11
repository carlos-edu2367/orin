import type { ClientEvent } from '../../api/types'
import { deriveToolActivityDetail } from './activityNormalizer'
import type { Activity } from './activityTypes'
import { ActivityGroup } from './ActivityGroup'

const toolStateLabel: Record<string, string> = {
  requested: 'Solicitada',
  running: 'Em execução',
  succeeded: 'Concluída',
  failed: 'Falhou',
  cancelled: 'Cancelada',
}

type ToolActivityGroupProps = {
  activity: Activity
  events: ClientEvent[]
}

/**
 * Renders one Tool invocation, grouped by invocation_id, using only the sanitized
 * fields ToolActivityView documents. Progress is shown as observed ticks, never as
 * an invented percentage; arguments/output/tokens are never read, let alone shown.
 */
export function ToolActivityGroup({ activity, events }: ToolActivityGroupProps) {
  const detail = deriveToolActivityDetail(events)
  const progressTicks = events.filter((event) => event.event_type === 'ToolProgressed').length
  const stateLabel = toolStateLabel[activity.state] ?? activity.state
  const summary = detail.toolKind ? `${stateLabel} · ${detail.toolKind}` : stateLabel

  return (
    <ActivityGroup activity={activity} summary={summary}>
      <dl className="activity-group__detail">
        <div>
          <dt>Estado</dt>
          <dd>{stateLabel}</dd>
        </div>
        {detail.toolKind && (
          <div>
            <dt>Tipo</dt>
            <dd>{detail.toolKind}</dd>
          </div>
        )}
        {progressTicks > 0 && (
          <div>
            <dt>Progresso observado</dt>
            <dd>{progressTicks} {progressTicks === 1 ? 'atualização' : 'atualizações'}</dd>
          </div>
        )}
        {detail.errorCode && (
          <div>
            <dt>Código de falha</dt>
            <dd>{detail.errorCode}</dd>
          </div>
        )}
        {detail.summary && (
          <div>
            <dt>Resultado</dt>
            <dd>{detail.summary}</dd>
          </div>
        )}
      </dl>
    </ActivityGroup>
  )
}

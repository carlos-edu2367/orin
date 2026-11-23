import { Link } from 'react-router-dom'
import type { PipelineList as PipelineListData, PipelineRunSummaryRow, PipelineSource, PipelineSummary } from '../../api/pipelines'
import { formatCost, formatTokens, RUN_STATE_LABEL, SOURCE_LABEL, toneOf } from './format'
import './pipelines.css'

const SOURCE_ORDER: readonly PipelineSource[] = ['builtin', 'user', 'project']
const SOURCE_NAME_ONE: Record<PipelineSource, string> = { builtin: 'pronto', user: 'seu', project: 'do projeto' }

function PipelineRow({ pipeline, selected, onSelect }: { pipeline: PipelineSummary; selected: boolean; onSelect: (id: string) => void }) {
  const needsApproval = pipeline.approvalRequired && !pipeline.approved
  return (
    <li className="pipeline-list__item" data-testid={`pipeline-row-${pipeline.id}`} data-valid={pipeline.valid}>
      <button type="button" className="pipeline-list__button" aria-pressed={selected} onClick={() => onSelect(pipeline.id)}>
        <span className="pipeline-list__name">{pipeline.name}</span>
        <code className="pipeline-list__id">{pipeline.id}</code>
        {pipeline.description && <span className="pipeline-list__desc">{pipeline.description}</span>}
        <span className="pipeline-list__chips">
          {!pipeline.valid && <span className="pipeline-badge pipeline-badge--bad">Com erros</span>}
          {pipeline.valid && needsApproval && <span className="pipeline-badge pipeline-badge--warn">Aprovação necessária</span>}
          {pipeline.valid && pipeline.approvalRequired && pipeline.approved && <span className="pipeline-badge pipeline-badge--ok">Aprovado</span>}
          {pipeline.steps.length > 0 && <span className="pipeline-badge pipeline-badge--muted">{pipeline.steps.length} {pipeline.steps.length === 1 ? 'etapa' : 'etapas'}</span>}
          {pipeline.gateCommands > 0 && <span className="pipeline-badge pipeline-badge--muted">{pipeline.gateCommands} {pipeline.gateCommands === 1 ? 'comando' : 'comandos'}</span>}
        </span>
      </button>
      {pipeline.errors.length > 0 && (
        <ul className="pipeline-list__errors" aria-label={`Erros de ${pipeline.id}`}>
          {pipeline.errors.slice(0, 4).map((issue, index) => (
            <li key={`${issue.code}-${index}`}>
              {issue.line ? <span className="pipeline-list__line">linha {issue.line}</span> : null}
              {issue.message}
            </li>
          ))}
          {pipeline.errors.length > 4 && <li className="pipeline-muted">e mais {pipeline.errors.length - 4}</li>}
        </ul>
      )}
    </li>
  )
}

export function PipelineList({
  list,
  selectedId,
  onSelect,
  runs,
  conversationId,
}: {
  list: PipelineListData
  selectedId: string | null
  onSelect: (id: string) => void
  runs: PipelineRunSummaryRow[]
  conversationId: string | null
}) {
  const trustLink = conversationId ? `/settings/trusted-folders?conversation=${encodeURIComponent(conversationId)}` : '/settings/trusted-folders'
  return (
    <div className="pipeline-list" data-testid="pipeline-list">
      {!list.enabled && (
        <div className="pipeline-note pipeline-note--warn" role="status" data-testid="pipelines-disabled">
          <strong>Pipelines desligados.</strong> Nada aparece nem roda enquanto a chave estiver desligada.{' '}
          <Link className="pipeline-link" to="/settings/orchestration">Ligar em Configurações &gt; Orquestração</Link>
        </div>
      )}

      {list.enabled && SOURCE_ORDER.map((source) => {
        const items = list.pipelines.filter((pipeline) => pipeline.source === source)
        if (items.length === 0) return null
        return (
          <section key={source} className="pipeline-list__group" aria-label={SOURCE_LABEL[source]}>
            <h3>{SOURCE_LABEL[source]}</h3>
            <ul>{items.map((pipeline) => <PipelineRow key={`${source}-${pipeline.id}`} pipeline={pipeline} selected={selectedId === pipeline.id} onSelect={onSelect} />)}</ul>
          </section>
        )
      })}

      {list.enabled && list.pipelines.length === 0 && <p className="pipeline-muted">Nenhum pipeline encontrado.</p>}

      {list.enabled && list.projectSkipped > 0 && (
        <p className="pipeline-note pipeline-note--warn" role="note" data-testid="project-skipped">
          {list.projectSkipped} {list.projectSkipped === 1 ? 'pipeline do projeto ignorado' : 'pipelines do projeto ignorados'}: pasta não confiável.{' '}
          <Link className="pipeline-link" to={trustLink}>Confiar nesta pasta</Link>
        </p>
      )}

      {list.enabled && list.shadowed.length > 0 && (
        <section className="pipeline-list__group" aria-label="Sombreados" data-testid="pipeline-shadowed">
          <h3>Sombreados</h3>
          <p className="pipeline-muted">Quando o id se repete, vale o do projeto, depois o seu, depois o pronto.</p>
          <ul className="pipeline-list__shadowed">
            {list.shadowed.map((item) => (
              <li key={`${item.source}-${item.id}`}><code>{item.id}</code> <span className="pipeline-muted">({SOURCE_NAME_ONE[item.source]}) ignorado: outro com o mesmo id tem precedência</span></li>
            ))}
          </ul>
        </section>
      )}

      {runs.length > 0 && (
        <section className="pipeline-list__group" aria-label="Execuções recentes" data-testid="recent-runs">
          <h3>Execuções recentes</h3>
          <ul className="pipeline-runs">
            {runs.slice(0, 8).map((run) => (
              <li key={run.runId}>
                <Link className="pipeline-runs__link" to={`/pipelines/runs/${encodeURIComponent(run.runId)}`}>
                  <code>{run.pipelineId}</code>
                  <span className={`pipeline-badge pipeline-badge--${toneOf(run.state)}`}>{RUN_STATE_LABEL[run.state] ?? run.state}</span>
                  <span className="pipeline-muted">{formatTokens(run.usedTokens)} tokens · {formatCost(run.usedCostUsd)}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

import { useState } from 'react'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import { approvePipeline, revokePipeline, type PipelineDetail as PipelineDetailData } from '../../api/pipelines'
import { SOURCE_LABEL } from './format'
import { ReviewBlock } from './PipelineRunForm'
import './pipelines.css'

/** The definition tab: the YAML as stored, what the file would run (commands), and the approval of a project pipeline. */
export function PipelineDefinition({
  client,
  pipeline,
  conversationId,
  onChanged,
}: {
  client: ApiClient
  pipeline: PipelineDetailData
  conversationId: string | null
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const lines = pipeline.text.split('\n')

  async function change(action: 'approve' | 'revoke') {
    setBusy(true)
    setError(null)
    try {
      await (action === 'approve' ? approvePipeline : revokePipeline)(client, pipeline.id, pipeline.definitionHash, conversationId)
      onChanged()
    } catch (caught: unknown) {
      setError(caught instanceof ApiError && caught.code === 'PIPELINE_CHANGED'
        ? 'O arquivo mudou depois que você revisou os comandos. Recarregue e revise de novo.'
        : caught instanceof ApiError && caught.serverMessage ? caught.serverMessage : 'Não foi possível alterar a aprovação.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="pipeline-definition" data-testid="pipeline-definition">
      <p className="pipeline-muted">
        {SOURCE_LABEL[pipeline.source]} · <code>{pipeline.definitionHash.slice(0, 12)}</code>
      </p>

      {pipeline.errors.length > 0 && (
        <ul className="pipeline-issues" aria-label="Erros do arquivo">
          {pipeline.errors.map((issue, index) => (
            <li key={`${issue.code}-${index}`} className="pipeline-issues__item pipeline-issues__item--bad">
              <span className="pipeline-issues__go">{issue.line ? `linha ${issue.line}` : 'arquivo'}</span>
              <span>{issue.message}</span><code>{issue.code}</code>
            </li>
          ))}
        </ul>
      )}
      {[...pipeline.contextErrors, ...pipeline.contextWarnings].length > 0 && (
        <ul className="pipeline-issues" aria-label="Avisos de contexto">
          {pipeline.contextErrors.map((issue, index) => (
            <li key={`e-${issue.code}-${index}`} className="pipeline-issues__item pipeline-issues__item--bad"><span className="pipeline-issues__go">{issue.line ? `linha ${issue.line}` : 'contexto'}</span><span>{issue.message}</span></li>
          ))}
          {pipeline.contextWarnings.map((issue, index) => (
            <li key={`w-${issue.code}-${index}`} className="pipeline-issues__item pipeline-issues__item--warn"><span className="pipeline-issues__go">{issue.line ? `linha ${issue.line}` : 'aviso'}</span><span>{issue.message}</span></li>
          ))}
        </ul>
      )}

      {pipeline.review && (
        <section className="pipeline-approval" aria-label="Comandos que o pipeline roda">
          <h3>Comandos que o pipeline roda</h3>
          <ReviewBlock review={pipeline.review} />
          {pipeline.approvalRequired && (
            <div className="pipeline-form__actions">
              {pipeline.approved ? (
                <>
                  <span className="pipeline-badge pipeline-badge--ok">Aprovado para este arquivo</span>
                  <button type="button" className="button button--secondary pipelines-btn" disabled={busy} onClick={() => void change('revoke')}>Revogar aprovação</button>
                </>
              ) : (
                <button type="button" className="button button--secondary pipelines-btn" disabled={busy || !pipeline.valid} onClick={() => void change('approve')}>
                  Aprovar estes comandos
                </button>
              )}
            </div>
          )}
        </section>
      )}
      {error && <p className="pipeline-note pipeline-note--bad" role="alert">{error}</p>}

      <section aria-label="Texto do pipeline">
        <h3>Definição (YAML)</h3>
        <pre className="pipeline-yaml" tabIndex={0} aria-label="YAML do pipeline" data-testid="pipeline-yaml">
          {lines.map((line, index) => (
            <span key={index} className="pipeline-yaml__line"><span className="pipeline-yaml__n" aria-hidden="true">{index + 1}</span>{line}{'\n'}</span>
          ))}
        </pre>
      </section>
    </div>
  )
}

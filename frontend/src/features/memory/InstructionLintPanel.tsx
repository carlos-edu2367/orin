import { useCallback, useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { runMemoryInstructionLint, type LintReport } from '../../api/memoryLint'

export type InstructionLintPanelProps = {
  client?: ApiClient
  projectId?: string
  workspaceRoot?: string
}

export function InstructionLintPanel({
  client: providedClient,
  projectId,
  workspaceRoot,
}: InstructionLintPanelProps) {
  const client = useMemo(() => providedClient ?? createBrowserApiClient(), [providedClient])

  const requestKey = `${projectId ?? ''}::${workspaceRoot ?? ''}`
  const [estimate, setEstimate] = useState<LintReport | null>(null)
  const [estimateLoadedKey, setEstimateLoadedKey] = useState<string | null>(null)
  const [report, setReport] = useState<LintReport | null>(null)
  const [running, setRunning] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const estimateLoading = estimateLoadedKey !== requestKey

  useEffect(() => {
    let active = true

    runMemoryInstructionLint(client, {
      projectId,
      workspaceRoot,
      dryRun: true,
    })
      .then((result) => {
        if (!active) return
        setEstimate(result)
        setErrorMessage(null)
      })
      .catch(() => {
        if (!active) return
        setEstimate(null)
      })
      .finally(() => {
        if (active) setEstimateLoadedKey(requestKey)
      })

    return () => {
      active = false
    }
  }, [client, projectId, workspaceRoot, requestKey])

  const handleRunLint = useCallback(() => {
    setRunning(true)
    setErrorMessage(null)

    runMemoryInstructionLint(client, {
      projectId,
      workspaceRoot,
      dryRun: false,
    })
      .then((result) => {
        setReport(result)
        setEstimate(result)
      })
      .catch(() => {
        setErrorMessage('Não foi possível verificar contradições entre memórias e arquivos de instrução.')
      })
      .finally(() => {
        setRunning(false)
      })
  }, [client, projectId, workspaceRoot])

  const estimatedCalls = estimate?.estimated_calls ?? 0
  const activeWarning = report?.warning ?? estimate?.warning ?? null

  return (
    <section
      className="instruction-lint-panel"
      data-testid="instruction-lint-panel"
      aria-label="Verificação de instruções"
      style={{
        border: '1px solid var(--border-subtle, rgba(255, 255, 255, 0.12))',
        borderRadius: '10px',
        padding: '12px 16px',
        marginBottom: '16px',
        background: 'var(--surface-elevated, rgba(255, 255, 255, 0.03))',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '12px',
        }}
      >
        <div>
          <h2
            style={{
              margin: 0,
              fontSize: '0.95rem',
              fontWeight: 600,
            }}
          >
            Conferir memórias contra arquivos de instrução
          </h2>
          <p
            data-testid="instruction-lint-estimate"
            style={{
              margin: '4px 0 0',
              fontSize: '0.85rem',
              opacity: 0.8,
            }}
          >
            {estimateLoading
              ? 'Calculando estimativa...'
              : `Estimativa: ${estimatedCalls} chamada(s) de modelo`}
            {estimate && !estimateLoading && estimate.files_checked.length > 0
              ? ` (${estimate.files_checked.length} arquivo(s), ${estimate.memories_checked} memória(s) relevante(s))`
              : ''}
          </p>
        </div>

        <button
          type="button"
          className="memory-ref11__add-btn"
          data-testid="instruction-lint-run-button"
          onClick={handleRunLint}
          disabled={running}
        >
          {running ? 'Verificando...' : 'Verificar agora'}
        </button>
      </div>

      {errorMessage && (
        <p
          role="alert"
          data-testid="instruction-lint-error"
          style={{ margin: '10px 0 0', fontSize: '0.85rem' }}
        >
          {errorMessage}
        </p>
      )}

      {activeWarning && !errorMessage && (
        <p
          role="status"
          data-testid="instruction-lint-warning"
          style={{ margin: '10px 0 0', fontSize: '0.85rem', opacity: 0.9 }}
        >
          {activeWarning}
        </p>
      )}

      {report && !report.dry_run && !activeWarning && report.contradictions.length === 0 && (
        <p
          role="status"
          data-testid="instruction-lint-empty"
          style={{ margin: '10px 0 0', fontSize: '0.85rem', opacity: 0.85 }}
        >
          Nenhuma contradição encontrada entre memórias ativas e arquivos de instrução.
        </p>
      )}

      {report && report.contradictions.length > 0 && (
        <div data-testid="instruction-lint-results" style={{ marginTop: '12px' }}>
          <p style={{ margin: '0 0 8px', fontSize: '0.82rem', opacity: 0.8 }}>
            {report.contradictions.length} contradição(ões) encontrada(s). Nenhuma memória ou arquivo foi alterado automaticamente.
          </p>
          <ul
            style={{
              listStyle: 'none',
              padding: 0,
              margin: 0,
              display: 'flex',
              flexDirection: 'column',
              gap: '10px',
            }}
          >
            {report.contradictions.map((item) => (
              <li
                key={`${item.memory_id}-${item.file}-${item.line}`}
                data-testid="instruction-lint-item"
                style={{
                  border: '1px solid var(--border-subtle, rgba(255, 255, 255, 0.14))',
                  borderRadius: '8px',
                  padding: '10px 12px',
                  fontSize: '0.85rem',
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: '4px' }}>
                  <code>{`${item.file}:${item.line}`}</code>
                </div>
                <div style={{ marginBottom: '4px' }}>
                  <strong>Instrução:</strong> <span>{item.quoted_line}</span>
                </div>
                <div style={{ marginBottom: '4px' }}>
                  <strong>Memória:</strong> <span>{item.memory_fact}</span>
                </div>
                <div>
                  <strong>Motivo:</strong> <span>{item.reason}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

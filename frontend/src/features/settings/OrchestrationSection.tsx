import { useCallback, useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import {
  getOrchestrationSettings,
  updateOrchestrationSettings,
  type OrchestrationSettingsReport,
} from '../../api/orchestrationSettings'
import { Card, describeError, ErrorNote, FolderBanner, useFolderConversation } from '../codeSettings/shared'
import { AgentMessagesSettingsBlock } from './AgentMessagesSettingsBlock'
import { BackgroundSettingsBlock } from './BackgroundSettingsBlock'
import { FriendDeliverySettingsBlock } from './FriendDeliverySettingsBlock'
import { OrchestrationPipelinesSettings, isPipelineSettingPath } from '../pipelines/OrchestrationPipelinesSettings'
import { SettingsSection } from './SettingsSection'
import { TaskBoardSettingsBlock } from './TaskBoardSettingsBlock'
import { VerifierMetricsTable } from './VerifierMetricsTable'
import { VerifierSettingsBlocks } from './VerifierSettingsBlocks'
import { WorktreeSettingsBlock } from './WorktreeSettingsBlock'
import './orchestrationSettings.css'

const GROUPS_QUERY = 'messages,friend,verifier,worktree,task_board,background,pipelines,budget,goal'

export function OrchestrationSection({ client }: { client?: ApiClient }) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const conversationId = useFolderConversation()
  const [report, setReport] = useState<OrchestrationSettingsReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<ReturnType<typeof describeError> | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getOrchestrationSettings(apiClient, {
      conversationId,
      group: GROUPS_QUERY,
    })
      .then((next) => {
        if (!cancelled) {
          setReport(next)
          setError(null)
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(describeError(caught, 'carregar as configurações de orquestração'))
          setReport(null)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [apiClient, conversationId])

  const handleSave = useCallback(
    async (changes: Record<string, unknown>) => {
      setSaving(true)
      setError(null)
      setNotice(null)
      try {
        const next = await updateOrchestrationSettings(apiClient, changes, {
          conversationId,
          group: GROUPS_QUERY,
        })
        setReport(next)
        setNotice('Configuração salva na camada do usuário.')
      } catch (caught: unknown) {
        setError(describeError(caught, 'salvar a configuração'))
      } finally {
        setSaving(false)
      }
    },
    [apiClient, conversationId],
  )

  const verifierItems = useMemo(
    () => (report?.items ?? []).filter((item) => item.group === 'verifier'),
    [report],
  )
  const messageItems = useMemo(
    () => (report?.items ?? []).filter((item) => item.group === 'messages'),
    [report],
  )
  const friendItems = useMemo(
    () => (report?.items ?? []).filter((item) => item.group === 'friend'),
    [report],
  )
  const worktreeItems = useMemo(
    () => (report?.items ?? []).filter((item) => item.group === 'worktree'),
    [report],
  )
  const taskBoardItems = useMemo(
    () => (report?.items ?? []).filter((item) => item.group === 'task_board'),
    [report],
  )
  const backgroundItems = useMemo(
    () => (report?.items ?? []).filter((item) => item.group === 'background'),
    [report],
  )
  const pipelineItems = useMemo(
    () => (report?.items ?? []).filter((item) => isPipelineSettingPath(item.path)),
    [report],
  )

  return (
    <SettingsSection eyebrow="INTELIGÊNCIA / ORQUESTRAÇÃO" title="Orquestração">
      <FolderBanner
        projectRoot={report?.projectRoot ?? null}
        trusted={report?.trusted}
        conversationId={conversationId}
      />
      <ErrorNote error={error} />
      {notice && (
        <p role="status" className="orch-settings__status">
          {notice}
        </p>
      )}
      {loading && !report && <p className="code-settings__muted">Carregando configurações…</p>}

      {report && (
        <div className="orch-settings__slots">
          {verifierItems.length > 0 && (
            <div data-testid="orchestration-section-verifier">
              <VerifierSettingsBlocks
                items={verifierItems}
                busy={saving}
                onSave={handleSave}
              />
            </div>
          )}

          {verifierItems.length > 0 && (
            <div data-testid="orchestration-section-verifier-metrics">
              <Card title="Métricas do verificador" id="orchestration-heading-verifier-metrics">
                <p className="code-settings__muted">
                  Acompanhe o desempenho por par autor → revisor para calibrar a tabela de famílias e níveis.
                </p>
                <VerifierMetricsTable client={apiClient} />
              </Card>
            </div>
          )}

          <div data-testid="orchestration-section-messages">
            <Card title="Mensagens a subagentes" id="orchestration-heading-messages">
              <p className="code-settings__muted">
                Controle se o agente pai pode orientar subagentes em andamento e se um subagente continua quando chegam mensagens não lidas no fim do turno.
              </p>
              <AgentMessagesSettingsBlock
                items={messageItems}
                busy={saving}
                onSave={handleSave}
              />
            </Card>
          </div>

          <div data-testid="orchestration-section-friend">
            <Card title="Friend" id="orchestration-heading-friend">
              <p className="code-settings__muted">
                Mensagens para sessões em andamento enviadas pelo Orin Friend.
              </p>
              <FriendDeliverySettingsBlock
                items={friendItems}
                busy={saving}
                onSave={handleSave}
              />
            </Card>
          </div>

          {worktreeItems.length > 0 && (
            <div data-testid="orchestration-section-worktree">
              <Card title="Worktree por subagente" id="orchestration-heading-worktree">
                <p className="code-settings__muted">
                  Isola subagentes configurados com <code>isolation: worktree</code> em worktrees Git próprias e integra as alterações pela fila serializada.
                </p>
                <WorktreeSettingsBlock
                  items={worktreeItems}
                  busy={saving}
                  trusted={report.trusted}
                  projectRoot={report.projectRoot}
                  onSave={handleSave}
                />
              </Card>
            </div>
          )}

          {taskBoardItems.length > 0 && (
            <div data-testid="orchestration-section-task-board">
              <Card title="Quadro de tarefas compartilhado" id="orchestration-heading-task-board">
                <p className="code-settings__muted">
                  Permite coordenar tarefas concorrentes entre subagentes com reivindicação atômica (<code>TaskClaim</code>) e dependências.
                </p>
                <TaskBoardSettingsBlock
                  items={taskBoardItems}
                  busy={saving}
                  onSave={handleSave}
                />
              </Card>
            </div>
          )}

          {pipelineItems.length > 0 && (
            <div data-testid="orchestration-section-pipelines">
              <Card title="Pipelines" id="orchestration-heading-pipelines">
                <p className="code-settings__muted">
                  Fluxos declarativos de agentes e o <code>/goal</code>. Nada disso roda sem um comando seu; aqui ficam as chaves, os tetos de gasto e a ferramenta que deixa o agente iniciar pipelines aprovados.
                </p>
                <OrchestrationPipelinesSettings
                  items={pipelineItems}
                  busy={saving}
                  onSave={handleSave}
                />
              </Card>
            </div>
          )}

          {backgroundItems.length > 0 && (
            <div data-testid="orchestration-section-background">
              <Card title="Segundo plano e travamento" id="orchestration-heading-background">
                <p className="code-settings__muted">
                  Configure a retomada automática após reinício e os limites para detectar ou cancelar subagentes travados.
                </p>
                <BackgroundSettingsBlock
                  items={backgroundItems}
                  busy={saving}
                  onSave={handleSave}
                />
              </Card>
            </div>
          )}
        </div>
      )}
    </SettingsSection>
  )
}

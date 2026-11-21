import { useCallback, useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import {
  getOrchestrationSettings,
  updateOrchestrationSettings,
  type OrchestrationSettingsReport,
} from '../../api/orchestrationSettings'
import { Card, describeError, ErrorNote, FolderBanner, useFolderConversation } from '../codeSettings/shared'
import { AgentMessagesSettingsBlock } from './AgentMessagesSettingsBlock'
import { FriendDeliverySettingsBlock } from './FriendDeliverySettingsBlock'
import { SettingsSection } from './SettingsSection'
import './orchestrationSettings.css'

const GROUPS_QUERY = 'messages,friend'

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

  const messageItems = useMemo(
    () => (report?.items ?? []).filter((item) => item.group === 'messages'),
    [report],
  )
  const friendItems = useMemo(
    () => (report?.items ?? []).filter((item) => item.group === 'friend'),
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
        </div>
      )}
    </SettingsSection>
  )
}

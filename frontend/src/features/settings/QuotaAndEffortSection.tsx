import { useCallback, useEffect, useMemo, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import {
  getOrchestrationSettings,
  updateOrchestrationSettings,
  type OrchestrationSettingsReport,
} from '../../api/orchestrationSettings'
import { Card, describeError, ErrorNote, FolderBanner, useFolderConversation } from '../codeSettings/shared'
import { OrchestrationSettingsForm } from './OrchestrationSettingsForm'
import { SettingsSection } from './SettingsSection'
import './orchestrationSettings.css'

const GROUPS_QUERY = 'effort,quota,context'

export function QuotaAndEffortSection({ client }: { client?: ApiClient }) {
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
          setError(describeError(caught, 'carregar as configurações de cota e esforço'))
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

  const effortItems = useMemo(
    () => (report?.items ?? []).filter((item) => item.group === 'effort'),
    [report],
  )
  const quotaItems = useMemo(
    () => (report?.items ?? []).filter((item) => item.group === 'quota'),
    [report],
  )
  const contextItems = useMemo(
    () => (report?.items ?? []).filter((item) => item.group === 'context'),
    [report],
  )

  return (
    <SettingsSection eyebrow="INTELIGÊNCIA / COTA E ESFORÇO" title="Cota e esforço">
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
          <div data-testid="quota-effort-section-effort">
            <Card title="Esforço de raciocínio" id="quota-effort-heading-effort">
              <p className="code-settings__muted">
                Defina o esforço padrão, políticas para subagentes, modo plano e escalada após reprovação.
              </p>
              <OrchestrationSettingsForm
                items={effortItems}
                busy={saving}
                hideGroupHeaders
                onSave={handleSave}
              />
            </Card>
          </div>

          <div data-testid="quota-effort-section-quota">
            <Card title="Cota de assinatura" id="quota-effort-heading-quota">
              <p className="code-settings__muted">
                Controle a exibição no compositor, limites de aviso/bloqueio e troca automática entre assinaturas.
              </p>
              <OrchestrationSettingsForm
                items={quotaItems}
                busy={saving}
                hideGroupHeaders
                onSave={handleSave}
              />
            </Card>
          </div>

          <div data-testid="quota-effort-section-context">
            <Card title="Janela de contexto por modelo" id="quota-effort-heading-context">
              <p className="code-settings__muted">
                Ajuste limites manuais por modelo (50.000 a 1.000.000 tokens) e o uso de janela longa com créditos.
              </p>
              <OrchestrationSettingsForm
                items={contextItems}
                busy={saving}
                hideGroupHeaders
                onSave={handleSave}
              />
            </Card>
          </div>
        </div>
      )}
    </SettingsSection>
  )
}

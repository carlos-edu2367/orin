import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import type { ApiClient } from '../../api/client'
import {
  listProviderModels,
  PROVIDER_NAMES,
  type ProviderModel,
  type ProviderName,
} from '../../api/providers'
import { inspectNewWorkspaceFolder } from '../../api/workspace'
import type { CodeSessionProposalRequest } from './activityTypes'

export type CodeSessionProposalDecision = {
  title: string
  prompt: string
  workspace_path: string | null
  provider: ProviderName
  model_id: string
  permission_mode: 'accept_edits' | 'plan_mode' | 'ask_permissions'
  project_id: string | null
}

type CodeSessionProposalCardProps = {
  proposal: CodeSessionProposalRequest
  active: boolean
  client?: ApiClient
  defaultProvider?: ProviderName
  defaultModelId?: string
  models?: Partial<Record<ProviderName, ProviderModel[]>>
  onAccept: (decision: CodeSessionProposalDecision) => Promise<void>
  onDecline: () => Promise<void>
}

type LaunchedSessionRecord = {
  conversation_id: string
  project_id?: string | null
  title?: string
}

const PROVIDER_DISPLAY_LABELS: Record<ProviderName, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  openrouter: 'OpenRouter',
  omniroute: 'OmniRoute',
  ollama: 'Ollama',
}

const FALLBACK_MODELS_BY_PROVIDER: Record<ProviderName, string[]> = {
  openai: ['gpt-5', 'gpt-4.1', 'o4-mini'],
  anthropic: ['claude-sonnet-4-5', 'claude-opus-4-1', 'claude-haiku-3-5'],
  openrouter: ['anthropic/claude-sonnet-4.5', 'openai/gpt-5', 'deepseek/deepseek-r1'],
  omniroute: ['auto', 'gpt-5', 'claude-sonnet-4-5'],
  ollama: ['qwen2.5-coder:32b', 'llama3.3:70b', 'deepseek-r1:14b'],
}

const PLACEHOLDER_MODEL_IDS = new Set(['default', 'current', 'none'])

function sanitizeProposalModelId(
  raw: string | null | undefined,
  provider?: string | null,
): string {
  const trimmed = raw?.trim() ?? ''
  if (!trimmed) return ''
  const lower = trimmed.toLowerCase()
  if (PLACEHOLDER_MODEL_IDS.has(lower)) return ''
  if (lower === 'auto' && provider !== 'omniroute') return ''
  return trimmed
}

function resolveInitialProvider(
  candidate: string | null | undefined,
  proposalModelId: string | null | undefined,
  fallback?: ProviderName,
): ProviderName {
  const explicitModel = sanitizeProposalModelId(proposalModelId, candidate)
  if (!explicitModel && fallback && PROVIDER_NAMES.includes(fallback)) {
    return fallback
  }
  if (candidate && PROVIDER_NAMES.includes(candidate as ProviderName)) {
    return candidate as ProviderName
  }
  if (fallback && PROVIDER_NAMES.includes(fallback)) {
    return fallback
  }
  return 'openai'
}

function readLaunchedSession(proposalId: string): LaunchedSessionRecord | null {
  try {
    const raw = localStorage.getItem(`orin.launched-code-session.${proposalId}`)
    if (!raw) return null
    const parsed = JSON.parse(raw) as LaunchedSessionRecord
    if (parsed && typeof parsed.conversation_id === 'string' && parsed.conversation_id) {
      return parsed
    }
  } catch {
    // Ignore storage read errors
  }
  return null
}

export function CodeSessionProposalCard({
  proposal,
  active,
  client,
  defaultProvider,
  defaultModelId,
  models,
  onAccept,
  onDecline,
}: CodeSessionProposalCardProps) {
  const sanitizedProposalModel = sanitizeProposalModelId(proposal.model_id, proposal.provider)
  const initialProvider = resolveInitialProvider(
    proposal.provider,
    proposal.model_id,
    defaultProvider,
  )
  const initialModelId =
    sanitizedProposalModel ||
    defaultModelId?.trim() ||
    models?.[initialProvider]?.[0]?.model_id ||
    FALLBACK_MODELS_BY_PROVIDER[initialProvider][0]

  const [workspacePath, setWorkspacePath] = useState(proposal.workspace_path ?? '')
  const [provider, setProvider] = useState<ProviderName>(initialProvider)
  const [modelId, setModelId] = useState<string>(initialModelId)
  const [permissionMode, setPermissionMode] = useState<'accept_edits' | 'plan_mode' | 'ask_permissions'>(
    proposal.permission_mode === 'plan_mode' || proposal.permission_mode === 'ask_permissions'
      ? proposal.permission_mode
      : 'accept_edits',
  )
  const [prompt, setPrompt] = useState(proposal.prompt)
  const [submitting, setSubmitting] = useState<'accept' | 'decline' | null>(null)
  const [pickingFolder, setPickingFolder] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fetchedModels, setFetchedModels] = useState<Partial<Record<ProviderName, ProviderModel[]>>>({})
  const userEditedSelectionRef = useRef(false)
  const [launched, setLaunched] = useState<LaunchedSessionRecord | null>(() =>
    readLaunchedSession(proposal.proposal_id),
  )

  useEffect(() => {
    if (userEditedSelectionRef.current || sanitizedProposalModel) return
    if (defaultProvider && PROVIDER_NAMES.includes(defaultProvider)) {
      setProvider(defaultProvider)
    }
    if (defaultModelId?.trim()) {
      setModelId(defaultModelId.trim())
    }
  }, [defaultModelId, defaultProvider, sanitizedProposalModel])

  useEffect(() => {
    if (!client) return
    if (models?.[provider]?.length || fetchedModels[provider]?.length) return
    const controller = new AbortController()
    listProviderModels(client, provider, controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return
        setFetchedModels((prev) => ({ ...prev, [provider]: items }))
      })
      .catch(() => {
        // Ignore model catalog fetch errors; fallback list remains available
      })
    return () => controller.abort()
  }, [client, fetchedModels, models, provider])

  const effectiveModelsForProvider = useMemo(
    () => models?.[provider] ?? fetchedModels[provider] ?? [],
    [fetchedModels, models, provider],
  )

  const modelOptions = useMemo(() => {
    const seen = new Set<string>()
    const list: Array<{ id: string; label: string }> = []
    const labelByModelId = new Map<string, string>()
    for (const item of effectiveModelsForProvider) {
      if (item.model_id && item.display_name) {
        labelByModelId.set(item.model_id, item.display_name)
      }
    }

    const addOption = (id: string | null | undefined, label?: string) => {
      const trimmed = sanitizeProposalModelId(id, provider)
      if (!trimmed || seen.has(trimmed)) return
      seen.add(trimmed)
      list.push({ id: trimmed, label: label || labelByModelId.get(trimmed) || trimmed })
    }

    addOption(modelId)
    addOption(sanitizedProposalModel)
    if (!provider || provider === defaultProvider) {
      addOption(defaultModelId)
    }
    for (const item of effectiveModelsForProvider) {
      addOption(item.model_id, item.display_name || item.model_id)
    }
    for (const fallbackId of FALLBACK_MODELS_BY_PROVIDER[provider] ?? []) {
      addOption(fallbackId)
    }
    return list
  }, [defaultModelId, defaultProvider, effectiveModelsForProvider, modelId, provider, sanitizedProposalModel])

  async function handleChooseFolder() {
    if (!client || pickingFolder || submitting !== null) return
    setPickingFolder(true)
    setError(null)
    try {
      const outcome = await inspectNewWorkspaceFolder(client, null, proposal.project_id ?? undefined)
      if (outcome.kind === 'folder') {
        setWorkspacePath(outcome.path)
      }
    } catch {
      setError('Não foi possível abrir o seletor de pasta.')
    } finally {
      setPickingFolder(false)
    }
  }

  async function handleAccept(event: FormEvent) {
    event.preventDefault()
    if (submitting !== null || !prompt.trim()) return
    setError(null)
    setSubmitting('accept')
    try {
      await onAccept({
        title: proposal.title,
        prompt: prompt.trim(),
        workspace_path: workspacePath.trim() || null,
        provider,
        model_id: sanitizeProposalModelId(modelId, provider) || initialModelId,
        permission_mode: permissionMode,
        project_id: proposal.project_id,
      })
      const updated = readLaunchedSession(proposal.proposal_id)
      if (updated) {
        setLaunched(updated)
      }
    } catch {
      setError('Não foi possível iniciar a sessão no Orin Code. Tente novamente.')
    } finally {
      setSubmitting(null)
    }
  }

  async function handleDecline() {
    if (submitting !== null) return
    setError(null)
    setSubmitting('decline')
    try {
      await onDecline()
    } catch {
      setError('Não foi possível registrar sua escolha. Tente novamente.')
    } finally {
      setSubmitting(null)
    }
  }

  if (launched) {
    const launchedUrl = launched.project_id
      ? `/projects/${encodeURIComponent(launched.project_id)}/chats/${encodeURIComponent(launched.conversation_id)}`
      : `/chats/${encodeURIComponent(launched.conversation_id)}`
    return (
      <section
        className="approval-card"
        data-active="false"
        data-testid="code-proposal-card-launched"
        aria-label={`Sessão iniciada: ${proposal.title}`}
      >
        <header className="approval-card__header">
          <span className="approval-card__glyph font-mono text-xs font-bold" aria-hidden="true">
            &gt;_
          </span>
          <div>
            <strong>{proposal.title}</strong>
            <p>Sessão iniciada no Orin Code</p>
          </div>
        </header>
        <div className="approval-card__actions">
          <Link to={launchedUrl} className="approval-card__submit" data-testid="code-proposal-open-link">
            Abrir no Orin Code →
          </Link>
        </div>
      </section>
    )
  }

  return (
    <section
      className="approval-card"
      data-active={active}
      data-testid="code-proposal-card"
      aria-label={`Iniciar no Orin Code: ${proposal.title}`}
    >
      <header className="approval-card__header">
        <span className="approval-card__glyph font-mono text-xs font-bold" aria-hidden="true">
          &gt;_
        </span>
        <div>
          <strong>{proposal.title}</strong>
          <p>
            Revise a pasta, o modelo, o modo de permissão e o prompt antes de abrir a sessão no Orin Code.
          </p>
        </div>
      </header>

      <form className="approval-card__form" onSubmit={(event) => void handleAccept(event)}>
        <fieldset disabled={!active || submitting !== null}>
          <div className="approval-card__field">
            <label htmlFor={`code-prop-folder-${proposal.proposal_id}`}>Pasta do projeto</label>
            <div className="approval-card__folder-row">
              <input
                id={`code-prop-folder-${proposal.proposal_id}`}
                type="text"
                data-testid="code-proposal-workspace-input"
                value={workspacePath}
                onChange={(event) => setWorkspacePath(event.target.value)}
                placeholder="Workspace gerenciado (ou selecione uma pasta local)"
              />
              {client && (
                <button
                  type="button"
                  className="ghost-button approval-card__folder-btn"
                  data-testid="code-proposal-choose-folder-button"
                  onClick={() => void handleChooseFolder()}
                  disabled={pickingFolder || submitting !== null}
                >
                  {pickingFolder ? 'Abrindo…' : 'Escolher pasta…'}
                </button>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="approval-card__field">
              <span>Provedor</span>
              <select
                data-testid="code-proposal-provider-select"
                value={provider}
                onChange={(event) => {
                  userEditedSelectionRef.current = true
                  const nextProvider = event.target.value as ProviderName
                  setProvider(nextProvider)
                  const nextModels = models?.[nextProvider] ?? fetchedModels[nextProvider]
                  if (nextModels && nextModels.length > 0) {
                    setModelId(nextModels[0].model_id)
                  } else {
                    setModelId(FALLBACK_MODELS_BY_PROVIDER[nextProvider]?.[0] ?? '')
                  }
                }}
              >
                {PROVIDER_NAMES.map((p) => (
                  <option key={p} value={p}>
                    {PROVIDER_DISPLAY_LABELS[p] ?? p}
                  </option>
                ))}
              </select>
            </label>

            <label className="approval-card__field">
              <span>Modo de permissão</span>
              <select
                data-testid="code-proposal-permission-select"
                value={permissionMode}
                onChange={(event) =>
                  setPermissionMode(
                    event.target.value as 'accept_edits' | 'plan_mode' | 'ask_permissions',
                  )
                }
              >
                <option value="accept_edits">Aceitar edições automaticamente</option>
                <option value="plan_mode">Modo Plano (aprovar plano antes)</option>
                <option value="ask_permissions">Pedir permissão a cada ação</option>
              </select>
            </label>
          </div>

          <label className="approval-card__field">
            <span>Modelo</span>
            <select
              data-testid="code-proposal-model-select"
              value={modelId}
              onChange={(event) => {
                userEditedSelectionRef.current = true
                setModelId(event.target.value)
              }}
            >
              {modelOptions.map((opt) => (
                <option key={opt.id} value={opt.id}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>

          <label className="approval-card__field">
            <span>Prompt / Especificação</span>
            <textarea
              rows={5}
              data-testid="code-proposal-prompt-input"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
            />
          </label>
        </fieldset>

        <footer className="approval-card__actions">
          <button
            type="button"
            className="approval-card__decline"
            data-testid="code-proposal-decline-button"
            onClick={() => void handleDecline()}
            disabled={!active || submitting !== null}
          >
            {submitting === 'decline' ? 'Enviando…' : 'Continuar aqui no Chat'}
          </button>
          <button
            type="submit"
            className="approval-card__submit"
            data-testid="code-proposal-accept-button"
            disabled={!active || submitting !== null || !prompt.trim()}
          >
            {submitting === 'accept' ? 'Iniciando…' : 'Iniciar no Orin Code'}
          </button>
        </footer>
        {error && (
          <p className="approval-card__error" role="alert">
            {error}
          </p>
        )}
      </form>
    </section>
  )
}

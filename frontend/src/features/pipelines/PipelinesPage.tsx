import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import {
  getPipeline,
  listPipelineRuns,
  listPipelines,
  type PipelineDetail,
  type PipelineList as PipelineListData,
  type PipelineRunSummaryRow,
} from '../../api/pipelines'
import { PipelineDefinition } from './PipelineDetail'
import { PipelineEditor, NEW_PIPELINE_TEMPLATE } from './PipelineEditor'
import { PipelineList } from './PipelineList'
import { PipelineRunForm } from './PipelineRunForm'
import { usePipelineScope } from './usePipelineScope'
import './pipelines.css'

type Tab = 'run' | 'definition' | 'edit'

const TABS: ReadonlyArray<{ id: Tab; label: string }> = [
  { id: 'run', label: 'Rodar' },
  { id: 'definition', label: 'Definição' },
  { id: 'edit', label: 'Editar' },
]

function presetsFrom(search: URLSearchParams): Record<string, string> {
  const presets: Record<string, string> = {}
  for (const [key, value] of search.entries()) {
    if (key.startsWith('in.') && key.length > 3) presets[key.slice(3)] = value
  }
  return presets
}

/** `id:` of a built-in text rewritten so it can be saved as the person's own copy. */
function copyOf(text: string, id: string): string {
  return text.replace(/^id:\s*.*$/m, `id: ${id}-copia`).replace(/^name:\s*(.*)$/m, (_line, name: string) => `name: ${name.trim()} (cópia)`)
}

export function PipelinesPage({ client }: { client?: ApiClient }) {
  const apiClient = useMemo(() => client ?? createBrowserApiClient(), [client])
  const navigate = useNavigate()
  const scope = usePipelineScope(apiClient)
  const [search, setSearch] = useSearchParams()
  const selectedId = search.get('pipeline')
  const creating = search.get('new') === '1'
  const presets = useMemo(() => presetsFrom(search), [search])
  const [tab, setTab] = useState<Tab>('run')
  const [list, setList] = useState<PipelineListData | null>(null)
  const [runRows, setRuns] = useState<PipelineRunSummaryRow[]>([])
  const [loadedDetail, setDetail] = useState<PipelineDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadedDetailError, setDetailError] = useState<{ id: string; code: string; message: string } | null>(null)
  const [listedFor, setListedFor] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  const [duplicate, setDuplicate] = useState(false)
  const conversationId = scope.conversationId
  // Derived, not reset inside effects: what is shown always belongs to the current selection and conversation.
  const loading = scope.loading || listedFor !== `${conversationId ?? ''}|${revision}`
  const runs = conversationId ? runRows : []
  const detailShown = Boolean(selectedId) && list?.enabled !== false
  const detail = detailShown && loadedDetail?.id === selectedId ? loadedDetail : null
  const detailError = detailShown && loadedDetailError?.id === selectedId ? loadedDetailError : null

  useEffect(() => {
    if (scope.loading) return undefined
    const controller = new AbortController()
    const key = `${conversationId ?? ''}|${revision}`
    listPipelines(apiClient, conversationId, controller.signal)
      .then((next) => { setList(next); setError(null) })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return
        setList(null)
        setError(caught instanceof ApiError && caught.status === 404
          ? 'Os pipelines não estão disponíveis neste Orin (precisam de uma instalação local com acesso à pasta).'
          : 'Não foi possível carregar os pipelines.')
      })
      .finally(() => { if (!controller.signal.aborted) setListedFor(key) })
    return () => controller.abort()
  }, [apiClient, conversationId, scope.loading, revision])

  useEffect(() => {
    if (!conversationId) return undefined
    const controller = new AbortController()
    listPipelineRuns(apiClient, { conversationId, signal: controller.signal })
      .then(setRuns)
      .catch(() => { if (!controller.signal.aborted) setRuns([]) })
    return () => controller.abort()
  }, [apiClient, conversationId, revision])

  useEffect(() => {
    if (!selectedId || list?.enabled === false) return undefined
    let cancelled = false
    getPipeline(apiClient, selectedId, conversationId)
      .then((next) => { if (!cancelled) { setDetail(next); setDetailError(null) } })
      .catch((caught: unknown) => {
        if (cancelled) return
        setDetail(null)
        setDetailError({
          id: selectedId,
          code: caught instanceof ApiError ? caught.code : 'ERROR',
          message: caught instanceof ApiError && caught.code === 'E_DISABLED'
            ? 'Os pipelines estão desligados.'
            : caught instanceof ApiError && caught.serverMessage ? caught.serverMessage : 'Não foi possível abrir este pipeline.',
        })
      })
    return () => { cancelled = true }
  }, [apiClient, selectedId, conversationId, revision, list?.enabled])

  const reload = useCallback(() => setRevision((value) => value + 1), [])

  function select(id: string) {
    const next = new URLSearchParams(search)
    next.set('pipeline', id)
    next.delete('run')
    next.delete('new')
    for (const key of [...next.keys()]) if (key.startsWith('in.')) next.delete(key)
    setSearch(next)
    setTab('run')
    setDuplicate(false)
  }

  function startNew() {
    const next = new URLSearchParams(search)
    next.delete('pipeline')
    next.delete('run')
    next.set('new', '1')
    setSearch(next)
  }

  const heading = scope.projectName ? `Pipelines · ${scope.projectName}` : 'Pipelines'
  const selected = list?.pipelines.find((item) => item.id === selectedId) ?? null
  const disabledList = list?.enabled === false

  return (
    <div className="pipelines-page" data-testid="pipelines-page">
      <header className="pipelines-page__head">
        <div>
          <p className="eyebrow">WORKSPACE / PIPELINES</p>
          <h1>{heading}</h1>
          <p className="pipelines-page__lede">
            Fluxos declarativos de agentes: etapas, gates, laços de correção e orçamento. Nada roda sem você apertar &quot;Rodar&quot;, e o custo aparece antes.
          </p>
        </div>
        <div className="pipelines-page__tools">
          {scope.chats.length > 0 && (
            <label className="pipelines-page__conv">
              <span>Conversa de contexto</span>
              <select value={conversationId ?? ''} onChange={(event) => scope.setConversation(event.target.value)}>
                {scope.chats.map((chat) => <option key={chat.conversation_id} value={chat.conversation_id}>{chat.title || chat.conversation_id}</option>)}
              </select>
            </label>
          )}
          {conversationId && !scope.chats.length && (
            <Link className="pipeline-link" to={`/chats/${encodeURIComponent(conversationId)}`}>Voltar à conversa</Link>
          )}
          <button type="button" className="button button--secondary pipelines-btn" onClick={startNew} disabled={disabledList}>Novo pipeline</button>
        </div>
      </header>

      {error && <p className="pipeline-note pipeline-note--bad" role="alert">{error}</p>}
      {loading && !list && <p className="pipeline-muted" role="status">Carregando pipelines…</p>}

      {list && (
        <div className="pipelines-page__body">
          <aside className="pipelines-page__side" aria-label="Lista de pipelines">
            <PipelineList list={list} selectedId={selectedId} onSelect={select} runs={runs} conversationId={conversationId} />
          </aside>

          <main className="pipelines-page__main" aria-live="polite">
            {disabledList && <p className="pipeline-muted">Ligue os pipelines em Configurações &gt; Orquestração para listar, editar e rodar.</p>}

            {!disabledList && creating && (
              <PipelineEditor
                client={apiClient}
                initialText={NEW_PIPELINE_TEMPLATE}
                original={null}
                conversationId={conversationId}
                scope="user"
                onCancel={() => { const next = new URLSearchParams(search); next.delete('new'); setSearch(next) }}
                onSaved={(saved) => { reload(); const next = new URLSearchParams(search); next.delete('new'); next.set('pipeline', saved.id); setSearch(next) }}
              />
            )}

            {!disabledList && !creating && !selectedId && !detailError && (
              <div className="pipelines-page__empty">
                <h2>Escolha um pipeline</h2>
                <p className="pipeline-muted">Selecione um da lista para ver o que ele faz, quem vai gastar e rodar.</p>
              </div>
            )}

            {detailError && (
              <p className="pipeline-note pipeline-note--warn" role="status" data-testid="detail-error" data-code={detailError.code}>{detailError.message}</p>
            )}

            {!disabledList && !creating && detail && selected && (
              <section className="pipelines-page__detail" aria-label={detail.name}>
                <header className="pipelines-page__detail-head">
                  <div>
                    <h2>{detail.name}</h2>
                    <p className="pipeline-muted"><code>{detail.id}</code>{detail.description ? ` · ${detail.description}` : ''}</p>
                  </div>
                </header>
                <div className="pipeline-tabs" role="tablist" aria-label="Seções do pipeline">
                  {TABS.map((item) => (
                    <button key={item.id} type="button" role="tab" id={`pipeline-tab-${item.id}`} aria-selected={tab === item.id} aria-controls={`pipeline-panel-${item.id}`} className={tab === item.id ? 'is-active' : ''} onClick={() => setTab(item.id)}>
                      {item.label}
                    </button>
                  ))}
                </div>
                {!detail.valid && tab === 'run' && (
                  <p className="pipeline-note pipeline-note--bad" role="alert">Este pipeline tem erros e não pode rodar. Veja a aba Definição ou corrija em Editar.</p>
                )}
                <div role="tabpanel" id={`pipeline-panel-${tab}`} aria-labelledby={`pipeline-tab-${tab}`}>
                  {tab === 'run' && detail.valid && (
                    <PipelineRunForm
                      key={`${detail.id}:${detail.definitionHash}:${search.toString()}`}
                      client={apiClient}
                      pipeline={detail}
                      conversationId={conversationId}
                      presets={presets}
                      onApproved={reload}
                      onStarted={(run) => navigate(`/pipelines/runs/${encodeURIComponent(run.runId)}`)}
                    />
                  )}
                  {tab === 'definition' && <PipelineDefinition client={apiClient} pipeline={detail} conversationId={conversationId} onChanged={reload} />}
                  {tab === 'edit' && (
                    detail.source === 'builtin' && !duplicate ? (
                      <div className="pipelines-page__empty">
                        <p className="pipeline-muted">Os pipelines prontos não são editados no lugar. Faça uma cópia sua para ajustar.</p>
                        <button type="button" className="button button--secondary pipelines-btn" onClick={() => setDuplicate(true)}>Criar cópia para editar</button>
                      </div>
                    ) : (
                      <PipelineEditor
                        key={`${detail.id}:${detail.definitionHash}:${duplicate}`}
                        client={apiClient}
                        initialText={duplicate ? copyOf(detail.text, detail.id) : detail.text}
                        original={duplicate ? null : detail}
                        conversationId={conversationId}
                        scope={detail.source === 'project' && !duplicate ? 'project' : 'user'}
                        onReload={reload}
                        onSaved={(saved) => { setDuplicate(false); reload(); select(saved.id); setTab('definition') }}
                      />
                    )
                  )}
                </div>
              </section>
            )}
          </main>
        </div>
      )}
    </div>
  )
}

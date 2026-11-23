import { useEffect, useMemo, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import { issuesOf, savePipeline, validatePipelineText, type PipelineDetail, type PipelineIssue } from '../../api/pipelines'
import './pipelines.css'

const VALIDATE_DELAY_MS = 700

export const NEW_PIPELINE_TEMPLATE = `version: 1
id: meu-pipeline
name: Meu pipeline
description: Descreva em uma frase o que ele faz.
inputs:
  tarefa: {type: string, required: true, max_length: 4000}
budget:
  max_tokens: 500000
  max_minutes: 30
steps:
  - id: fazer
    agent: general
    prompt: |
      Faça a tarefa abaixo e resuma o que foi feito.
      Tarefa: \${{ inputs.tarefa }}
`

/** The `id:` line of the text, which is the pipeline's file name. */
export function idOfText(text: string): string {
  return /^id:\s*["']?([a-z0-9][a-z0-9-]{0,63})["']?\s*$/m.exec(text)?.[1] ?? ''
}

function describeSaveError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'PIPELINE_CONFLICT') return 'O arquivo mudou (ou já existe um com este id) depois que você o abriu. Recarregue antes de gravar para não sobrescrever a outra edição.'
    if (error.code === 'PIPELINE_UNTRUSTED') return 'Gravar no projeto exige que a pasta seja confiável. Confie na pasta em Configurações > Pastas confiáveis.'
    if (error.code === 'PIPELINE_INVALID') return 'O pipeline tem erros e não foi gravado. Veja a lista abaixo.'
    if (error.serverMessage) return error.serverMessage
  }
  return 'Não foi possível gravar o pipeline. Tente de novo.'
}

function lineStartOffset(text: string, line: number): number {
  let offset = 0
  for (let index = 1; index < line; index += 1) {
    const next = text.indexOf('\n', offset)
    if (next === -1) return text.length
    offset = next + 1
  }
  return offset
}

export function PipelineEditor({
  client,
  initialText,
  original,
  conversationId,
  scope: initialScope,
  onSaved,
  onReload,
  onCancel,
}: {
  client: ApiClient
  initialText: string
  /** The saved pipeline being edited (carries the hash `If-Match` needs); null for a new or duplicated one. */
  original: PipelineDetail | null
  conversationId: string | null
  scope: 'user' | 'project'
  onSaved: (saved: PipelineDetail) => void
  onReload?: () => void
  onCancel?: () => void
}) {
  const [text, setText] = useState(initialText)
  const [scope, setScope] = useState<'user' | 'project'>(initialScope)
  const [errors, setErrors] = useState<PipelineIssue[]>([])
  const [warnings, setWarnings] = useState<PipelineIssue[]>([])
  // What was validated, as a key of text + scope + conversation: a new key means "validating" until its answer lands.
  const [validatedFor, setValidatedFor] = useState<string | null>(null)
  const [settledFor, setSettledFor] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  const [saving, setSaving] = useState(false)
  const areaRef = useRef<HTMLTextAreaElement | null>(null)
  const gutterRef = useRef<HTMLDivElement | null>(null)

  const id = idOfText(text)
  const lineCount = useMemo(() => text.split('\n').length, [text])
  const validationKey = `${scope}|${conversationId ?? ''}|${text}`
  const validated = validatedFor === validationKey
  const validating = settledFor !== validationKey

  useEffect(() => {
    const key = `${scope}|${conversationId ?? ''}|${text}`
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      validatePipelineText(client, text, { source: scope, conversationId, signal: controller.signal })
        .then((report) => {
          setErrors(report.errors)
          setWarnings(report.warnings)
          setValidatedFor(key)
        })
        .catch(() => { if (!controller.signal.aborted) setValidatedFor(null) })
        .finally(() => { if (!controller.signal.aborted) setSettledFor(key) })
    }, VALIDATE_DELAY_MS)
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [client, text, scope, conversationId])

  function goToLine(line: number | null) {
    const area = areaRef.current
    if (!area || !line) return
    const offset = lineStartOffset(text, line)
    area.focus()
    area.setSelectionRange(offset, offset)
    const lineHeight = 20
    area.scrollTop = Math.max(0, (line - 3) * lineHeight)
    if (gutterRef.current) gutterRef.current.scrollTop = area.scrollTop
  }

  const editingSameFile = original !== null && original.id === id
  const canSave = validated && !validating && errors.length === 0 && id !== '' && !saving && text.trim() !== ''

  async function save() {
    if (!canSave) return
    setSaving(true)
    setSaveError(null)
    setConflict(false)
    try {
      const saved = await savePipeline(client, id, text, {
        scope,
        conversationId,
        ifMatch: editingSameFile ? original.definitionHash : null,
      })
      onSaved(saved)
    } catch (caught: unknown) {
      setSaveError(describeSaveError(caught))
      setConflict(caught instanceof ApiError && caught.code === 'PIPELINE_CONFLICT')
      const refused = issuesOf(caught)
      if (refused.length) setErrors(refused)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="pipeline-editor" aria-label="Editor de pipeline" data-testid="pipeline-editor">
      <div className="pipeline-editor__bar">
        <label className="pipeline-editor__scope">
          <span>Gravar em</span>
          <select value={scope} onChange={(event) => setScope(event.target.value === 'project' ? 'project' : 'user')} disabled={saving}>
            <option value="user">Seus pipelines</option>
            {conversationId && <option value="project">Projeto (pasta confiável)</option>}
          </select>
        </label>
        <span className="pipeline-muted" data-testid="editor-id">{id ? <>arquivo <code>{id}.yaml</code></> : 'defina o id'}</span>
        <span className="pipeline-muted" role="status" data-testid="editor-status">
          {validating ? 'Validando…' : !validated ? 'Sem validação' : errors.length > 0 ? `${errors.length} ${errors.length === 1 ? 'erro' : 'erros'}` : 'Sem erros'}
        </span>
      </div>

      <div className="pipeline-editor__frame">
        <div className="pipeline-editor__gutter" ref={gutterRef} aria-hidden="true">
          {Array.from({ length: lineCount }, (_, index) => <span key={index}>{index + 1}</span>)}
        </div>
        <textarea
          ref={areaRef}
          className="pipeline-editor__area"
          aria-label="Texto YAML do pipeline"
          spellCheck={false}
          wrap="off"
          value={text}
          disabled={saving}
          onChange={(event) => setText(event.target.value)}
          onScroll={(event) => { if (gutterRef.current) gutterRef.current.scrollTop = event.currentTarget.scrollTop }}
        />
      </div>

      {errors.length > 0 && (
        <ul className="pipeline-issues" aria-label="Erros do pipeline" data-testid="editor-errors">
          {errors.map((issue, index) => (
            <li key={`${issue.code}-${index}`} className="pipeline-issues__item pipeline-issues__item--bad">
              {issue.line ? (
                <button type="button" className="pipeline-issues__go" onClick={() => goToLine(issue.line)}>
                  linha {issue.line}{issue.column ? `, coluna ${issue.column}` : ''}
                </button>
              ) : <span className="pipeline-issues__go">arquivo</span>}
              <span>{issue.message}</span>
              <code>{issue.code}</code>
            </li>
          ))}
        </ul>
      )}
      {warnings.length > 0 && (
        <ul className="pipeline-issues" aria-label="Avisos do pipeline">
          {warnings.map((issue, index) => (
            <li key={`${issue.code}-${index}`} className="pipeline-issues__item pipeline-issues__item--warn">
              {issue.line ? (
                <button type="button" className="pipeline-issues__go" onClick={() => goToLine(issue.line)}>linha {issue.line}</button>
              ) : <span className="pipeline-issues__go">aviso</span>}
              <span>{issue.message}</span>
            </li>
          ))}
        </ul>
      )}

      {saveError && (
        <p className="pipeline-note pipeline-note--bad" role="alert" data-testid="editor-save-error">
          {saveError}{' '}
          {conflict && onReload && <button type="button" className="pipeline-link-button" onClick={onReload}>Recarregar o arquivo</button>}
        </p>
      )}

      <div className="pipeline-form__actions">
        <button type="button" className="button button--primary pipelines-btn" disabled={!canSave} onClick={() => void save()}>
          {saving ? 'Gravando…' : editingSameFile ? 'Gravar alterações' : 'Gravar como novo'}
        </button>
        {onCancel && <button type="button" className="button button--secondary pipelines-btn" disabled={saving} onClick={onCancel}>Fechar editor</button>}
      </div>
    </section>
  )
}

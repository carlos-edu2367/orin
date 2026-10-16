import { useState } from 'react'
import type { ActivityDiffHunk, ConversationActivityEvent, StructuredPatchHunk } from './activityTypes'

export type CodeToolCallBlockProps = {
  event: ConversationActivityEvent
  onOpenFile?: (path: string) => void
  defaultDiffOpen?: boolean
}

export type ToolVisualStatus = 'completed' | 'running' | 'failed' | 'muted'

export function resolveVisualStatus(event: ConversationActivityEvent): ToolVisualStatus {
  if (
    event.state === 'cancelled' ||
    event.status === 'cancelled' ||
    event.errorCode === 'TURN_CANCELLED'
  ) {
    return 'muted'
  }
  if (event.state === 'failed' || event.status === 'failed' || Boolean(event.errorCode)) {
    return 'failed'
  }
  if (
    event.state === 'waiting_user' ||
    event.state === 'waiting_agent'
  ) {
    return 'muted'
  }
  if (
    event.state === 'waiting_tool' ||
    event.state === 'working' ||
    event.state === 'queued' ||
    event.state === 'retrying' ||
    event.status === 'running' ||
    event.status === 'in_progress' ||
    event.status === 'started'
  ) {
    return 'running'
  }
  const exitCode = event.exitCode ?? event.bashOutput?.exit_code
  if (typeof exitCode === 'number' && exitCode !== 0) {
    return 'failed'
  }
  return 'completed'
}

const FILE_TOOLS = new Set(['read_file', 'edit_file', 'write_file', 'notebook_edit'])
const TERMINAL_PREVIEW_LINE_LIMIT = 6
const DIFF_PREVIEW_LINE_LIMIT = 12

function sanitizeTerminalText(value: string | undefined | null): string {
  if (!value) return ''
  const trimmed = value.trim()
  if (!trimmed) return ''
  if (trimmed === '[REDACTED]' || trimmed.replace(/\[REDACTED\]/g, '').trim() === '') {
    return ''
  }
  return value
}

function isReusedReadText(value: string | undefined | null): boolean {
  if (!value) return false
  return /reaproveitado da chamada anterior|^Arquivo inalterado:/i.test(value.trim())
}

function extractFilePathFromSummary(summary: string | undefined): string {
  if (!summary) return ''
  const match = summary.match(/^Arquivo inalterado:\s*(.+)$/i)
  return match ? match[1].trim() : ''
}

export function CodeToolCallBlock({
  event,
  onOpenFile,
  defaultDiffOpen = true,
}: CodeToolCallBlockProps) {
  const toolName = event.toolName ?? 'tool'
  const rawFilePath = event.filePath ?? event.path ?? event.diff?.path ?? ''
  const filePath = rawFilePath || (toolName === 'read_file' ? extractFilePathFromSummary(event.summary) : '')
  const hunks: Array<StructuredPatchHunk | ActivityDiffHunk> =
    event.structuredPatch && event.structuredPatch.length > 0
      ? event.structuredPatch
      : event.diff?.structured_patch && event.diff.structured_patch.length > 0
        ? event.diff.structured_patch
        : []

  const isDiffTool = toolName === 'edit_file' || toolName === 'write_file'
  const hasDiffHunks = isDiffTool && hunks.length > 0
  const totalDiffLines = hunks.reduce((sum, hunk) => sum + hunk.lines.length, 0)
  const hasLongDiff = totalDiffLines > DIFF_PREVIEW_LINE_LIMIT

  const [diffOpen, setDiffOpen] = useState(defaultDiffOpen)
  const [diffExpanded, setDiffExpanded] = useState(false)
  const [terminalExpanded, setTerminalExpanded] = useState(false)

  const visualStatus = resolveVisualStatus(event)
  const isCancelled =
    event.state === 'cancelled' ||
    event.status === 'cancelled' ||
    event.errorCode === 'TURN_CANCELLED'

  const statusDotClass =
    visualStatus === 'completed'
      ? 'text-emerald-400'
      : visualStatus === 'running'
        ? 'text-amber-400 animate-pulse'
        : visualStatus === 'failed'
          ? 'text-rose-400'
          : 'text-[var(--text-muted)]'

  const addedLines = event.addedLines ?? event.diff?.added_lines
  const removedLines = event.removedLines ?? event.diff?.removed_lines
  const hasDiffStats = (toolName === 'edit_file' || toolName === 'write_file') && (addedLines !== undefined || removedLines !== undefined)

  const bashCommand = event.bashOutput?.command ?? event.command ?? ''
  const bashDescription = event.description ?? event.bashOutput?.description ?? ''
  const isBackground = Boolean(
    event.autoBackgrounded || event.bashOutput?.auto_backgrounded || event.bashOutput?.background,
  )
  const detectedUrl = event.detectedUrl ?? event.bashOutput?.detected_url
  const exitCode = event.exitCode ?? event.bashOutput?.exit_code

  const { verb, arg } = formatToolSignature(event, {
    toolName,
    filePath,
    bashCommand,
    bashDescription,
    visualStatus,
  })

  const canOpenFile = Boolean(onOpenFile && filePath && FILE_TOOLS.has(toolName))
  const isProseFile = /\.(md|mdx|txt)$/i.test(filePath)
  const isReadUnchanged =
    toolName === 'read_file' &&
    (Boolean(event.unchanged) || isReusedReadText(event.summary) || isReusedReadText(event.label))

  const extra = event as ConversationActivityEvent & {
    detail?: string
    error?: string
    output?: string
  }

  // Combine terminal output from bashOutput (stdout / stderr) or fallback to outputTail / output / content / detail
  const rawTerminalOutput = (() => {
    if (toolName !== 'run_command' && toolName !== 'bash') return ''
    const parts: string[] = []
    const cleanStdout = sanitizeTerminalText(event.bashOutput?.stdout)
    const cleanStderr = sanitizeTerminalText(event.bashOutput?.stderr)
    if (cleanStdout) parts.push(cleanStdout)
    if (cleanStderr) parts.push(cleanStderr)
    if (parts.length > 0) return parts.join('\n')
    return (
      sanitizeTerminalText(event.outputTail) ||
      sanitizeTerminalText(extra.output) ||
      sanitizeTerminalText(event.content) ||
      sanitizeTerminalText(extra.detail) ||
      ''
    )
  })()

  const terminalLines = rawTerminalOutput
    ? rawTerminalOutput.replace(/\r\n/g, '\n').replace(/\n$/, '').split('\n')
    : []
  const hasLongTerminalOutput = terminalLines.length > TERMINAL_PREVIEW_LINE_LIMIT
  const visibleTerminalLines =
    hasLongTerminalOutput && !terminalExpanded
      ? terminalLines.slice(0, TERMINAL_PREVIEW_LINE_LIMIT)
      : terminalLines

  const showCommandSubheader =
    (toolName === 'run_command' || toolName === 'bash') &&
    Boolean(bashDescription && bashCommand && bashCommand !== bashDescription)

  const errorDetailText = (() => {
    if (visualStatus !== 'failed') return ''
    const candidate =
      sanitizeTerminalText(extra.error) ||
      sanitizeTerminalText(extra.detail) ||
      sanitizeTerminalText(event.content) ||
      (event.summary && !event.summary.startsWith('$') ? sanitizeTerminalText(event.summary) : '') ||
      ''
    if (!candidate) return ''
    if (rawTerminalOutput && rawTerminalOutput.includes(candidate)) return ''
    return candidate
  })()

  // Slice diff hunks when previewing a long diff
  const visibleHunks = (() => {
    if (!hasLongDiff || diffExpanded) return hunks
    let remaining = DIFF_PREVIEW_LINE_LIMIT
    const sliced: Array<StructuredPatchHunk | ActivityDiffHunk> = []
    for (const hunk of hunks) {
      if (remaining <= 0) break
      if (hunk.lines.length <= remaining) {
        sliced.push(hunk)
        remaining -= hunk.lines.length
      } else {
        sliced.push({
          ...hunk,
          lines: hunk.lines.slice(0, remaining),
        })
        remaining = 0
      }
    }
    return sliced
  })()

  return (
    <div
      className="code-tool-call-block my-1.5 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-raised)]/70 text-[var(--text-primary)] overflow-hidden"
      data-testid="code-tool-call-block"
      data-tool-name={toolName}
      data-status={visualStatus}
    >
      <div
        className={`flex flex-wrap items-center justify-between gap-2 px-3 py-1.5 font-mono text-[13px] ${
          hasDiffHunks ? 'cursor-pointer select-none hover:bg-[var(--surface-hover)]/60' : ''
        }`}
        onClick={hasDiffHunks ? () => setDiffOpen((prev) => !prev) : undefined}
        role={hasDiffHunks ? 'button' : undefined}
        tabIndex={hasDiffHunks ? 0 : undefined}
        aria-expanded={hasDiffHunks ? diffOpen : undefined}
        onKeyDown={
          hasDiffHunks
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  setDiffOpen((prev) => !prev)
                }
              }
            : undefined
        }
      >
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <span className={`text-[11px] leading-none ${statusDotClass}`} aria-hidden="true">
            ●
          </span>
          <span className="truncate">
            <strong className="font-semibold text-[var(--text-primary)]">{verb}</strong>
            <span className="text-[var(--text-muted)]">(</span>
            {canOpenFile ? (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  onOpenFile?.(filePath)
                }}
                className="font-mono text-[13px] text-[var(--accent-primary)] hover:underline"
                title={`Abrir ${filePath}`}
              >
                {arg}
              </button>
            ) : (
              <span className="text-[var(--text-secondary)]">{arg}</span>
            )}
            <span className="text-[var(--text-muted)]">)</span>
          </span>

          {isReadUnchanged && (
            <span
              className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[11px] text-[var(--text-muted)]"
              data-testid="code-tool-unchanged-badge"
            >
              inalterado (sem alterações desde a última leitura)
            </span>
          )}

          {hasDiffStats && (
            <span
              className="inline-flex items-center gap-1.5 rounded bg-white/[0.05] px-1.5 py-0.5 text-[11px] font-medium"
              aria-label={`Alterações +${addedLines ?? 0} -${removedLines ?? 0}`}
            >
              <span className="text-emerald-400">+{addedLines ?? 0}</span>
              <span className="text-rose-400">-{removedLines ?? 0}</span>
            </span>
          )}

          {(toolName === 'run_command' || toolName === 'bash') && isBackground && (
            <span className="inline-flex items-center gap-1.5 rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] text-amber-300">
              <span>em background</span>
              {detectedUrl && (
                <a
                  href={detectedUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="underline hover:text-amber-200"
                >
                  {detectedUrl}
                </a>
              )}
            </span>
          )}

          {visualStatus === 'failed' ? (
            <>
              <span
                className="rounded bg-rose-500/15 px-1.5 py-0.5 text-[11px] text-rose-300"
                data-testid="code-tool-status-badge"
              >
                {typeof exitCode === 'number' && exitCode !== 0 ? `exit ${exitCode}` : 'falhou'}
              </span>
              {event.errorCode && (
                <span
                  className="rounded bg-rose-500/15 px-1.5 py-0.5 font-mono text-[11px] text-rose-300"
                  data-testid="code-tool-error-code"
                >
                  {event.errorCode}
                </span>
              )}
            </>
          ) : isCancelled ? (
            <span
              className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[11px] text-[var(--text-muted)]"
              data-testid="code-tool-status-badge"
            >
              cancelado
            </span>
          ) : (
            (toolName === 'run_command' || toolName === 'bash') &&
            exitCode !== undefined &&
            exitCode !== null && (
              <span
                className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[11px] text-emerald-300"
                data-testid="code-tool-status-badge"
              >
                exit {exitCode}
              </span>
            )
          )}
        </div>

        {hasDiffHunks && (
          <span className="text-[11px] text-[var(--text-muted)]" aria-hidden="true">
            {diffOpen ? '▾' : '▸'}
          </span>
        )}
      </div>

      {hasDiffHunks && diffOpen && (
        <div
          className="border-t border-[var(--border-subtle)] bg-black/25 px-2 py-1.5 text-[12px] font-mono leading-5 overflow-x-auto"
          data-testid="code-tool-diff-view"
        >
          <div data-testid="code-tool-diff">
            {visibleHunks.map((hunk, hunkIdx) => {
              const oldStart = hunk.oldStart ?? hunk.old_start ?? 1
              const oldLines = hunk.oldLines ?? hunk.old_lines ?? 0
              const newStart = hunk.newStart ?? hunk.new_start ?? 1
              const newLines = hunk.newLines ?? hunk.new_lines ?? 0
              let oldLineNo = oldStart
              let newLineNo = newStart
              return (
                <div key={hunkIdx} className="mb-1 last:mb-0">
                  <div className="px-2 py-0.5 text-[11px] text-[var(--text-muted)] select-none">
                    {`@@ -${oldStart},${oldLines} +${newStart},${newLines} @@`}
                  </div>
                  {hunk.lines.map((line, lineIdx) => {
                    const isAdd = line.startsWith('+')
                    const isDel = line.startsWith('-')
                    const oldNo = isAdd ? '' : String(oldLineNo++)
                    const newNo = isDel ? '' : String(newLineNo++)
                    const rowClass = isAdd
                      ? 'bg-emerald-500/15 text-emerald-300'
                      : isDel
                        ? 'bg-rose-500/15 text-rose-300'
                        : 'text-[var(--text-secondary)]'
                    const wrapClass = isProseFile
                      ? 'whitespace-pre-wrap break-words'
                      : 'whitespace-pre'
                    return (
                      <div
                        key={lineIdx}
                        className={`flex items-baseline gap-2 px-2 ${wrapClass} ${rowClass}`}
                        data-diff-line={isAdd ? 'add' : isDel ? 'del' : 'ctx'}
                      >
                        <span className="w-7 shrink-0 select-none text-right text-[11px] opacity-50">
                          {oldNo}
                        </span>
                        <span className="w-7 shrink-0 select-none text-right text-[11px] opacity-50">
                          {newNo}
                        </span>
                        <span className={`flex-1 ${isProseFile ? 'min-w-0 break-words' : ''}`}>
                          {line}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )
            })}
          </div>
          {hasLongDiff && (
            <button
              type="button"
              onClick={() => setDiffExpanded((prev) => !prev)}
              data-testid="code-tool-diff-expand"
              className="mt-1.5 inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium text-[var(--accent-primary)] hover:underline"
            >
              {diffExpanded
                ? 'Recolher diff'
                : `Mostrar todas as ${totalDiffLines} linhas (+${addedLines ?? 0} -${removedLines ?? 0})`}
            </button>
          )}
        </div>
      )}

      {(toolName === 'run_command' || toolName === 'bash') &&
        (showCommandSubheader || visibleTerminalLines.length > 0) && (
          <div
            className="border-t border-[var(--border-subtle)] bg-black/30 px-3 py-2 font-mono text-[12px] leading-5"
            data-testid="code-tool-terminal-output"
          >
            {showCommandSubheader && (
              <div className="mb-1 text-[var(--text-muted)] select-all">$ {bashCommand}</div>
            )}
            {visibleTerminalLines.length > 0 && (
              <pre className="m-0 overflow-x-auto whitespace-pre-wrap break-words text-[var(--text-secondary)]">
                {visibleTerminalLines.join('\n')}
              </pre>
            )}
            {hasLongTerminalOutput && (
              <button
                type="button"
                onClick={() => setTerminalExpanded((prev) => !prev)}
                className="mt-1.5 inline-flex items-center gap-1 text-[11px] font-medium text-[var(--accent-primary)] hover:underline"
              >
                {terminalExpanded
                  ? 'Recolher'
                  : `Mostrar saída completa (${terminalLines.length} linhas)`}
              </button>
            )}
          </div>
        )}

      {errorDetailText && (
        <div
          className="border-t border-[var(--border-subtle)] bg-rose-500/10 px-3 py-2 font-mono text-[12px] leading-5 text-rose-200 whitespace-pre-wrap break-words"
          data-testid="code-tool-error-detail"
        >
          {errorDetailText}
        </div>
      )}
    </div>
  )
}

export function formatToolSignature(
  event: ConversationActivityEvent,
  context?: {
    toolName?: string
    filePath?: string
    bashCommand?: string
    bashDescription?: string
    visualStatus?: ToolVisualStatus
  },
): { verb: string; arg: string } {
  const toolName = context?.toolName ?? event.toolName ?? event.type
  const rawFilePath = event.filePath ?? event.path ?? event.diff?.path ?? ''
  const filePath =
    context?.filePath ??
    (rawFilePath || (toolName === 'read_file' ? extractFilePathFromSummary(event.summary) : ''))
  const bashCommand = context?.bashCommand ?? event.bashOutput?.command ?? event.command ?? ''
  const bashDescription = context?.bashDescription ?? event.bashOutput?.description ?? event.description ?? ''
  const visualStatus = context?.visualStatus ?? resolveVisualStatus(event)
  const isFailed = visualStatus === 'failed'
  const cleanLabel = isReusedReadText(event.label) ? '' : event.label

  switch (toolName) {
    case 'read_file': {
      const cleanSummary = isReusedReadText(event.summary)
        ? extractFilePathFromSummary(event.summary)
        : event.summary
      return {
        verb: 'Read',
        arg: filePath || cleanLabel || (isFailed ? 'arquivo' : cleanSummary || 'arquivo'),
      }
    }
    case 'edit_file':
      return {
        verb: 'Update',
        arg: filePath || cleanLabel || (isFailed ? 'arquivo' : event.summary || 'arquivo'),
      }
    case 'write_file':
      return {
        verb: 'Write',
        arg: filePath || cleanLabel || (isFailed ? 'arquivo' : event.summary || 'arquivo'),
      }
    case 'notebook_edit':
      return {
        verb: 'NotebookEdit',
        arg: filePath || cleanLabel || (isFailed ? 'arquivo' : event.summary || 'arquivo'),
      }
    case 'run_command':
    case 'bash': {
      const summaryCmd = event.summary?.startsWith('$')
        ? event.summary.replace(/^\$\s*/, '').trim()
        : ''
      return {
        verb: 'Bash',
        arg: isFailed
          ? bashCommand || bashDescription || summaryCmd || cleanLabel || 'comando'
          : bashDescription || bashCommand || cleanLabel || summaryCmd || event.summary || 'comando',
      }
    }
    case 'search_files':
      return {
        verb: 'Grep',
        arg: cleanLabel || event.description || filePath || (isFailed ? 'busca' : event.summary || ''),
      }
    case 'glob_files':
    case 'list_directory':
    case 'list_files': {
      const rawArg = cleanLabel || filePath || event.description || (isFailed ? '.' : event.summary || '.')
      const normalizedArg = rawArg.trim() === '/' ? '.' : rawArg
      return {
        verb: 'Glob',
        arg: normalizedArg,
      }
    }
    case 'todo_write':
    case 'task_create':
    case 'task_update': {
      const count = event.todos?.length
      const arg =
        count !== undefined
          ? `${count} ${count === 1 ? 'tarefa' : 'tarefas'}`
          : cleanLabel || event.summary || 'tarefas'
      return { verb: 'TodoWrite', arg }
    }
    case 'run_subagent': {
      const baseArg = event.agentName || cleanLabel || event.summary || 'subagente'
      const rawModel =
        typeof event.payload?.model_id === 'string' && event.payload.model_id.trim()
          ? event.payload.model_id.trim()
          : typeof event.payload?.subagent_type === 'string' && event.payload.subagent_type.trim()
            ? event.payload.subagent_type.trim()
            : ''
      const modelSuffix = rawModel
        ? rawModel.includes('/') && !rawModel.startsWith('~')
          ? `~${rawModel}`
          : rawModel
        : ''
      return {
        verb: 'Task',
        arg: modelSuffix ? `${baseArg} · ${modelSuffix}` : baseArg,
      }
    }
    case 'verify_project':
    case 'verify_frontend':
      return {
        verb: 'Verify',
        arg: cleanLabel || filePath || 'projeto',
      }
    case 'enter_plan_mode':
      return { verb: 'EnterPlanMode', arg: '' }
    case 'exit_plan_mode':
      return { verb: 'ExitPlanMode', arg: '' }
    default:
      return {
        verb: toolName,
        arg: cleanLabel || filePath || (isFailed ? '' : event.summary || ''),
      }
  }
}

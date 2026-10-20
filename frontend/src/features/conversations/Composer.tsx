import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'
import type { CodePermissionMode } from '../../api/codeMode'
import type { PluginCommand } from '../../api/plugins'
import type { CatalogCommand } from '../../api/sessionTools'
import type { BuiltinCommand } from '../commands/builtins'
import { PENDING, useKeybindings } from '../keybindings/engine'
import type { SkillSummary } from '../../api/skills'
import type { CodeModeChoice, ConversationMode } from '../../api/conversations'
import type { TodoItem } from './activityTypes'
import { AttachmentChips, type ComposerAttachment } from './AttachmentChips'
import { CommandPicker, slashItems } from './CommandPicker'
import { applyFileMention, detectFileMention, FileMentionPicker } from './FileMentionPicker'
import { TodoBar } from './TodoBar'
import {
  SAFE_PERMISSION_CYCLE,
  SessionContextPopover,
  formatPermissionModePill,
  formatPermissionModeTitle,
  type SessionContextPopoverProps,
} from './SessionContextPopover'

export { formatPermissionModePill, formatPermissionModeTitle }

export type ComposerProps = {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  onStop?: () => void
  running?: boolean
  disabled?: boolean
  placeholder?: string
  /** Rendered inside the composer, under the input: provider/model pickers. */
  settings?: ReactNode
  hint?: string
  error?: string | null
  autoFocus?: boolean
  /** Shown under the composer as a persistent explanation, not an error. */
  notice?: string | null
  /** Bumped by the owner to pull focus back after a failed submit. */
  focusSignal?: number
  /**
   * Blocks sending while leaving the text editable. A person whose session
   * needs refreshing should still be able to write and keep their draft.
   */
  canSend?: boolean
  /** Files chosen, dropped, or pasted, waiting to be sent alongside the text. */
  attachments?: ComposerAttachment[]
  onAttach?: (files: File[]) => void
  onRemoveAttachment?: (id: string) => void
  /** Active plugin commands offered by the `/` picker. */
  commands?: PluginCommand[]
  /** Available skills offered alongside plugin commands by the `/` picker. */
  skills?: SkillSummary[]
  /** W7: Orin's built-in commands the chat implements, and the folder's own skills and commands. */
  builtinCommands?: readonly BuiltinCommand[]
  catalogCommands?: readonly CatalogCommand[]
  /** W7 chat:sendNow — interrupt the running turn and send this text. */
  onSendNow?: () => void
  /** W7 chat:modelPicker. */
  onOpenModelPicker?: () => void
  /** W7: picking a built-in that takes no arguments runs it at once (as Claude Code does). */
  onRunCommand?: (text: string) => void
  codeMode?: CodeModeChoice
  onCodeModeChange?: (value: CodeModeChoice) => void
  /** Compact segmented mode switch (`[Chat | >_ Code]`) rendered in the composer bar on Home. */
  modeSwitch?: { mode: 'chat' | 'code'; onModeChange: (mode: 'chat' | 'code') => void }
  /** Unified progressive-disclosure popover for workspace, permission, worktree, and project. */
  sessionContext?: SessionContextPopoverProps
  /** When true, hides the Chat/Code mode toggle button (e.g. inside an active conversation). */
  hideCodeModeToggle?: boolean
  /** Immutable mode of an existing conversation ('chat' | 'code'). */
  conversationMode?: ConversationMode
  codePermissionMode?: CodePermissionMode
  onCodePermissionModeChange?: (value: CodePermissionMode) => void
  /** Live TodoWrite / Task checklist pinned directly above the composer input. */
  todos?: TodoItem[]
  /** Workspace file paths offered by the `@` mention picker. */
  workspaceFiles?: string[]
  /** Optional async lookup for workspace files matching the typed `@query`. */
  onSearchWorkspaceFiles?: (query: string) => Promise<string[]>
  /** Whether Git Worktree isolation is active for this Code session. */
  worktreeIsolated?: boolean
  onWorktreeIsolatedChange?: (isolated: boolean) => void
  worktreeBranch?: string | null
}

const MAX_ROWS_HEIGHT = 260

/**
 * The single place a person types.
 *
 * Provider and model live inside it as small, quiet controls rather than as
 * separate form fields, because the message is the action and the configuration
 * is not. Send becomes Stop while a turn runs, in place, so the control the user
 * is already looking at is the one that ends the run.
 */
export function Composer({
  value, onChange, onSubmit, onStop, running = false, disabled = false,
  placeholder = 'Descreva o que você precisa…', settings, hint, error, autoFocus, notice, focusSignal = 0, canSend = true,
  attachments = [], onAttach = () => {}, onRemoveAttachment = () => {}, commands = [], skills = [],
  builtinCommands = [], catalogCommands = [], onSendNow, onOpenModelPicker, onRunCommand,
  codeMode = 'auto', onCodeModeChange, modeSwitch, sessionContext, hideCodeModeToggle = false, conversationMode,
  codePermissionMode, onCodePermissionModeChange, todos,
  workspaceFiles = [], onSearchWorkspaceFiles,
  worktreeIsolated, onWorktreeIsolatedChange, worktreeBranch = null,
}: ComposerProps) {
  const effectiveCodeMode: CodeModeChoice = modeSwitch?.mode ?? conversationMode ?? codeMode
  const showCodeModeToggle = Boolean(!modeSwitch && onCodeModeChange && !hideCodeModeToggle && conversationMode === undefined)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [focused, setFocused] = useState(false)
  const [caretPosition, setCaretPosition] = useState<number | undefined>(undefined)
  const [internalPermissionMode, setInternalPermissionMode] = useState<CodePermissionMode>(
    sessionContext?.permissionMode ?? codePermissionMode ?? (conversationMode === 'code' ? 'accept_edits' : 'ask_permissions'),
  )
  const [internalWorktreeIsolated, setInternalWorktreeIsolated] = useState(false)
  const activePermissionMode = sessionContext?.permissionMode ?? codePermissionMode ?? internalPermissionMode
  const activeWorktreeIsolated = sessionContext?.worktreeIsolated ?? worktreeIsolated ?? internalWorktreeIsolated
  const reduced = useReducedMotion()

  function changePermissionMode(next: CodePermissionMode) {
    setInternalPermissionMode(next)
    onCodePermissionModeChange?.(next)
    sessionContext?.onPermissionModeChange?.(next)
  }

  function stepPermissionMode() {
    if (activePermissionMode === 'auto' || activePermissionMode === 'dont_ask') {
      changePermissionMode('ask_permissions')
      return
    }
    const idx = SAFE_PERMISSION_CYCLE.indexOf(activePermissionMode)
    const next = SAFE_PERMISSION_CYCLE[(idx === -1 ? 0 : idx + 1) % SAFE_PERMISSION_CYCLE.length]
    changePermissionMode(next)
  }

  function changeWorktreeIsolated(next: boolean) {
    setInternalWorktreeIsolated(next)
    onWorktreeIsolatedChange?.(next)
    sessionContext?.onWorktreeIsolatedChange?.(next)
  }

  // The picker only claims the keyboard while the message is exactly a
  // command being typed from the very start. Anything else — a path, a
  // regex, a second word — leaves Enter-to-send alone.
  const commandQuery = /^\/[^\s/]*$/.test(value) ? value.slice(1) : null
  const [dismissed, setDismissed] = useState(false)
  // A dismissal (Escape, or picking a command) only applies to the command
  // being typed at that moment; once the person leaves and re-enters command
  // shape, the picker is available again. Adjusted during render, React's
  // documented pattern for this, rather than in an effect.
  const [previousCommandQuery, setPreviousCommandQuery] = useState(commandQuery)
  if (commandQuery !== previousCommandQuery) {
    setPreviousCommandQuery(commandQuery)
    if (commandQuery === null) setDismissed(false)
  }
  const pickerHasMatches = commandQuery !== null && slashItems(commandQuery, commands, skills, builtinCommands, catalogCommands).length > 0
  const pickerOpen = commandQuery !== null && !dismissed && pickerHasMatches

  // `@file` mention autocomplete anywhere in the composer text.
  const activeMention = detectFileMention(value, caretPosition)
  const mentionQuery = activeMention?.query ?? null
  const [mentionDismissed, setMentionDismissed] = useState(false)
  const [previousMentionKey, setPreviousMentionKey] = useState<string | null>(null)
  const currentMentionKey = activeMention ? `${activeMention.startIndex}:${activeMention.query}` : null
  if (currentMentionKey !== previousMentionKey) {
    setPreviousMentionKey(currentMentionKey)
    if (currentMentionKey === null || !previousMentionKey || !currentMentionKey.startsWith(`${activeMention?.startIndex}:`)) {
      setMentionDismissed(false)
    } else {
      setMentionDismissed(false)
    }
  }

  const [fetchedMentionResult, setFetchedMentionResult] = useState<{ query: string; items: string[] } | null>(null)

  useEffect(() => {
    if (mentionQuery === null || mentionDismissed || !onSearchWorkspaceFiles) return
    let cancelled = false
    onSearchWorkspaceFiles(mentionQuery)
      .then((items) => {
        if (!cancelled) setFetchedMentionResult({ query: mentionQuery, items })
      })
      .catch(() => {
        if (!cancelled) setFetchedMentionResult({ query: mentionQuery, items: [] })
      })
    return () => {
      cancelled = true
    }
  }, [mentionQuery, mentionDismissed, onSearchWorkspaceFiles])

  const mentionLoading = Boolean(
    onSearchWorkspaceFiles &&
    mentionQuery !== null &&
    !mentionDismissed &&
    fetchedMentionResult?.query !== mentionQuery,
  )
  const fetchedFiles = fetchedMentionResult?.query === mentionQuery ? fetchedMentionResult.items : []
  const availableMentionFiles = onSearchWorkspaceFiles && fetchedFiles.length > 0 ? fetchedFiles : workspaceFiles
  const mentionHasMatches = mentionQuery !== null && (
    mentionLoading || availableMentionFiles.some((file) => {
      const needle = mentionQuery.trim().toLowerCase()
      return !needle || file.toLowerCase().includes(needle)
    })
  )
  const mentionPickerOpen = activeMention !== null && !pickerOpen && !mentionDismissed && mentionHasMatches

  useEffect(() => {
    const element = textareaRef.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, MAX_ROWS_HEIGHT)}px`
  }, [value])

  useEffect(() => {
    if (autoFocus) textareaRef.current?.focus()
  }, [autoFocus])

  // A failed submit must not leave the keyboard stranded on a disabled button:
  // focus returns to the text the person still needs to edit or resend.
  useEffect(() => {
    if (focusSignal > 0) textareaRef.current?.focus()
  }, [focusSignal])

  function submit(event: FormEvent) {
    event.preventDefault()
    if (running || disabled || !canSend || (!value.trim() && attachments.length === 0)) return
    onSubmit()
  }

  const { match } = useKeybindings()

  function insertAtCaret(text: string) {
    const element = textareaRef.current
    const start = element?.selectionStart ?? value.length
    const end = element?.selectionEnd ?? value.length
    onChange(`${value.slice(0, start)}${text}${value.slice(end)}`)
    requestAnimationFrame(() => element?.setSelectionRange(start + text.length, start + text.length))
  }

  // W7: the Chat context of the keybinding engine (keybindings.json can rebind every one of these).
  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing) return
    const pickerKeys = pickerOpen && ['Enter', 'Tab', 'ArrowUp', 'ArrowDown', 'Escape'].includes(event.key)
    if (pickerKeys) return
    if (mentionPickerOpen && ['Enter', 'Tab', 'ArrowUp', 'ArrowDown', 'Escape'].includes(event.key)) return
    const action = match(event.nativeEvent, ['Chat'])
    if (action === PENDING) { event.preventDefault(); return }
    const handled = (() => {
      switch (action) {
        case 'chat:cycleMode':
          if (!(effectiveCodeMode === 'code' || Boolean(sessionContext) || Boolean(onCodePermissionModeChange))) return false
          stepPermissionMode()
          return true
        case 'chat:cancel':
          if (!running || !onStop) return false
          onStop()
          return true
        case 'chat:submit':
          if (running || !canSend) return true
          if (value.trim() || attachments.length > 0) onSubmit()
          return true
        case 'chat:newline':
          // Shift+Enter already types a line break; any other key bound to it inserts one.
          if (event.key === 'Enter' && event.shiftKey) return false
          insertAtCaret('\n')
          return true
        case 'chat:sendNow':
          if (!value.trim()) return true
          if (running && onSendNow) onSendNow()
          else if (!running && canSend) onSubmit()
          return true
        case 'chat:modelPicker':
          if (!onOpenModelPicker) return false
          onOpenModelPicker()
          return true
        case 'chat:clearInput':
          onChange('')
          return true
        default:
          return false
      }
    })()
    if (handled) {
      event.preventDefault()
      event.stopPropagation()
    }
  }

  function onPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.clipboardData?.files ?? [])
    if (files.length > 0) {
      event.preventDefault()
      onAttach(files)
    }
  }

  function onDrop(event: DragEvent<HTMLFormElement>) {
    const files = Array.from(event.dataTransfer?.files ?? [])
    if (files.length > 0) {
      event.preventDefault()
      onAttach(files)
    }
  }

  function onDragOver(event: DragEvent<HTMLFormElement>) {
    if (event.dataTransfer?.types?.includes('Files')) event.preventDefault()
  }

  const sendDisabled = Boolean(disabled || !canSend || (!value.trim() && attachments.length === 0))

  return (
    <form className={`composer${focused ? ' is-focused' : ''}${running ? ' is-running' : ''}`} onSubmit={submit} onDrop={onDrop} onDragOver={onDragOver}>
      {todos && todos.length > 0 && <TodoBar todos={todos} isTurnRunning={Boolean(running)} className="mb-2" />}
      <div className="composer__surface">
        <label className="visually-hidden" htmlFor="composer-message">Mensagem</label>
        <AttachmentChips items={attachments} onRemove={onRemoveAttachment} />
        {pickerOpen && (
          <CommandPicker
            commands={commands}
            skills={skills}
            builtins={builtinCommands}
            catalog={catalogCommands}
            query={commandQuery ?? ''}
            onSelect={(token) => {
              const builtin = builtinCommands.find((item) => item.name === token)
              setDismissed(true)
              if (builtin && !builtin.argumentHint && onRunCommand) { onRunCommand(`/${token}`); return }
              onChange(`/${token} `)
            }}
            onDismiss={() => setDismissed(true)}
          />
        )}
        {mentionPickerOpen && activeMention && (
          <FileMentionPicker
            files={availableMentionFiles}
            query={activeMention.query}
            loading={mentionLoading}
            onSelect={(filePath) => {
              const nextValue = applyFileMention(value, activeMention, filePath)
              onChange(nextValue)
              setMentionDismissed(true)
            }}
            onDismiss={() => setMentionDismissed(true)}
          />
        )}
        <textarea
          id="composer-message"
          ref={textareaRef}
          className="composer__input"
          value={value}
          rows={1}
          placeholder={placeholder}
          disabled={disabled}
          onChange={(event) => {
            setCaretPosition(event.target.selectionStart ?? event.target.value.length)
            onChange(event.target.value)
          }}
          onSelect={(event) => {
            const target = event.currentTarget
            setCaretPosition(target.selectionStart ?? target.value.length)
          }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
        />
        <div className="composer__bar">
          <div className="composer__settings">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              onChange={(event) => {
                const files = Array.from(event.target.files ?? [])
                if (files.length > 0) onAttach(files)
                event.target.value = ''
              }}
            />
            <button
              type="button"
              className="composer__attach"
              aria-label="Anexar arquivos"
              onClick={() => fileInputRef.current?.click()}
            >
              <svg aria-hidden="true" viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M10.7 4.3 5.4 9.6a2 2 0 1 0 2.8 2.8l5.1-5.1a3.4 3.4 0 1 0-4.8-4.8L3.3 7.7" />
              </svg>
            </button>
            {modeSwitch ? (
              <div
                className="composer__mode-switch"
                role="group"
                aria-label="Modo da conversa"
                data-testid="home-mode-toggle"
              >
                <button
                  type="button"
                  data-testid="home-mode-chat"
                  aria-pressed={modeSwitch.mode === 'chat'}
                  className={`composer__mode-pill${modeSwitch.mode === 'chat' ? ' is-active' : ''}`}
                  onClick={() => modeSwitch.onModeChange('chat')}
                >
                  <span>Chat</span>
                </button>
                <button
                  type="button"
                  data-testid="home-mode-code"
                  aria-pressed={modeSwitch.mode === 'code'}
                  className={`composer__mode-pill composer__mode-pill--code${modeSwitch.mode === 'code' ? ' is-active' : ''}`}
                  onClick={() => modeSwitch.onModeChange('code')}
                >
                  <span aria-hidden="true">&gt;_</span>
                  <span>Orin Code</span>
                </button>
              </div>
            ) : showCodeModeToggle ? (
              <button
                type="button"
                className={`composer__code${effectiveCodeMode === 'code' ? ' is-active' : ''}`}
                aria-pressed={effectiveCodeMode === 'code'}
                aria-label={effectiveCodeMode === 'code' ? 'Desativar Modo Code' : 'Ativar Modo Code'}
                title={effectiveCodeMode === 'code' ? 'Modo Code ativo' : 'Ativar Modo Code'}
                onClick={() => onCodeModeChange?.(effectiveCodeMode === 'code' ? 'auto' : 'code')}
              >
                <span aria-hidden="true">&lt;/&gt;</span>
                <span>Code</span>
              </button>
            ) : null}
            {sessionContext ? (
              <SessionContextPopover
                {...sessionContext}
                mode={sessionContext.mode ?? (effectiveCodeMode === 'code' ? 'code' : 'chat')}
                permissionMode={activePermissionMode}
                onPermissionModeChange={changePermissionMode}
                worktreeIsolated={activeWorktreeIsolated}
                onWorktreeIsolatedChange={changeWorktreeIsolated}
                worktreeBranch={sessionContext.worktreeBranch ?? worktreeBranch}
                disabled={disabled}
              />
            ) : effectiveCodeMode === 'code' ? (
              <>
                <button
                  type="button"
                  className="composer__permission-pill composer__permission-mode inline-flex items-center gap-1.5 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-raised)]/80 px-2.5 py-1 font-mono text-[11px] font-medium text-[var(--text-secondary)] hover:border-[var(--accent-primary)] hover:text-[var(--text-primary)] transition-colors"
                  data-testid="code-permission-mode-pill"
                  data-permission-mode={activePermissionMode}
                  aria-label="Modo de permissão do Code"
                  title={formatPermissionModeTitle(activePermissionMode)}
                  onClick={() => stepPermissionMode()}
                >
                  {formatPermissionModePill(activePermissionMode)}
                </button>
                <select
                  className="visually-hidden"
                  data-testid="code-permission-mode-select"
                  aria-label="Selecionar modo de permissão do Code"
                  tabIndex={-1}
                  value={activePermissionMode}
                  onChange={(event) => changePermissionMode(event.target.value as CodePermissionMode)}
                >
                  <option value="ask_permissions">Pedir permissão</option>
                  <option value="accept_edits">Aceitar edições</option>
                  <option value="plan_mode">Modo Plano</option>
                  <option value="full_autonomy">Sem restrições</option>
                </select>
                <button
                  type="button"
                  className={`composer__worktree${activeWorktreeIsolated ? ' is-active' : ''}`}
                  data-testid="code-worktree-toggle"
                  aria-pressed={activeWorktreeIsolated}
                  aria-label={activeWorktreeIsolated ? 'Desativar Worktree isolado' : 'Ativar Worktree isolado'}
                  title={
                    activeWorktreeIsolated
                      ? `Worktree isolado ativo${worktreeBranch ? ` (${worktreeBranch})` : ''}`
                      : 'Isolar sessão em um Git Worktree (.orin/worktrees/)'
                  }
                  onClick={() => changeWorktreeIsolated(!activeWorktreeIsolated)}
                >
                  <span aria-hidden="true">⑂</span>
                  <span>{activeWorktreeIsolated && worktreeBranch ? worktreeBranch : 'Worktree'}</span>
                </button>
              </>
            ) : null}
            {settings}
          </div>
          <div className="composer__actions">
            {hint && <span className="composer__hint" role="status" aria-live="polite">{hint}</span>}
            <AnimatePresence mode="wait" initial={false}>
              {running ? (
                <motion.button
                  key="stop"
                  type="button"
                  className="composer__action composer__action--stop"
                  onClick={onStop}
                  aria-label="Parar execução"
                  initial={reduced ? false : { scale: 0.7, opacity: 0, rotate: -25 }}
                  animate={{ scale: 1, opacity: 1, rotate: 0 }}
                  exit={reduced ? { opacity: 0 } : { scale: 0.7, opacity: 0, rotate: 25 }}
                  transition={{ duration: 0.18, ease: 'easeOut' }}
                >
                  <span aria-hidden="true" className="composer__stop-glyph" />
                </motion.button>
              ) : (
                <motion.button
                  key="send"
                  type="submit"
                  className="composer__action composer__action--send"
                  aria-label="Enviar mensagem"
                  disabled={sendDisabled}
                  style={{ opacity: sendDisabled ? 0.32 : 1 }}
                  initial={reduced ? false : { scale: 0.7, opacity: 0, rotate: 25 }}
                  animate={{ scale: 1, opacity: sendDisabled ? 0.32 : 1, rotate: 0 }}
                  exit={reduced ? { opacity: 0 } : { scale: 0.7, opacity: 0, rotate: -25 }}
                  transition={{ duration: 0.18, ease: 'easeOut' }}
                >
                  <span aria-hidden="true">↑</span>
                </motion.button>
              )}
            </AnimatePresence>
          </div>
        </div>
      </div>
      {notice && <p className="composer__notice" role="status" aria-live="polite">{notice}</p>}
      {error && <p className="composer__error" role="alert">{error}</p>}
    </form>
  )
}

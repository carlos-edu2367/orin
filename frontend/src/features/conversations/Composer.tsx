import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'
import type { PluginCommand } from '../../api/plugins'
import type { SkillSummary } from '../../api/skills'
import type { CodeModeChoice } from '../../api/conversations'
import { AttachmentChips, type ComposerAttachment } from './AttachmentChips'
import { CommandPicker, commandToken, skillToken } from './CommandPicker'

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
  codeMode?: CodeModeChoice
  onCodeModeChange?: (value: CodeModeChoice) => void
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
  codeMode = 'auto', onCodeModeChange,
}: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [focused, setFocused] = useState(false)
  const reduced = useReducedMotion()

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
  const pickerHasMatches = commandQuery !== null && [
    ...commands.map((command) => ({ token: commandToken(command), description: command.description })),
    ...skills.filter((skill) => skill.available).map((skill) => ({ token: skillToken(skill, commands), description: skill.description })),
  ].some((item) => {
    const needle = commandQuery.trim().toLowerCase()
    return !needle || item.token.toLowerCase().includes(needle) || item.description.toLowerCase().includes(needle)
  })
  const pickerOpen = commandQuery !== null && !dismissed && pickerHasMatches

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

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (pickerOpen && ['Enter', 'ArrowUp', 'ArrowDown', 'Escape'].includes(event.key)) return
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      if (running || !canSend) return
      if (value.trim() || attachments.length > 0) onSubmit()
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

  return (
    <form className={`composer${focused ? ' is-focused' : ''}${running ? ' is-running' : ''}`} onSubmit={submit} onDrop={onDrop} onDragOver={onDragOver}>
      <div className="composer__surface">
        <label className="visually-hidden" htmlFor="composer-message">Mensagem</label>
        <AttachmentChips items={attachments} onRemove={onRemoveAttachment} />
        {pickerOpen && (
          <CommandPicker
            commands={commands}
            skills={skills}
            query={commandQuery ?? ''}
            onSelect={(token) => { onChange(`/${token} `); setDismissed(true) }}
            onDismiss={() => setDismissed(true)}
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
          onChange={(event) => onChange(event.target.value)}
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
            {onCodeModeChange && <button
              type="button"
              className={`composer__code${codeMode === 'code' ? ' is-active' : ''}`}
              aria-pressed={codeMode === 'code'}
              aria-label={codeMode === 'code' ? 'Desativar Modo Code' : 'Ativar Modo Code'}
              title={codeMode === 'code' ? 'Modo Code ativo' : 'Ativar Modo Code'}
              onClick={() => onCodeModeChange(codeMode === 'code' ? 'auto' : 'code')}
            ><span aria-hidden="true">&lt;/&gt;</span><span>Code</span></button>}
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
                  disabled={disabled || !canSend || (!value.trim() && attachments.length === 0)}
                  initial={reduced ? false : { scale: 0.7, opacity: 0, rotate: 25 }}
                  animate={{ scale: 1, opacity: 1, rotate: 0 }}
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

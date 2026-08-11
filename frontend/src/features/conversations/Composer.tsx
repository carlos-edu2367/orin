import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from 'react'

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
}: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [focused, setFocused] = useState(false)
  const reduced = useReducedMotion()

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
    if (running || disabled || !canSend || !value.trim()) return
    onSubmit()
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      if (running || !canSend) return
      if (value.trim()) onSubmit()
    }
  }

  return (
    <form className={`composer${focused ? ' is-focused' : ''}${running ? ' is-running' : ''}`} onSubmit={submit}>
      <div className="composer__surface">
        <label className="visually-hidden" htmlFor="composer-message">Mensagem</label>
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
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
        />
        <div className="composer__bar">
          <div className="composer__settings">{settings}</div>
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
                  disabled={disabled || !canSend || !value.trim()}
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

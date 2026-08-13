import { useState, type FormEvent } from 'react'
import type { UserQuestion } from './activityTypes'

export type UserQuestionAnswer = { id: string; selected: string[]; note: string }

type UserQuestionCardProps = {
  questions: UserQuestion[]
  active: boolean
  submitting?: boolean
  onSubmit: (answers: UserQuestionAnswer[]) => Promise<void>
}

/** A batch is answered atomically so the agent receives one coherent reply. */
export function UserQuestionCard({ questions, active, submitting = false, onSubmit }: UserQuestionCardProps) {
  const [selected, setSelected] = useState<Record<string, string[]>>({})
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)

  function choose(question: UserQuestion, optionId: string, checked: boolean) {
    setSelected((current) => {
      const previous = current[question.id] ?? []
      const next = question.mode === 'single_choice'
        ? (checked ? [optionId] : [])
        : (checked ? [...new Set([...previous, optionId])] : previous.filter((id) => id !== optionId))
      return { ...current, [question.id]: next }
    })
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!active || submitting) return
    setError(null)
    try {
      await onSubmit(questions.map((question) => ({
        id: question.id,
        selected: selected[question.id] ?? [],
        note: (notes[question.id] ?? '').trim(),
      })))
    } catch {
      setError('Não foi possível enviar as respostas. Tente novamente.')
    }
  }

  return (
    <section className="user-question-card" data-active={active} aria-label="Perguntas do agente">
      <header>
        <span aria-hidden="true">?</span>
        <div><strong>O agente precisa da sua decisão</strong><p>Responda o que quiser e acrescente uma observação, se necessário.</p></div>
      </header>
      <form onSubmit={(event) => void submit(event)}>
        {questions.map((question, index) => (
          <fieldset key={question.id} disabled={!active || submitting}>
            <legend>{index + 1}. {question.question}</legend>
            {question.mode === 'text' ? (
              <textarea
                value={notes[question.id] ?? ''}
                onChange={(event) => setNotes((current) => ({ ...current, [question.id]: event.target.value }))}
                placeholder={question.placeholder || 'Escreva sua resposta (opcional)'}
                maxLength={1200}
              />
            ) : (
              <div className="user-question-card__options">
                {question.options.map((option) => {
                  const chosen = (selected[question.id] ?? []).includes(option.id)
                  return <label key={option.id}>
                    <input type={question.mode === 'checkbox' ? 'checkbox' : 'radio'} name={question.id} checked={chosen} onChange={(event) => choose(question, option.id, event.target.checked)} />
                    <span>{option.label}</span>
                  </label>
                })}
              </div>
            )}
            {question.mode !== 'text' && <textarea value={notes[question.id] ?? ''} onChange={(event) => setNotes((current) => ({ ...current, [question.id]: event.target.value }))} placeholder={question.placeholder || 'Observação adicional (opcional)'} maxLength={1200} />}
          </fieldset>
        ))}
        {active ? <button className="user-question-card__submit" type="submit" disabled={submitting}>{submitting ? 'Enviando…' : 'Enviar respostas'}</button> : <p className="user-question-card__answered">Respondida</p>}
        {error && <p className="user-question-card__error" role="alert">{error}</p>}
      </form>
    </section>
  )
}

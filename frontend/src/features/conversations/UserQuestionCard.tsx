import { useState, type FormEvent } from 'react'
import type { UserQuestion } from './activityTypes'

export type UserQuestionAnswer = { id: string; selected: string[]; note: string }

type UserQuestionCardProps = {
  questions: UserQuestion[]
  active: boolean
  submitting?: boolean
  onSubmit: (answers: UserQuestionAnswer[]) => Promise<void>
}

type AnswerMap = Record<string, UserQuestionAnswer>

/**
 * Presents a batch as a local wizard. The final answer is still sent atomically
 * because the waiting-user turn resumes only after the complete batch arrives.
 */
export function UserQuestionCard({ questions, active, submitting = false, onSubmit }: UserQuestionCardProps) {
  const [answers, setAnswers] = useState<AnswerMap>({})
  const [currentIndex, setCurrentIndex] = useState(0)
  const [error, setError] = useState<string | null>(null)

  if (questions.length === 0) return null

  const current = questions[Math.min(currentIndex, questions.length - 1)]
  const currentAnswer = answers[current.id] ?? emptyAnswer(current.id)
  const isLast = currentIndex === questions.length - 1

  function choose(question: UserQuestion, optionId: string, checked: boolean) {
    setAnswers((currentAnswers) => {
      const previous = currentAnswers[question.id] ?? emptyAnswer(question.id)
      const next = question.mode === 'single_choice'
        ? (checked ? [optionId] : [])
        : (checked ? [...new Set([...previous.selected, optionId])] : previous.selected.filter((id) => id !== optionId))
      return { ...currentAnswers, [question.id]: { ...previous, selected: next } }
    })
  }

  function setNote(questionId: string, note: string) {
    setAnswers((currentAnswers) => ({
      ...currentAnswers,
      [questionId]: { ...(currentAnswers[questionId] ?? emptyAnswer(questionId)), note },
    }))
  }

  function edit(index: number) {
    setError(null)
    setCurrentIndex(index)
  }

  async function advance(event: FormEvent) {
    event.preventDefault()
    if (!active || submitting) return
    setError(null)

    if (!isLast) {
      setCurrentIndex((index) => index + 1)
      return
    }

    try {
      await onSubmit(questions.map((question) => answers[question.id] ?? emptyAnswer(question.id)))
    } catch {
      setError('Não foi possível enviar as respostas. Tente novamente.')
    }
  }

  return (
    <section className="user-question-card" data-active={active} aria-label="Perguntas do agente">
      <header className="user-question-card__header">
        <span className="user-question-card__glyph" aria-hidden="true">?</span>
        <div>
          <strong>O agente precisa da sua decisão</strong>
          <p>{active ? 'Uma pergunta por vez. Você pode voltar e ajustar qualquer resposta.' : 'As respostas foram enviadas para o agente.'}</p>
        </div>
      </header>

      {active ? (
        <>
          <div className="user-question-card__progress" aria-live="polite">
            <span>Pergunta {currentIndex + 1} de {questions.length}</span>
            <div aria-hidden="true">{questions.map((question, index) => <i key={question.id} className={index < currentIndex ? 'is-complete' : index === currentIndex ? 'is-current' : ''} />)}</div>
          </div>

          {questions.slice(0, currentIndex).map((question, index) => <AnsweredQuestion key={question.id} question={question} answer={answers[question.id] ?? emptyAnswer(question.id)} index={index} onEdit={() => edit(index)} />)}

          <form className="user-question-card__step" onSubmit={(event) => void advance(event)}>
            <fieldset disabled={submitting}>
              <legend><span>{String(currentIndex + 1).padStart(2, '0')}</span>{current.question}</legend>
              {current.mode === 'text' ? (
                <textarea
                  value={currentAnswer.note}
                  onChange={(event) => setNote(current.id, event.target.value)}
                  placeholder={current.placeholder || 'Escreva sua resposta (opcional)'}
                  maxLength={1200}
                  autoFocus
                />
              ) : (
                <div className="user-question-card__options">
                  {current.options.map((option) => {
                    const chosen = currentAnswer.selected.includes(option.id)
                    return <label key={option.id} className={chosen ? 'is-selected' : ''}>
                      <input type={current.mode === 'checkbox' ? 'checkbox' : 'radio'} name={current.id} checked={chosen} onChange={(event) => choose(current, option.id, event.target.checked)} />
                      <span>{option.label}</span>
                    </label>
                  })}
                </div>
              )}
              {current.mode !== 'text' && <textarea value={currentAnswer.note} onChange={(event) => setNote(current.id, event.target.value)} placeholder={current.placeholder || 'Observação adicional (opcional)'} maxLength={1200} />}
            </fieldset>
            <footer className="user-question-card__actions">
              {currentIndex > 0 && <button className="user-question-card__back" type="button" onClick={() => setCurrentIndex((index) => index - 1)}>← Voltar</button>}
              <button className="user-question-card__submit" type="submit" disabled={submitting}>{submitting ? 'Enviando…' : isLast ? 'Enviar respostas' : 'Continuar →'}</button>
            </footer>
            {error && <p className="user-question-card__error" role="alert">{error}</p>}
          </form>
        </>
      ) : (
        <p className="user-question-card__answered">Respostas enviadas · o agente continuará com este contexto.</p>
      )}
    </section>
  )
}

function AnsweredQuestion({ question, answer, index, onEdit }: { question: UserQuestion; answer: UserQuestionAnswer; index: number; onEdit: () => void }) {
  const selections = answer.selected.map((id) => question.options.find((option) => option.id === id)?.label).filter((label): label is string => Boolean(label))
  const summaryParts = [...selections]
  if (answer.note) summaryParts.push(`Obs.: ${answer.note}`)
  const summary = summaryParts.join(' · ') || 'Sem resposta adicional'
  return <article className="user-question-card__answered-step">
    <div><span>{String(index + 1).padStart(2, '0')}</span><strong>{question.question}</strong><p>{summary}</p></div>
    <button type="button" aria-label={`Editar resposta: ${question.question}`} onClick={onEdit}>Editar</button>
  </article>
}

function emptyAnswer(id: string): UserQuestionAnswer {
  return { id, selected: [], note: '' }
}

import { useState } from 'react'
import type { TodoItem } from './activityTypes'

export type TodoBarProps = {
  todos: TodoItem[]
  isTurnRunning?: boolean
  className?: string
}

export function TodoBar({ todos, isTurnRunning = true, className = '' }: TodoBarProps) {
  const [userToggled, setUserToggled] = useState<boolean | null>(null)

  if (!todos || todos.length === 0) {
    return null
  }

  const defaultExpanded = isTurnRunning && todos.length <= 4
  const isExpanded = userToggled !== null ? userToggled : defaultExpanded
  const completedCount = todos.filter((item) => item.status === 'completed').length
  const inProgressItem = todos.find((item) => item.status === 'in_progress')
  const activeText = inProgressItem
    ? inProgressItem.activeForm || inProgressItem.active_form || inProgressItem.content
    : null
  const summaryLabel = `Tarefas (${completedCount}/${todos.length} concluídas)`

  return (
    <section
      className={`todo-bar rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-raised)] px-3 py-2 text-[12px] text-[var(--text-primary)] ${className}`.trim()}
      data-testid="todo-bar"
      aria-label="Lista de tarefas da sessão"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {inProgressItem && isTurnRunning ? (
            <>
              <span
                className="inline-block h-2 w-2 shrink-0 rounded-full bg-amber-400 animate-pulse"
                aria-hidden="true"
              />
              <span className="truncate font-medium text-[var(--text-primary)]">{activeText}</span>
              <span className="shrink-0 font-mono text-[11px] text-[var(--text-muted)]">
                · {summaryLabel}
              </span>
            </>
          ) : (
            <span className="font-medium text-[var(--text-secondary)]">{summaryLabel}</span>
          )}
        </div>
        <button
          type="button"
          onClick={() => setUserToggled(!isExpanded)}
          aria-expanded={isExpanded}
          className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] text-[var(--text-muted)] hover:bg-[var(--surface-hover)] hover:text-[var(--text-primary)] transition-colors"
        >
          {isExpanded ? 'Recolher ▴' : 'Expandir ▾'}
        </button>
      </div>

      {isExpanded && (
        <ul className="mt-2 max-h-36 space-y-1 overflow-y-auto border-t border-[var(--border-subtle)] pt-2 font-mono text-[12px]">
          {todos.map((todo, idx) => {
            const key = todo.id || `${idx}-${todo.content}`
            if (todo.status === 'completed') {
              return (
                <li key={key} className="flex items-baseline gap-2" data-todo-status="completed">
                  <span className="shrink-0 text-emerald-400" aria-hidden="true">
                    [✓]
                  </span>
                  <span className="line-through text-[var(--text-muted)]">{todo.content}</span>
                </li>
              )
            }
            if (todo.status === 'in_progress') {
              const display = todo.activeForm || todo.active_form || todo.content
              return (
                <li key={key} className="flex items-baseline gap-2" data-todo-status="in_progress">
                  <span className="shrink-0 text-amber-400" aria-hidden="true">
                    [•]
                  </span>
                  <span className="font-semibold text-[var(--text-primary)]">{display}</span>
                </li>
              )
            }
            return (
              <li key={key} className="flex items-baseline gap-2" data-todo-status="pending">
                <span className="shrink-0 text-[var(--text-muted)]" aria-hidden="true">
                  [ ]
                </span>
                <span className="text-[var(--text-secondary)]">{todo.content}</span>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

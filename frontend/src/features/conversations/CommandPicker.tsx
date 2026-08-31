import { useEffect, useMemo, useState } from 'react'
import type { PluginCommand } from '../../api/plugins'
import type { SkillSummary } from '../../api/skills'

export type CommandPickerProps = {
  commands: PluginCommand[]
  skills?: SkillSummary[]
  query: string
  onSelect: (token: string) => void
  onDismiss: () => void
}

/** The name a person types to reach a command: bare when unique, qualified when not. */
export function commandToken(command: PluginCommand): string {
  return command.qualified ? `${command.plugin_id}:${command.slug}` : command.slug
}

type SlashItem = {
  id: string
  token: string
  kind: 'command' | 'skill'
  description: string
  hint?: string
}

export function skillToken(skill: SkillSummary, commands: PluginCommand[]): string {
  // Preserve existing plugin-command precedence for typed bare names. A skill
  // with the same name remains directly available through an explicit prefix.
  return commands.some((command) => commandToken(command) === skill.id) ? `skill:${skill.id}` : skill.id
}

/**
 * The menu that opens on a leading `/`.
 *
 * It owns the arrow keys, Enter, and Escape only while it is on screen, so the
 * composer's own Enter-to-send is untouched whenever the picker is closed.
 */
export function CommandPicker({ commands, skills = [], query, onSelect, onDismiss }: CommandPickerProps) {
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const items: SlashItem[] = [
      ...commands.map((item) => ({
        id: item.command_id,
        token: commandToken(item),
        kind: 'command' as const,
        description: item.description,
        hint: item.argument_hint,
      })),
      ...skills.filter((skill) => skill.available).map((skill) => ({
        id: `skill:${skill.id}`,
        token: skillToken(skill, commands),
        kind: 'skill' as const,
        description: skill.description,
      })),
    ]
    return items
      .filter((item) => !needle || item.token.toLowerCase().includes(needle) || item.description.toLowerCase().includes(needle))
      .slice(0, 50)
  }, [commands, skills, query])
  const [highlighted, setHighlighted] = useState(0)
  // Reset the highlight when the query changes, adjusting state during render
  // (React's documented pattern for this) rather than in an effect, which
  // would cost an extra commit.
  const [queryAtLastReset, setQueryAtLastReset] = useState(query)
  if (query !== queryAtLastReset) {
    setQueryAtLastReset(query)
    setHighlighted(0)
  }

  useEffect(() => {
    if (matches.length === 0) return
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'ArrowDown') { event.preventDefault(); setHighlighted((index) => (index + 1) % matches.length) }
      else if (event.key === 'ArrowUp') { event.preventDefault(); setHighlighted((index) => (index - 1 + matches.length) % matches.length) }
      else if (event.key === 'Enter') { event.preventDefault(); onSelect(matches[highlighted]!.token) }
      else if (event.key === 'Escape') { event.preventDefault(); onDismiss() }
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [matches, highlighted, onSelect, onDismiss])

  if (matches.length === 0) return null

  return (
    <ul className="command-picker" role="listbox" aria-label="Comandos e skills">
      {matches.map((item, index) => (
        <li
          key={item.id}
          role="option"
          aria-selected={index === highlighted}
          className={`command-picker__item${index === highlighted ? ' is-highlighted' : ''}`}
          onMouseEnter={() => setHighlighted(index)}
          onMouseDown={(event) => { event.preventDefault(); onSelect(item.token) }}
        >
          <span className="command-picker__name">/{item.token}</span>
          <span className="command-picker__kind">{item.kind === 'skill' ? 'Skill' : 'Comando'}</span>
          {item.hint && <span className="command-picker__hint">{item.hint}</span>}
          <span className="command-picker__description">{item.description}</span>
        </li>
      ))}
    </ul>
  )
}

import { useEffect, useMemo, useState } from 'react'
import type { PluginCommand } from '../../api/plugins'

export type CommandPickerProps = {
  commands: PluginCommand[]
  query: string
  onSelect: (command: PluginCommand) => void
  onDismiss: () => void
}

/** The name a person types to reach a command: bare when unique, qualified when not. */
export function commandToken(command: PluginCommand): string {
  return command.qualified ? `${command.plugin_id}:${command.slug}` : command.slug
}

/**
 * The menu that opens on a leading `/`.
 *
 * It owns the arrow keys, Enter, and Escape only while it is on screen, so the
 * composer's own Enter-to-send is untouched whenever the picker is closed.
 */
export function CommandPicker({ commands, query, onSelect, onDismiss }: CommandPickerProps) {
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return commands
      .filter((item) => !needle || commandToken(item).toLowerCase().includes(needle) || item.description.toLowerCase().includes(needle))
      .slice(0, 50)
  }, [commands, query])
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
      else if (event.key === 'Enter') { event.preventDefault(); onSelect(matches[highlighted]) }
      else if (event.key === 'Escape') { event.preventDefault(); onDismiss() }
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [matches, highlighted, onSelect, onDismiss])

  if (matches.length === 0) return null

  return (
    <ul className="command-picker" role="listbox" aria-label="Comandos de plugin">
      {matches.map((item, index) => (
        <li
          key={item.command_id}
          role="option"
          aria-selected={index === highlighted}
          className={`command-picker__item${index === highlighted ? ' is-highlighted' : ''}`}
          onMouseEnter={() => setHighlighted(index)}
          onMouseDown={(event) => { event.preventDefault(); onSelect(item) }}
        >
          <span className="command-picker__name">/{commandToken(item)}</span>
          {item.argument_hint && <span className="command-picker__hint">{item.argument_hint}</span>}
          <span className="command-picker__description">{item.description}</span>
        </li>
      ))}
    </ul>
  )
}

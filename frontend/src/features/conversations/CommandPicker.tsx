import { useEffect, useMemo, useState } from 'react'
import type { PluginCommand } from '../../api/plugins'
import type { CatalogCommand } from '../../api/sessionTools'
import type { SkillSummary } from '../../api/skills'
import type { BuiltinCommand } from '../commands/builtins'
import { useKeybindingHandlers } from '../keybindings/engine'

export type CommandPickerProps = {
  commands: PluginCommand[]
  skills?: SkillSummary[]
  /** W7: Orin's built-in commands (only those the chat implements) and the folder's own skills and commands. */
  builtins?: readonly BuiltinCommand[]
  catalog?: readonly CatalogCommand[]
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
  kind: 'builtin' | 'command' | 'skill'
  description: string
  hint?: string
  /** Other names that find it (a built-in's aliases). */
  aliases?: string[]
}

const KIND_LABEL: Record<SlashItem['kind'], string> = { builtin: 'Orin', command: 'Comando', skill: 'Skill' }

export function skillToken(skill: SkillSummary, commands: PluginCommand[]): string {
  // Preserve existing plugin-command precedence for typed bare names. A skill
  // with the same name remains directly available through an explicit prefix.
  return commands.some((command) => commandToken(command) === skill.id) ? `skill:${skill.id}` : skill.id
}

/** Every entry the picker offers for a query, built-ins first. Exported for the composer, which must know the same. */
/** W7: every built-in is offered (56), so the cap leaves room for the folder's commands, plugins and skills after them. */
export const MAX_SLASH_ITEMS = 200

export function slashItems(
  query: string,
  commands: readonly PluginCommand[],
  skills: readonly SkillSummary[] = [],
  builtins: readonly BuiltinCommand[] = [],
  catalog: readonly CatalogCommand[] = [],
): SlashItem[] {
  const needle = query.trim().toLowerCase()
  const builtinNames = new Set(builtins.flatMap((item) => [item.name, ...item.aliases]))
  const items: SlashItem[] = [
    ...builtins.map((item) => ({ id: `builtin:${item.name}`, token: item.name, kind: 'builtin' as const, description: item.description, hint: item.argumentHint, aliases: item.aliases })),
    ...catalog.filter((item) => !builtinNames.has(item.name)).map((item) => ({
      id: `disk:${item.name}`, token: item.name, kind: item.kind === 'command' ? 'command' as const : 'skill' as const,
      description: item.description, hint: item.argumentHint || undefined,
    })),
    ...commands.map((item) => ({
      id: item.command_id,
      token: commandToken(item),
      kind: 'command' as const,
      description: item.description,
      hint: item.argument_hint,
    })),
    ...skills.filter((skill) => skill.available).map((skill) => ({
      id: `skill:${skill.id}`,
      token: skillToken(skill, [...commands]),
      kind: 'skill' as const,
      description: skill.description,
    })),
  ]
  if (!needle) return items.slice(0, MAX_SLASH_ITEMS)
  // W7: the best match first, as Claude Code does — so Enter on `/context` runs /context, not a command whose
  // description mentions "contexto". Within a rank the order above (built-ins, folder, plugins, skills) holds.
  const rank = (item: SlashItem): number => {
    const token = item.token.toLowerCase()
    const aliases = item.aliases ?? []
    if (token === needle || aliases.includes(needle)) return 0
    if (token.startsWith(needle)) return 1
    if (aliases.some((alias) => alias.startsWith(needle))) return 2
    if (token.includes(needle) || aliases.some((alias) => alias.includes(needle))) return 3
    if (item.description.toLowerCase().includes(needle)) return 4
    return -1
  }
  return items
    .map((item, index) => ({ item, index, rank: rank(item) }))
    .filter((entry) => entry.rank >= 0)
    .sort((left, right) => left.rank - right.rank || left.index - right.index)
    .map((entry) => entry.item)
    .slice(0, MAX_SLASH_ITEMS)
}

/**
 * The menu that opens on a leading `/`.
 *
 * It owns the arrow keys, Enter, and Escape only while it is on screen, so the
 * composer's own Enter-to-send is untouched whenever the picker is closed.
 */
export function CommandPicker({ commands, skills = [], builtins = [], catalog = [], query, onSelect, onDismiss }: CommandPickerProps) {
  const matches = useMemo(() => slashItems(query, commands, skills, builtins, catalog), [commands, skills, builtins, catalog, query])
  const [highlighted, setHighlighted] = useState(0)
  // Reset the highlight when the query changes, adjusting state during render
  // (React's documented pattern for this) rather than in an effect, which
  // would cost an extra commit.
  const [queryAtLastReset, setQueryAtLastReset] = useState(query)
  if (query !== queryAtLastReset) {
    setQueryAtLastReset(query)
    setHighlighted(0)
  }

  // W7: the Autocomplete context of the keybinding engine (rebindable); Enter also picks, as before.
  useKeybindingHandlers('Autocomplete', {
    'autocomplete:next': () => setHighlighted((index) => (index + 1) % Math.max(1, matches.length)),
    'autocomplete:previous': () => setHighlighted((index) => (index - 1 + matches.length) % Math.max(1, matches.length)),
    'autocomplete:accept': () => { if (matches[highlighted]) onSelect(matches[highlighted].token) },
    'autocomplete:dismiss': () => onDismiss(),
  }, matches.length > 0)
  useEffect(() => {
    if (matches.length === 0) return
    function onKeyDown(event: KeyboardEvent) {
      // W7-R10: the composer's Enter picks, and so does Enter with no control focused (the page itself); a
      // person's Enter on another control (a permission card's button, a dialog, another field) is theirs.
      // An IME's Enter confirms the composition.
      const target = event.target
      const fromComposer = target instanceof HTMLTextAreaElement && target.id === 'composer-message'
      const fromPage = target === document.body || target === document.documentElement
      if (!(fromComposer || fromPage) || event.isComposing) return
      if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); event.stopPropagation(); onSelect(matches[highlighted]!.token) }
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [matches, highlighted, onSelect])

  if (matches.length === 0) return null

  return (
    // Focusable because it scrolls (keyboard users can reach every entry); the composer keeps driving it.
    <ul className="command-picker" role="listbox" aria-label="Comandos e skills" tabIndex={0}>
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
          <span className="command-picker__kind">{KIND_LABEL[item.kind]}</span>
          {item.hint && <span className="command-picker__hint">{item.hint}</span>}
          <span className="command-picker__description">{item.description}</span>
        </li>
      ))}
    </ul>
  )
}

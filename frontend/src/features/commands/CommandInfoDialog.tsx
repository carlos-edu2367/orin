import { useEffect, useRef } from 'react'
import { keysFor } from '../keybindings/bindings'
import { ACTIONS } from '../keybindings/catalog'
import { useKeybindingHandlers, useKeybindings } from '../keybindings/engine'
import { displayKeys } from '../keybindings/keys'
import type { BuiltinCommand } from './builtins'
import type { InfoPanel } from './useChatCommands'

const SHORTCUTS = [
  'chat:submit', 'chat:newline', 'chat:cancel', 'chat:cycleMode', 'chat:modelPicker', 'chat:sendNow', 'history:search',
  'app:toggleTranscript', 'app:toggleTodos',
]

/** `/help` (commands and keys) and the short answers of `/status`, `/version`, `/sandbox`. */
export function CommandInfoDialog({ panel, commands, onClose }: { panel: InfoPanel; commands: readonly BuiltinCommand[]; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const { keymap } = useKeybindings()
  useKeybindingHandlers('Help', { 'help:dismiss': () => onClose() })
  useEffect(() => { closeRef.current?.focus() }, [])
  const title = panel.kind === 'help' ? 'Comandos e atalhos' : panel.title
  return (
    <div className="command-info__backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className="command-info" role="dialog" aria-modal="true" aria-labelledby="command-info-title">
        <header className="command-info__header">
          <h2 id="command-info-title">{title}</h2>
          <button ref={closeRef} type="button" className="ghost-button" onClick={onClose}>Fechar</button>
        </header>
        {panel.kind === 'text' ? (
          <div className="command-info__body" role="region" aria-label={title} tabIndex={0}>
            {panel.lines.map((line, index) => <p key={index}>{line}</p>)}
          </div>
        ) : (
          <div className="command-info__body command-info__body--help" role="region" aria-label="Lista de comandos e atalhos" tabIndex={0}>
            <h3>Comandos</h3>
            <dl className="command-info__list">
              {commands.map((command) => (
                <div key={command.name} className="command-info__row">
                  <dt>/{command.name}{command.argumentHint ? ` ${command.argumentHint}` : ''}{command.aliases.length > 0 ? <span className="command-info__aliases"> ({command.aliases.map((alias) => `/${alias}`).join(', ')})</span> : null}</dt>
                  <dd>{command.description}</dd>
                </div>
              ))}
            </dl>
            <h3>Atalhos</h3>
            <dl className="command-info__list">
              {SHORTCUTS.map((action) => {
                const keys = keysFor(keymap, action)
                if (keys.length === 0) return null
                return (
                  <div key={action} className="command-info__row">
                    <dt><kbd>{keys.map((sequence) => displayKeys(sequence)).join(' · ')}</kbd></dt>
                    <dd>{ACTIONS[action]?.label ?? action}</dd>
                  </div>
                )
              })}
            </dl>
            <p className="command-info__note">Digite <kbd>/</kbd> para ver os comandos, <kbd>@</kbd> para citar arquivos.</p>
          </div>
        )}
      </section>
    </div>
  )
}

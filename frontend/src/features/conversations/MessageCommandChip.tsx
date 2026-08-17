export type MessageCommand = { command_id: string; slug: string; arguments: string }

/** A user message that was a command invocation, shown as what was typed. */
export function MessageCommandChip({ command }: { command: MessageCommand }) {
  return (
    <p className="message-command-chip" role="note">
      <span className="message-command-chip__name">/{command.slug}</span>
      {command.arguments && <span className="message-command-chip__arguments">{command.arguments}</span>}
    </p>
  )
}

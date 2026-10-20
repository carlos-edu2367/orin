import { useEffect, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { runStatusLine, type StatusLine } from '../../api/sessionTools'
import { parseAnsi } from './ansi'

/**
 * The status line under the composer (W7, settings `statusLine`): the output of the person's command, with its ANSI
 * colours. It runs when the chat opens and whenever `refreshKey` changes (a turn starting or ending). Nothing shows
 * while it is not configured, the folder is not trusted, or the command fails — as in Claude Code.
 */
export function StatusLineBar({ client, conversationId, refreshKey }: { client: ApiClient; conversationId: string; refreshKey: string }) {
  const [line, setLine] = useState<StatusLine | null>(null)

  useEffect(() => {
    if (!conversationId) return
    const controller = new AbortController()
    // A short wait so a burst of state changes runs the command once.
    const timer = window.setTimeout(() => {
      runStatusLine(client, conversationId, controller.signal)
        .then((result) => setLine(result))
        .catch(() => { if (!controller.signal.aborted) setLine(null) })
    }, 300)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [client, conversationId, refreshKey])

  if (!line || !line.enabled || line.error || line.lines.length === 0) return null
  return (
    <div className="status-line" role="status" aria-label="Linha de status">
      {line.lines.map((text, row) => (
        <div key={row} className="status-line__row">
          {parseAnsi(text).map((segment, index) => (
            <span
              key={index}
              className={[
                typeof segment.color === 'number' ? `ansi-fg-${segment.color}` : '',
                segment.bold ? 'ansi-bold' : '', segment.dim ? 'ansi-dim' : '', segment.italic ? 'ansi-italic' : '',
                segment.underline ? 'ansi-underline' : '',
              ].filter(Boolean).join(' ') || undefined}
              style={typeof segment.color === 'string' ? { color: segment.color } : undefined}
            >
              {segment.text}
            </span>
          ))}
        </div>
      ))}
    </div>
  )
}

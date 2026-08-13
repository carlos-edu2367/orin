import type { ActivityGroup } from './activityTypes'
import { activityStateLabel } from './activitySummary'
import { workspaceFileUrl, type WorkspaceFilePreviewHandler } from './WorkspaceFileCard'

type BrowserActivityCardProps = {
  group: ActivityGroup
  conversationId?: string
  onPreview?: WorkspaceFilePreviewHandler
}

/** A compact, private visual trace for an agent's browser observation. */
export function BrowserActivityCard({ group, conversationId, onPreview }: BrowserActivityCardProps) {
  const latest = [...group.events].reverse().find((event) => event.screenshotPath)
  const screenshotPath = latest?.screenshotPath
  const title = latest?.label || group.label
  const openPreview = () => {
    if (conversationId && screenshotPath && onPreview) onPreview({ conversationId, path: screenshotPath, name: title })
  }

  return (
    <article className="browser-activity-card" data-state={group.state}>
      <header>
        <span className="browser-activity-card__glyph" aria-hidden="true">◉</span>
        <div>
          <strong>{group.label}</strong>
          <p>{title}</p>
        </div>
        <span className="browser-activity-card__state">{activityStateLabel(group.state)}</span>
      </header>
      {conversationId && screenshotPath && (
        <button type="button" className="browser-activity-card__capture" onClick={openPreview} aria-label={`Abrir captura de ${title}`}>
          <img src={workspaceFileUrl({ conversationId, path: screenshotPath })} alt={`Captura privada do navegador: ${title}`} />
          <span>Abrir captura</span>
        </button>
      )}
      {!screenshotPath && <p className="browser-activity-card__pending" role="status">Navegador em atividade; a captura aparecerá ao concluir a ação.</p>}
    </article>
  )
}

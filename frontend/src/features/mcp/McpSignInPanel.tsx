import type { McpOAuthControl } from './useMcpOAuth'

type McpSignInPanelProps = {
  displayName: string
  actionLabel: string
  oauth: McpOAuthControl
}

/** The sign-in controls shared by the approval card and the Reconectar action. */
export function McpSignInPanel({ displayName, actionLabel, oauth }: McpSignInPanelProps) {
  if (oauth.phase === 'waiting') {
    return (
      <div className="mcp-sign-in">
        <p className="approval-card__hint" role="status">Aguardando autorização no navegador…</p>
        {oauth.pendingUrl && (
          <a href={oauth.pendingUrl} target="_blank" rel="noopener noreferrer">Abrir página de login</a>
        )}
        <button type="button" className="approval-card__decline" onClick={() => void oauth.cancel()}>Cancelar</button>
      </div>
    )
  }
  return (
    <div className="mcp-sign-in">
      {oauth.phase === 'failed' && oauth.reason && <p className="approval-card__error" role="alert">{oauth.reason}</p>}
      <button type="button" className="approval-card__submit" onClick={() => void oauth.start()} disabled={oauth.phase === 'starting'}>
        {oauth.phase === 'starting' ? 'Abrindo…' : actionLabel || `Entrar com ${displayName}`}
      </button>
    </div>
  )
}

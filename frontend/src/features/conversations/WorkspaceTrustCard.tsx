import { useState } from 'react'
import { createBrowserApiClient } from '../../api/client'
import { trustConversationWorkspace } from '../../api/conversations'
import type { ConversationActivityEvent } from './activityTypes'

type Props = {
  event: ConversationActivityEvent
  conversationId: string
  /** Injected in tests; defaults to the real API call. */
  trust?: (conversationId: string) => Promise<{ trusted: boolean; path: string }>
}

const ITEM_LABELS: Record<string, string> = {
  hooks: 'hooks',
  env: 'variáveis de ambiente',
  apiKeyHelper: 'apiKeyHelper',
  statusLine: 'status line',
  'permissions.allow': 'permissions.allow',
  'permissions.additionalDirectories': 'diretórios adicionais',
  enabledPlugins: 'plugins',
  enableAllProjectMcpServers: 'servidores MCP do projeto',
}

export function WorkspaceTrustCard({ event, conversationId, trust }: Props) {
  const [state, setState] = useState<'idle' | 'working' | 'trusted' | 'failed'>('idle')
  const info = event.workspaceTrust
  if (!info) return null
  const run = () => {
    setState('working')
    const action = trust ?? ((id: string) => trustConversationWorkspace(createBrowserApiClient(), id))
    action(conversationId)
      .then(() => setState('trusted'))
      .catch(() => setState('failed'))
  }
  return (
    <article className="activity-card workspace-trust" data-state={state === 'trusted' ? 'completed' : 'waiting'} data-kind="lifecycle">
      <span className="activity-card__glyph" aria-hidden="true">⚠</span>
      <div className="workspace-trust__body">
        <span className="activity-card__label">
          {state === 'trusted' ? 'Pasta confiável — vale a partir da próxima mensagem.' : event.summary}
        </span>
        {state !== 'trusted' && (
          <>
            <p className="workspace-trust__detail">
              Desativado por segurança: {info.items.map((item) => ITEM_LABELS[item] ?? item).join(', ')}. Só confie se você conhece este repositório.
            </p>
            <code className="workspace-trust__path">{info.path}</code>
            <button type="button" className="workspace-trust__action" onClick={run} disabled={state === 'working'}>
              Confiar nesta pasta
            </button>
          </>
        )}
        {state === 'failed' && <span role="alert" className="workspace-trust__error">Não foi possível salvar a confiança.</span>}
      </div>
    </article>
  )
}

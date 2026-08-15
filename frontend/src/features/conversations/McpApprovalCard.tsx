import { useState, type FormEvent } from 'react'

export type McpApprovalServer = {
  server_id: string
  display_name: string
  transport: string
  secret_names: string[]
  catalog_id: string | null
}

type McpApprovalCardProps = {
  server: McpApprovalServer
  active: boolean
  onApprove: (secrets: Record<string, string>) => Promise<void>
  onDecline: () => Promise<void>
}

const TRANSPORT_LABEL: Record<string, string> = { stdio: 'processo local (stdio)', http: 'servidor remoto (https)' }

/**
 * The agent proposed a connection; only the user's click activates it. A
 * credential is typed here, never passed back through the agent's own message
 * — the value goes straight to approveMcpServer and nowhere else.
 */
export function McpApprovalCard({ server, active, onApprove, onDecline }: McpApprovalCardProps) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState<'approve' | 'decline' | null>(null)
  const [error, setError] = useState<string | null>(null)

  const missing = server.secret_names.some((name) => !values[name]?.trim())

  async function approve(event: FormEvent) {
    event.preventDefault()
    if (!active || submitting || missing) return
    setError(null)
    setSubmitting('approve')
    try {
      await onApprove({ ...values })
    } catch {
      setError(`Não foi possível conectar ${server.display_name}. Confira os valores e tente novamente.`)
    } finally {
      setSubmitting(null)
    }
  }

  async function decline() {
    if (!active || submitting) return
    setError(null)
    setSubmitting('decline')
    try {
      await onDecline()
    } catch {
      setError('Não foi possível registrar a recusa. Tente novamente.')
    } finally {
      setSubmitting(null)
    }
  }

  return (
    <section className="approval-card" data-active={active} aria-label={`Conectar ${server.display_name}`}>
      <header className="approval-card__header">
        <span className="approval-card__glyph" aria-hidden="true">⇄</span>
        <div>
          <strong>{server.display_name}</strong>
          <p>
            {active
              ? `O agente quer conectar este servidor MCP (${TRANSPORT_LABEL[server.transport] ?? server.transport}). Confira e informe as credenciais necessárias.`
              : `Solicitação de conexão com ${server.display_name} já foi resolvida.`}
          </p>
        </div>
      </header>

      {active ? (
        <form className="approval-card__form" onSubmit={(event) => void approve(event)}>
          <fieldset disabled={submitting !== null}>
            {server.secret_names.length === 0 ? (
              <p className="approval-card__hint">Este servidor não precisa de credencial.</p>
            ) : server.secret_names.map((name) => (
              <label key={name} className="approval-card__field">
                <span>{name}</span>
                <input
                  type="password"
                  autoComplete="off"
                  value={values[name] ?? ''}
                  onChange={(event) => setValues((current) => ({ ...current, [name]: event.target.value }))}
                />
              </label>
            ))}
          </fieldset>
          <footer className="approval-card__actions">
            <button type="button" className="approval-card__decline" onClick={() => void decline()} disabled={submitting !== null}>
              {submitting === 'decline' ? 'Recusando…' : 'Recusar'}
            </button>
            <button type="submit" className="approval-card__submit" disabled={submitting !== null || missing}>
              {submitting === 'approve' ? 'Conectando…' : 'Conectar'}
            </button>
          </footer>
          {error && <p className="approval-card__error" role="alert">{error}</p>}
        </form>
      ) : (
        <p className="approval-card__settled">Resolvido · {server.display_name}</p>
      )}
    </section>
  )
}

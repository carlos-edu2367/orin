import type { ContextUsage } from './activityTypes'

export function ContextIndicator({ usage }: { usage: ContextUsage | null }) {
  const tone = usage ? usage.percentage >= 90 ? 'is-danger' : usage.percentage >= 75 ? 'is-warn' : '' : 'is-unavailable'
  const rows = [
    ['System prompt', usage?.system_prompt_tokens], ['Histórico', usage?.history_tokens],
    ['Entrada atual', usage?.input_tokens], ['Tools', usage?.tools_tokens],
    ['Skills', usage?.skills_tokens], ['MCPs', usage?.mcps_tokens],
  ] as const
  return (
    <div className={`context-indicator ${tone}`} tabIndex={0} aria-label={usage ? `Contexto: ${usage.used_tokens.toLocaleString('pt-BR')} de ${usage.limit_tokens.toLocaleString('pt-BR')} tokens` : 'Contexto: aguardando cálculo'}>
      <span className="context-indicator__dot" aria-hidden="true" />
      <span>Contexto {usage ? `${usage.used_tokens.toLocaleString('pt-BR')} / ${usage.limit_tokens.toLocaleString('pt-BR')}` : '—'}</span>
      <div className="context-indicator__popover" role="tooltip">
        {usage ? <>
          <div className="context-indicator__heading"><strong>Contexto usado</strong><span>{usage.percentage}%</span></div>
          <div className="context-indicator__meter" aria-hidden="true"><span style={{ width: `${usage.percentage}%` }} /></div>
          <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value!.toLocaleString('pt-BR')} tokens</dd></div>)}</dl>
          <p>{usage.compaction_enabled ? 'Compactação automática ativa.' : 'Compactação automática indisponível.'}{usage.compaction_count > 0 ? ` Compactado ${usage.compaction_count} vez${usage.compaction_count === 1 ? '' : 'es'}.` : ''}</p>
        </> : <>
          <div className="context-indicator__heading"><strong>Contexto</strong><span>—</span></div>
          <p>O uso detalhado será calculado quando esta conversa executar a próxima mensagem.</p>
        </>}
      </div>
    </div>
  )
}

import type { ContextReport } from '../../api/sessionTools'

export const CONTEXT_LABELS: Record<string, string> = {
  system_prompt: 'Prompt do sistema',
  memory: 'Memória',
  tools: 'Ferramentas',
  mcp_tools: 'Ferramentas MCP',
  skills: 'Skills',
  messages: 'Mensagens',
  free: 'Livre',
}

export const GRID_CELLS = 100

/**
 * How many of the grid's cells each category fills: proportional to the context limit, by largest remainder, so the
 * cells always add up to the grid (the free space takes what the categories leave).
 */
export function contextCells(report: ContextReport, cells = GRID_CELLS): Array<{ key: string; cells: number }> {
  const limit = report.limitTokens > 0 ? report.limitTokens : report.categories.reduce((sum, item) => sum + item.tokens, 0)
  const used = report.categories.filter((item) => item.key !== 'free' && item.tokens > 0)
  if (limit <= 0) return [{ key: 'free', cells }]
  const raw = used.map((item) => ({ key: item.key, share: (item.tokens / limit) * cells }))
  const total = raw.reduce((sum, item) => sum + item.share, 0)
  // Over the limit (a measurement taken just before compaction): the categories share the whole grid.
  const exact = total > cells ? raw.map((item) => ({ key: item.key, share: (item.share * cells) / total })) : raw
  const counts = exact.map((item) => ({ key: item.key, cells: Math.floor(item.share), rest: item.share - Math.floor(item.share) }))
  const usedShare = Math.min(cells, Math.round(exact.reduce((sum, item) => sum + item.share, 0)))
  let missing = usedShare - counts.reduce((sum, item) => sum + item.cells, 0)
  for (const item of [...counts].sort((left, right) => right.rest - left.rest)) {
    if (missing <= 0) break
    item.cells += 1
    missing -= 1
  }
  const filled = counts.filter((item) => item.cells > 0).map(({ key, cells: count }) => ({ key, cells: count }))
  const free = cells - filled.reduce((sum, item) => sum + item.cells, 0)
  return free > 0 ? [...filled, { key: 'free', cells: free }] : filled
}

const number = (value: number) => value.toLocaleString('pt-BR')

/** `/context` (W7): the context window as a grid of cells, one colour per category, with a legend. */
export function ContextGrid({ report }: { report: ContextReport }) {
  const layout = contextCells(report)
  const limit = report.limitTokens
  const summary = `${number(report.usedTokens)} de ${number(limit)} tokens (${Math.round(report.percentage)}%)`
  return (
    <div className="context-grid">
      <p className="context-grid__summary">{summary}{report.compactionCount > 0 ? ` · compactada ${report.compactionCount}×` : ''}</p>
      <div className="context-grid__cells" role="img" aria-label={`Uso do contexto: ${summary}`}>
        {layout.flatMap((item) => Array.from({ length: item.cells }, (_, index) => (
          <span key={`${item.key}-${index}`} className={`context-grid__cell context-grid__cell--${item.key}`} />
        )))}
      </div>
      <ul className="context-grid__legend">
        {report.categories.map((item) => (
          <li key={item.key}>
            <span className={`context-grid__swatch context-grid__cell--${item.key}`} aria-hidden="true" />
            <span className="context-grid__name">{CONTEXT_LABELS[item.key] ?? item.key}</span>
            <span className="context-grid__tokens">{number(item.tokens)} tokens{limit > 0 ? ` · ${((item.tokens / limit) * 100).toFixed(1).replace('.', ',')}%` : ''}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

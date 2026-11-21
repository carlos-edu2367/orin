import type { ActivityGroup, ConversationActivityEvent } from './activityTypes'
import './VerificationCard.css'

export type VerificationFindingResolution = 'confirmed' | 'rejected' | 'fixed' | 'open'

export type VerificationEvidence = {
  kind: string
  command?: string
  exit_code?: number
  output_excerpt?: string
  file?: string
  line?: number
  reason?: string
}

export type VerificationFinding = {
  id: string
  severity: string
  originalSeverity: string
  category: string
  claim: string
  hasEvidence: boolean
  evidence: VerificationEvidence | null
  suggestedFix?: string
  resolution: VerificationFindingResolution
  resolutionReason?: string
}

export type VerificationCheckedCommand = {
  command: string
  exitCode: number | null
  summary: string
}

type VerificationViewModel = {
  reviewerProvider: string | null
  reviewerModel: string | null
  effort: string | null
  policy: string | null
  fallbackStep: number | null
  selectionReason: string | null
  triggerReason: string | null
  round: number | null
  filesChanged: number | null
  linesChanged: number | null
  linesKnown: boolean | null
  verdict: 'pass' | 'fail' | 'inconclusive' | 'skipped' | 'running'
  skippedReason: string | null
  completedWithCaveats: boolean
  checked: VerificationCheckedCommand[]
  findings: VerificationFinding[]
  unverified: string[]
}

const SEVERITY_ORDER: Record<string, number> = {
  blocker: 0,
  major: 1,
  minor: 2,
  nit: 3,
  unproven: 4,
}

const SEVERITY_LABELS: Record<string, string> = {
  blocker: 'Bloqueante',
  major: 'Alto',
  minor: 'Menor',
  nit: 'Detalhe',
  unproven: 'Sem prova',
}

const RESOLUTION_LABELS: Record<VerificationFindingResolution, string> = {
  confirmed: 'confirmado',
  rejected: 'rejeitado',
  fixed: 'corrigido',
  open: 'aberto',
}

const POLICY_LABELS: Record<string, string> = {
  cross_family: 'Outra família',
  same_model: 'Mesmo modelo',
  explicit: 'Modelo fixo',
}

const TRIGGER_LABELS: Record<string, string> = {
  files: 'Arquivos alterados acima do limiar',
  lines: 'Linhas alteradas acima do limiar',
  sensitive_path: 'Caminho sensível alterado',
  sensitive_paths: 'Caminho sensível alterado',
  manual: 'Verificação solicitada',
  hook: 'Hook de verificação',
  reverify: 'Nova rodada após correções',
}

const VERDICT_LABELS: Record<VerificationViewModel['verdict'], string> = {
  pass: 'Aprovado',
  fail: 'Reprovado',
  inconclusive: 'Inconclusivo',
  skipped: 'Pulado',
  running: 'Em andamento',
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function asString(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value.trim() : null
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function normalizeResolution(raw: unknown): VerificationFindingResolution {
  const cleaned = typeof raw === 'string' ? raw.trim().toLowerCase() : ''
  if (cleaned === 'confirmed' || cleaned === 'rejected' || cleaned === 'fixed') {
    return cleaned
  }
  return 'open'
}

function parseEvidence(raw: unknown): VerificationEvidence | null {
  const rec = asRecord(raw)
  const kind = asString(rec.kind)
  if (!kind) return null
  return {
    kind,
    command: asString(rec.command) ?? undefined,
    exit_code: asNumber(rec.exit_code) ?? undefined,
    output_excerpt: asString(rec.output_excerpt) ?? undefined,
    file: asString(rec.file) ?? undefined,
    line: asNumber(rec.line) ?? undefined,
    reason: asString(rec.reason) ?? undefined,
  }
}

function parseResolutionsMap(raw: unknown): Map<string, { status: VerificationFindingResolution; reason?: string }> {
  const map = new Map<string, { status: VerificationFindingResolution; reason?: string }>()
  if (Array.isArray(raw)) {
    for (const entry of raw) {
      const rec = asRecord(entry)
      const id = asString(rec.id ?? rec.finding_id)
      if (!id) continue
      map.set(id, {
        status: normalizeResolution(rec.status ?? rec.resolution),
        reason: asString(rec.reason ?? rec.resolution_reason) ?? undefined,
      })
    }
    return map
  }
  const obj = asRecord(raw)
  for (const [key, value] of Object.entries(obj)) {
    if (typeof value === 'string') {
      map.set(key, { status: normalizeResolution(value) })
    } else {
      const rec = asRecord(value)
      map.set(key, {
        status: normalizeResolution(rec.status ?? rec.resolution),
        reason: asString(rec.reason ?? rec.resolution_reason) ?? undefined,
      })
    }
  }
  return map
}

function parseFindings(
  rawFindings: unknown,
  resolutions: Map<string, { status: VerificationFindingResolution; reason?: string }>,
): VerificationFinding[] {
  if (!Array.isArray(rawFindings)) return []
  const items: VerificationFinding[] = []
  for (let i = 0; i < rawFindings.length; i += 1) {
    const rec = asRecord(rawFindings[i])
    const id = asString(rec.id ?? rec.finding_id) ?? `F${i + 1}`
    const severity = (asString(rec.severity) ?? 'unproven').toLowerCase()
    const originalSeverity = (asString(rec.original_severity) ?? severity).toLowerCase()
    const category = (asString(rec.category) ?? 'bug').toLowerCase()
    const claim = asString(rec.claim) ?? ''
    const evidence = parseEvidence(rec.evidence)
    const hasEvidence =
      typeof rec.has_evidence === 'boolean'
        ? rec.has_evidence
        : severity !== 'unproven' && evidence !== null
    const mapResolution = resolutions.get(id)
    const resolution = mapResolution
      ? mapResolution.status
      : normalizeResolution(rec.resolution ?? rec.status)
    const resolutionReason =
      mapResolution?.reason ?? asString(rec.resolution_reason) ?? undefined
    items.push({
      id,
      severity,
      originalSeverity,
      category,
      claim,
      hasEvidence,
      evidence,
      suggestedFix: asString(rec.suggested_fix) ?? undefined,
      resolution,
      resolutionReason,
    })
  }
  items.sort((a, b) => {
    const aOrder = SEVERITY_ORDER[a.severity] ?? 99
    const bOrder = SEVERITY_ORDER[b.severity] ?? 99
    if (aOrder !== bOrder) return aOrder - bOrder
    return a.id.localeCompare(b.id)
  })
  return items
}

function parseChecked(raw: unknown): VerificationCheckedCommand[] {
  if (!Array.isArray(raw)) return []
  const items: VerificationCheckedCommand[] = []
  for (const entry of raw) {
    const rec = asRecord(entry)
    const command = asString(rec.command)
    if (!command) continue
    items.push({
      command,
      exitCode: asNumber(rec.exit_code),
      summary: asString(rec.summary) ?? '',
    })
  }
  return items
}

function extractViewModel(group: ActivityGroup): VerificationViewModel {
  let reviewerProvider: string | null = null
  let reviewerModel: string | null = null
  let effort: string | null = null
  let policy: string | null = null
  let fallbackStep: number | null = null
  let selectionReason: string | null = null
  let triggerReason: string | null = null
  let round: number | null = null
  let filesChanged: number | null = null
  let linesChanged: number | null = null
  let linesKnown: boolean | null = null
  let verdict: VerificationViewModel['verdict'] = 'running'
  let skippedReason: string | null = null
  let completedWithCaveats = group.state === 'completed_with_caveats'
  let checked: VerificationCheckedCommand[] = []
  const mergedRawFindings = new Map<string, Record<string, unknown>>()
  let findingAutoIndex = 0
  const mergedResolutions = new Map<string, { status: VerificationFindingResolution; reason?: string }>()
  let unverified: string[] = []

  const mergeRawFindingList = (rawList: unknown) => {
    if (!Array.isArray(rawList)) return
    for (const entry of rawList) {
      const rec = asRecord(entry)
      findingAutoIndex += 1
      const id = asString(rec.id ?? rec.finding_id) ?? `F${findingAutoIndex}`
      const prev = mergedRawFindings.get(id)
      mergedRawFindings.set(id, prev ? { ...prev, ...rec } : { ...rec })
    }
  }

  for (const event of group.events) {
    const payload = asRecord(event.payload)
    if (
      event.type === 'code_mode.completed_with_caveats' ||
      event.verification === 'completed_with_caveats' ||
      event.state === 'completed_with_caveats' ||
      payload.completed_with_caveats === true
    ) {
      completedWithCaveats = true
    }

    if (event.type === 'verifier.model_selected') {
      const chosen = asRecord(payload.chosen)
      reviewerProvider = asString(chosen.provider) ?? reviewerProvider
      reviewerModel = asString(chosen.model) ?? reviewerModel
      effort = asString(chosen.effort) ?? effort
      policy = asString(payload.policy) ?? policy
      fallbackStep = asNumber(payload.fallback_step) ?? fallbackStep
      selectionReason = asString(payload.reason) ?? selectionReason
    }

    if (event.type === 'verifier.started') {
      reviewerProvider = asString(payload.reviewer_provider) ?? reviewerProvider
      reviewerModel = asString(payload.reviewer_model) ?? reviewerModel
      effort = asString(payload.effort) ?? effort
      policy = asString(payload.policy) ?? policy
      fallbackStep = asNumber(payload.fallback_step) ?? fallbackStep
      triggerReason = asString(payload.trigger_reason) ?? triggerReason
      round = asNumber(payload.round) ?? round
      filesChanged = asNumber(payload.files_changed) ?? filesChanged
      if (typeof payload.lines_known === 'boolean') linesKnown = payload.lines_known
      if (payload.lines_changed !== undefined) {
        linesChanged = asNumber(payload.lines_changed)
      }
      if (verdict === 'running') {
        verdict = 'running'
      }
    }

    if (event.type === 'verifier.finished' || event.type === 'code_mode.completed_with_caveats') {
      reviewerProvider = asString(payload.reviewer_provider) ?? reviewerProvider
      reviewerModel = asString(payload.reviewer_model) ?? reviewerModel
      effort = asString(payload.effort) ?? effort
      policy = asString(payload.policy) ?? policy
      fallbackStep = asNumber(payload.fallback_step) ?? fallbackStep
      triggerReason = asString(payload.trigger_reason) ?? triggerReason
      round = asNumber(payload.round) ?? round
      filesChanged = asNumber(payload.files_changed) ?? filesChanged
      if (typeof payload.lines_known === 'boolean') linesKnown = payload.lines_known
      if (payload.lines_changed !== undefined) {
        linesChanged = asNumber(payload.lines_changed)
      }
      const rawVerdict = asString(payload.verdict)?.toLowerCase()
      if (rawVerdict === 'pass' || rawVerdict === 'fail' || rawVerdict === 'inconclusive' || rawVerdict === 'skipped') {
        verdict = rawVerdict
      }
      skippedReason = asString(payload.skipped_reason) ?? skippedReason
      if (Array.isArray(payload.checked)) {
        checked = parseChecked(payload.checked)
      }
      if (payload.resolutions !== undefined) {
        for (const [id, res] of parseResolutionsMap(payload.resolutions).entries()) {
          mergedResolutions.set(id, res)
        }
      }
      if (Array.isArray(payload.findings)) {
        mergeRawFindingList(payload.findings)
      }
      if (Array.isArray(payload.open_findings)) {
        mergeRawFindingList(payload.open_findings)
      }
      if (Array.isArray(payload.unverified)) {
        unverified = payload.unverified.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
      }
    }
  }

  return {
    reviewerProvider,
    reviewerModel,
    effort,
    policy,
    fallbackStep,
    selectionReason,
    triggerReason,
    round,
    filesChanged,
    linesChanged,
    linesKnown,
    verdict,
    skippedReason,
    completedWithCaveats,
    checked,
    findings: parseFindings(Array.from(mergedRawFindings.values()), mergedResolutions),
    unverified,
  }
}

function formatTriggerReason(reason: string | null): string | null {
  if (!reason) return null
  return TRIGGER_LABELS[reason] ?? reason
}

function formatPolicy(policy: string | null, fallbackStep: number | null): string | null {
  if (!policy && fallbackStep === null) return null
  const parts: string[] = []
  if (policy) parts.push(POLICY_LABELS[policy] ?? policy)
  if (fallbackStep !== null && fallbackStep > 1) parts.push(`fallback passo ${fallbackStep}`)
  return parts.join(' · ')
}

export function VerificationCard({
  group,
  events,
}: {
  group?: ActivityGroup
  events?: ConversationActivityEvent[]
}) {
  const resolvedGroup: ActivityGroup = group ?? {
    id: 'verifier:standalone',
    kind: 'lifecycle',
    state: 'completed',
    label: 'Verificação independente',
    count: events?.length ?? 0,
    events: events ?? [],
    agentId: events?.[0]?.agentId ?? 'agent:main',
    failed: false,
  }

  const vm = extractViewModel(resolvedGroup)
  const modelDisplay =
    vm.reviewerModel && vm.reviewerProvider
      ? `${vm.reviewerModel} (${vm.reviewerProvider})`
      : vm.reviewerModel ?? vm.reviewerProvider ?? 'Nenhum modelo disponível'
  const policyDisplay = formatPolicy(vm.policy, vm.fallbackStep)
  const triggerDisplay = formatTriggerReason(vm.triggerReason)
  const verdictBadgeClass =
    vm.verdict === 'pass'
      ? 'verification-card__badge verification-card__badge--pass'
      : vm.verdict === 'fail'
        ? 'verification-card__badge verification-card__badge--fail'
        : vm.verdict === 'inconclusive' || vm.verdict === 'skipped'
          ? 'verification-card__badge verification-card__badge--warn'
          : 'verification-card__badge'

  return (
    <article
      className="verification-card"
      data-testid="verification-card"
      data-verdict={vm.verdict}
      data-caveats={vm.completedWithCaveats ? 'true' : undefined}
      aria-label="Verificação independente"
    >
      <header className="verification-card__header">
        <div className="verification-card__title-group">
          <h4 className="verification-card__title">Verificação independente</h4>
          <span className="verification-card__model" data-testid="verification-model">
            {modelDisplay}
            {vm.effort ? ` · esforço ${vm.effort}` : ''}
            {vm.round !== null && vm.round > 1 ? ` · rodada ${vm.round}` : ''}
          </span>
        </div>
        <div className="verification-card__badges">
          <span className={verdictBadgeClass} data-testid="verification-verdict">
            {VERDICT_LABELS[vm.verdict]}
          </span>
          {vm.completedWithCaveats && (
            <span
              className="verification-card__badge verification-card__badge--warn"
              data-testid="verification-caveats-badge"
            >
              Concluído com pendências
            </span>
          )}
        </div>
      </header>

      <div className="verification-card__meta">
        {triggerDisplay && (
          <span data-testid="verification-trigger">
            <strong>Motivo:</strong> {triggerDisplay}
          </span>
        )}
        {policyDisplay && (
          <span data-testid="verification-policy">
            <strong>Política:</strong> {policyDisplay}
          </span>
        )}
        {vm.filesChanged !== null && (
          <span data-testid="verification-scope">
            <strong>Escopo:</strong> {vm.filesChanged}{' '}
            {vm.filesChanged === 1 ? 'arquivo' : 'arquivos'}
            {vm.linesKnown === true && vm.linesChanged !== null
              ? ` · ${vm.linesChanged} ${vm.linesChanged === 1 ? 'linha' : 'linhas'}`
              : ''}
          </span>
        )}
      </div>

      {vm.linesKnown === false && (
        <p className="verification-card__warning" data-testid="verification-lines-unknown">
          Linhas alteradas desconhecidas — a contagem de linhas não pôde ser obtida pelo git neste workspace.
        </p>
      )}

      {vm.verdict === 'skipped' && (vm.skippedReason || vm.selectionReason) && (
        <p className="verification-card__warning" data-testid="verification-skipped-reason">
          Revisão pulada: {vm.skippedReason ?? vm.selectionReason}
        </p>
      )}

      {vm.checked.length > 0 && (
        <section className="verification-card__section" aria-label="Comandos executados">
          <h5 className="verification-card__section-title">Comandos verificados</h5>
          <ul className="verification-card__commands">
            {vm.checked.map((cmd, idx) => (
              <li className="verification-card__command" key={`${cmd.command}-${idx}`}>
                <div>
                  <code>{cmd.command}</code>
                  {cmd.summary && <p className="verification-card__reason">{cmd.summary}</p>}
                </div>
                {cmd.exitCode !== null && (
                  <span
                    className={`verification-card__exit ${
                      cmd.exitCode === 0
                        ? 'verification-card__exit--ok'
                        : 'verification-card__exit--err'
                    }`}
                  >
                    exit {cmd.exitCode}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {vm.findings.length > 0 && (
        <section className="verification-card__section" aria-label="Achados da verificação">
          <h5 className="verification-card__section-title">
            Achados ({vm.findings.length})
          </h5>
          <ul className="verification-card__findings">
            {vm.findings.map((finding) => {
              const isUnproven = finding.severity === 'unproven' || !finding.hasEvidence
              const severityLabel = SEVERITY_LABELS[finding.severity] ?? finding.severity
              return (
                <li
                  className="verification-card__finding"
                  key={finding.id}
                  data-testid={`verification-finding-${finding.id}`}
                  data-severity={finding.severity}
                  data-resolution={finding.resolution}
                >
                  <div className="verification-card__finding-head">
                    <div className="verification-card__finding-tags">
                      {!isUnproven && (
                        <span
                          className={`verification-card__severity verification-card__severity--${finding.severity}`}
                        >
                          {severityLabel}
                        </span>
                      )}
                      <span className="verification-card__reason">
                        {finding.id} · {finding.category}
                      </span>
                      {isUnproven && (
                        <span
                          className="verification-card__unproven"
                          data-testid={`finding-unproven-${finding.id}`}
                        >
                          sem prova
                        </span>
                      )}
                    </div>
                    <span
                      className={`verification-card__resolution verification-card__resolution--${finding.resolution}`}
                      data-testid={`finding-resolution-${finding.id}`}
                    >
                      {RESOLUTION_LABELS[finding.resolution]}
                    </span>
                  </div>

                  {finding.claim && <p className="verification-card__claim">{finding.claim}</p>}

                  {finding.evidence && (
                    <p className="verification-card__evidence">
                      {finding.evidence.file ? (
                        <>
                          Evidência:{' '}
                          <code>
                            {finding.evidence.file}
                            {finding.evidence.line !== undefined ? `:${finding.evidence.line}` : ''}
                          </code>
                          {finding.evidence.reason ? ` — ${finding.evidence.reason}` : ''}
                        </>
                      ) : finding.evidence.command ? (
                        <>
                          Evidência: <code>{finding.evidence.command}</code>
                          {finding.evidence.exit_code !== undefined
                            ? ` (exit ${finding.evidence.exit_code})`
                            : ''}
                          {finding.evidence.output_excerpt ? ` — ${finding.evidence.output_excerpt}` : ''}
                        </>
                      ) : null}
                    </p>
                  )}

                  {finding.suggestedFix && (
                    <p className="verification-card__fix">
                      <strong>Sugestão:</strong> {finding.suggestedFix}
                    </p>
                  )}

                  {finding.resolutionReason && (
                    <p className="verification-card__reason">
                      <strong>Resolução:</strong> {finding.resolutionReason}
                    </p>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {vm.unverified.length > 0 && (
        <section className="verification-card__section" aria-label="Pontos não verificados">
          <h5 className="verification-card__section-title">Não verificado</h5>
          <ul className="verification-card__unverified">
            {vm.unverified.map((entry, idx) => (
              <li key={idx} className="verification-card__reason">
                {entry}
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  )
}

import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { parseConversationActivityEvent } from '../../../api/conversations'
import { activityStateLabel } from '../activitySummary'
import { CodeModeCard } from '../CodeModeCard'
import { TurnTimeline } from '../TurnTimeline'
import { buildTurnTimeline } from '../turnTimelineFold'
import { VerificationCard } from '../VerificationCard'
import type { ConversationActivityEvent } from '../activityTypes'

function makeVerifierEvents(): ConversationActivityEvent[] {
  const selected = parseConversationActivityEvent(
    {
      event_id: 'evt-sel',
      event_type: 'verifier.model_selected',
      agent_id: 'agent:main',
      turn_id: 'turn-42',
      summary: 'Modelo do verificador selecionado: gpt-6.1-sol (openai-codex)',
      payload: {
        policy: 'cross_family',
        chosen: { provider: 'openai-codex', model: 'gpt-6.1-sol', effort: 'high' },
        fallback_step: 2,
        reason: 'cross_family_selected',
      },
    },
    '1',
  )
  const started = parseConversationActivityEvent(
    {
      event_id: 'evt-start',
      event_type: 'verifier.started',
      agent_id: 'agent:main',
      turn_id: 'turn-42',
      summary: 'Verificando com gpt-6.1-sol (openai-codex, esforço high): sensitive_path',
      payload: {
        round: 1,
        reviewer_provider: 'openai-codex',
        reviewer_model: 'gpt-6.1-sol',
        effort: 'high',
        policy: 'cross_family',
        fallback_step: 2,
        trigger_reason: 'sensitive_path',
        files_changed: 3,
        lines_changed: null,
        lines_known: false,
      },
    },
    '2',
  )
  const finished = parseConversationActivityEvent(
    {
      event_id: 'evt-fin',
      event_type: 'verifier.finished',
      agent_id: 'agent:main',
      turn_id: 'turn-42',
      summary: 'Verificação independente encontrou 2 achado(s) bloqueante(s) (gpt-6.1-sol)',
      payload: {
        review_id: 'rev-1',
        round: 1,
        verdict: 'fail',
        passed: false,
        reviewer_provider: 'openai-codex',
        reviewer_model: 'gpt-6.1-sol',
        effort: 'high',
        trigger_reason: 'sensitive_path',
        files_changed: 3,
        lines_changed: null,
        lines_known: false,
        completed_with_caveats: true,
        checked: [
          { command: 'pytest tests/unit/auth -q', exit_code: 1, summary: '1 failed' },
          { command: 'npx tsc -b', exit_code: 0, summary: 'clean' },
        ],
        findings: [
          {
            id: 'F1',
            severity: 'blocker',
            original_severity: 'blocker',
            category: 'security',
            claim: 'Token expiration is not checked on refresh.',
            has_evidence: true,
            evidence: { kind: 'code_ref', file: 'src/auth/jwt.py', line: 88, reason: 'missing exp check' },
          },
          {
            id: 'F2',
            severity: 'major',
            original_severity: 'major',
            category: 'bug',
            claim: 'Retry loop swallows timeout.',
            has_evidence: true,
            evidence: { kind: 'failing_test', command: 'pytest tests/unit/auth -q', exit_code: 1 },
          },
          {
            id: 'F3',
            severity: 'minor',
            original_severity: 'minor',
            category: 'consistency',
            claim: 'Rename helper to match module naming.',
            has_evidence: true,
            evidence: { kind: 'code_ref', file: 'src/auth/jwt.py', line: 12, reason: 'style mismatch' },
          },
          {
            id: 'F4',
            severity: 'nit',
            original_severity: 'nit',
            category: 'test',
            claim: 'Add edge case for empty audience.',
            has_evidence: true,
            evidence: { kind: 'code_ref', file: 'tests/test_jwt.py', line: 40, reason: 'missing case' },
          },
          {
            id: 'F5',
            severity: 'unproven',
            original_severity: 'blocker',
            category: 'performance',
            claim: 'Query might be slow at scale.',
            has_evidence: false,
            evidence: null,
          },
        ],
        resolutions: {
          F1: { status: 'confirmed', reason: 'Mantido para próxima etapa.' },
          F2: { status: 'fixed', reason: 'Corrigido no retry.' },
          F3: { status: 'rejected', reason: 'Convenção já usada no pacote.' },
        },
        unverified: ['Integração com provedor externo'],
      },
    },
    '3',
  )
  return [selected, started, finished]
}

describe('VerificationCard and completed_with_caveats (WS-A7)', () => {
  it('renders model, trigger, commands, unknown lines warning, unproven badge, all four resolution labels, and caveats badge', () => {
    const events = makeVerifierEvents()
    render(<VerificationCard events={events} />)

    const card = screen.getByTestId('verification-card')
    expect(within(card).getByText('Verificação independente')).toBeInTheDocument()
    expect(screen.getByTestId('verification-model')).toHaveTextContent('gpt-6.1-sol (openai-codex)')
    expect(screen.getByTestId('verification-model')).toHaveTextContent('esforço high')
    expect(screen.getByTestId('verification-trigger')).toHaveTextContent(/Caminho sensível alterado/i)
    expect(screen.getByTestId('verification-policy')).toHaveTextContent(/Outra família/i)
    expect(screen.getByTestId('verification-policy')).toHaveTextContent(/fallback passo 2/i)
    expect(screen.getByTestId('verification-lines-unknown')).toHaveTextContent(/Linhas alteradas desconhecidas/i)
    expect(screen.getByTestId('verification-verdict')).toHaveTextContent('Reprovado')
    expect(screen.getByTestId('verification-caveats-badge')).toHaveTextContent('Concluído com pendências')

    // Checked commands
    const commandsSection = within(card).getByRole('region', { name: 'Comandos executados' })
    expect(within(commandsSection).getByText('pytest tests/unit/auth -q')).toBeInTheDocument()
    expect(within(commandsSection).getByText('exit 1')).toBeInTheDocument()
    expect(within(commandsSection).getByText('npx tsc -b')).toBeInTheDocument()
    expect(within(commandsSection).getByText('exit 0')).toBeInTheDocument()

    // Unproven finding badge
    expect(screen.getByTestId('finding-unproven-F5')).toHaveTextContent('sem prova')

    // Four resolution states in Portuguese
    expect(screen.getByTestId('finding-resolution-F1')).toHaveTextContent('confirmado')
    expect(screen.getByTestId('finding-resolution-F2')).toHaveTextContent('corrigido')
    expect(screen.getByTestId('finding-resolution-F3')).toHaveTextContent('rejeitado')
    expect(screen.getByTestId('finding-resolution-F4')).toHaveTextContent('aberto')
    expect(screen.getByTestId('finding-resolution-F5')).toHaveTextContent('aberto')
  })

  it('folds verifier.* events into a single timeline card even across intervening tool events and renders in TurnTimeline', () => {
    const [selected, started, finished] = makeVerifierEvents()
    const interveningTool = parseConversationActivityEvent(
      {
        event_id: 'evt-tool',
        event_type: 'tool.finished',
        agent_id: 'agent:verifier-1',
        parent_agent_id: 'agent:main',
        turn_id: 'turn-42',
        summary: 'Rodou pytest',
        payload: { tool_name: 'run_command', tool_kind: 'terminal', status: 'succeeded' },
      },
      '2b',
    )

    const items = buildTurnTimeline(
      [selected, started, interveningTool, finished],
      'turn-42',
      'msg-42',
      'Entrega finalizada com ressalvas.',
      true,
    )

    render(<TurnTimeline mode="code" items={items} />)
    expect(screen.getAllByTestId('verification-card')).toHaveLength(1)
    expect(screen.getByTestId('verification-caveats-badge')).toHaveTextContent('Concluído com pendências')
  })

  it('parses and renders code_mode.completed_with_caveats as "Concluído com pendências" in CodeModeCard, TurnTimeline, and activityStateLabel', () => {
    expect(activityStateLabel('completed_with_caveats')).toBe('Concluído com pendências')

    const caveatsEvent = parseConversationActivityEvent(
      {
        event_id: 'evt-cav',
        event_type: 'code_mode.completed_with_caveats',
        agent_id: 'agent:main',
        turn_id: 'turn-99',
        summary: '1 achado bloqueante permaneceu em aberto.',
        payload: {
          stage: 'completed_with_caveats',
          verification: 'completed_with_caveats',
        },
      },
      '99',
    )

    expect(caveatsEvent.state).toBe('completed_with_caveats')
    expect(caveatsEvent.verification).toBe('completed_with_caveats')

    const group = {
      id: 'code-mode:turn-99',
      kind: 'lifecycle' as const,
      state: 'completed_with_caveats' as const,
      label: 'Modo Code',
      count: 1,
      agentId: 'agent:main',
      failed: false,
      events: [caveatsEvent],
    }

    const { unmount } = render(<CodeModeCard group={group} />)
    expect(screen.getByText('Concluído com pendências')).toBeInTheDocument()
    expect(screen.getByTestId('code-completed-with-caveats')).toHaveTextContent('Concluído com pendências')
    unmount()

    render(
      <TurnTimeline
        mode="code"
        items={[{ id: group.id, kind: 'activity', group }]}
      />,
    )
    expect(screen.getByTestId('code-completed-with-caveats')).toHaveTextContent('Concluído com pendências')
  })
})

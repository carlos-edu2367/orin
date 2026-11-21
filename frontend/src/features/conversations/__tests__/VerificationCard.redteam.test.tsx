import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { parseConversationActivityEvent } from '../../../api/conversations'
import { VerificationCard } from '../VerificationCard'
import type { ConversationActivityEvent } from '../activityTypes'

describe('VerificationCard Red Team [RT-A-08]', () => {
  it('updates finding resolutions after round 1 verifier.finished, preserves findings across rounds, and displays completed_with_caveats', () => {
    const events: ConversationActivityEvent[] = [
      parseConversationActivityEvent(
        {
          event_id: 'evt-sel-1',
          event_type: 'verifier.model_selected',
          agent_id: 'agent:main',
          turn_id: 'turn-rt-8',
          summary: 'Modelo do verificador selecionado: gpt-6.1-sol (openai-codex)',
          payload: {
            policy: 'cross_family',
            chosen: { provider: 'openai-codex', model: 'gpt-6.1-sol', effort: 'high' },
            fallback_step: 1,
            reason: 'cross_family_selected',
          },
        },
        '1',
      ),
      parseConversationActivityEvent(
        {
          event_id: 'evt-start-1',
          event_type: 'verifier.started',
          agent_id: 'agent:main',
          turn_id: 'turn-rt-8',
          summary: 'Verificando com gpt-6.1-sol (openai-codex, esforço high): files',
          payload: {
            round: 1,
            reviewer_provider: 'openai-codex',
            reviewer_model: 'gpt-6.1-sol',
            effort: 'high',
            trigger_reason: 'files',
            files_changed: 4,
            lines_changed: 80,
            lines_known: true,
          },
        },
        '2',
      ),
      // Round 1 finishes with 3 findings and NO resolutions yet
      parseConversationActivityEvent(
        {
          event_id: 'evt-fin-1',
          event_type: 'verifier.finished',
          agent_id: 'agent:main',
          turn_id: 'turn-rt-8',
          summary: 'Verificação independente encontrou 3 achado(s) bloqueante(s)',
          payload: {
            review_id: 'rev-1',
            round: 1,
            verdict: 'fail',
            passed: false,
            reviewer_provider: 'openai-codex',
            reviewer_model: 'gpt-6.1-sol',
            effort: 'high',
            findings: [
              {
                id: 'F1',
                severity: 'blocker',
                category: 'security',
                claim: 'Missing CSRF check',
                has_evidence: true,
                evidence: { kind: 'code_ref', file: 'src/api.py', line: 10, reason: 'no csrf' },
              },
              {
                id: 'F2',
                severity: 'major',
                category: 'bug',
                claim: 'Off-by-one pagination',
                has_evidence: true,
                evidence: { kind: 'code_ref', file: 'src/list.py', line: 22, reason: 'off by one' },
              },
              {
                id: 'F3',
                severity: 'major',
                category: 'consistency',
                claim: 'Unused flag parameter',
                has_evidence: true,
                evidence: { kind: 'code_ref', file: 'src/list.py', line: 44, reason: 'unused' },
              },
            ],
            checked: [{ command: 'pytest -q', exit_code: 1, summary: '1 failed' }],
            unverified: [],
          },
        },
        '3',
      ),
      // Resolution update + round 2 pass (empty findings array on round 2) + caveats because F1 stayed confirmed
      parseConversationActivityEvent(
        {
          event_id: 'evt-fin-2',
          event_type: 'verifier.finished',
          agent_id: 'agent:main',
          turn_id: 'turn-rt-8',
          summary: 'Verificação independente encerrada com pendências',
          payload: {
            review_id: 'rev-2',
            round: 2,
            verdict: 'pass',
            passed: true,
            completed_with_caveats: true,
            findings: [],
            open_findings: [
              {
                id: 'F1',
                severity: 'blocker',
                category: 'security',
                claim: 'Missing CSRF check',
                has_evidence: true,
                evidence: { kind: 'code_ref', file: 'src/api.py', line: 10, reason: 'no csrf' },
              },
            ],
            resolutions: {
              F1: { status: 'confirmed', reason: 'Será tratado no próximo PR.' },
              F2: { status: 'fixed', reason: 'Corrigido na rodada 2.' },
              F3: { status: 'rejected', reason: 'Mantido por compatibilidade.' },
            },
          },
        },
        '4',
      ),
    ]

    render(<VerificationCard events={events} />)
    const card = screen.getByTestId('verification-card')

    // All three findings from round 1 MUST be preserved (not wiped out by round 2 findings: [])
    expect(within(card).getByText('Missing CSRF check')).toBeInTheDocument()
    expect(within(card).getByText('Off-by-one pagination')).toBeInTheDocument()
    expect(within(card).getByText('Unused flag parameter')).toBeInTheDocument()

    // Resolutions must reflect confirmed, fixed, rejected (none stuck at "aberto")
    expect(within(card).getByText('confirmado')).toBeInTheDocument()
    expect(within(card).getByText('corrigido')).toBeInTheDocument()
    expect(within(card).getByText('rejeitado')).toBeInTheDocument()
    expect(within(card).queryByText('aberto')).not.toBeInTheDocument()

    // Caveats badge must be shown
    expect(within(card).getByText('Concluído com pendências')).toBeInTheDocument()
  })
})

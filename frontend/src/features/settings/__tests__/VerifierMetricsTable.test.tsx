import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { VerifierMetricsTable } from '../VerifierMetricsTable'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('VerifierMetricsTable (WS-A7)', () => {
  it('shows a friendly empty state when pairs is empty', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ pairs: [], since: null }),
    )
    const client = new ApiClient({ fetchImpl: fetchMock })

    render(<VerifierMetricsTable client={client} />)

    const empty = await screen.findByTestId('verifier-metrics-empty')
    expect(empty).toHaveTextContent(/Nenhuma revisão registrada neste período/i)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('renders exact rates, findings per 100 lines, tokens, and numeric vs null avg_cost_usd', async () => {
    const user = userEvent.setup()
    const requestedUrls: string[] = []
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      requestedUrls.push(String(input))
      return jsonResponse({
        since: '2026-10-22T00:00:00+00:00',
        pairs: [
          {
            author_provider: 'claude-cli',
            author_model: 'claude-opus-5-5',
            reviewer_provider: 'openai-codex',
            reviewer_model: 'gpt-6.1-sol',
            reviews_count: 4,
            pass_count: 3,
            fail_count: 1,
            inconclusive_count: 0,
            total_findings: 5,
            proven_findings: 4,
            unproven_findings: 1,
            confirmed_count: 1,
            fixed_count: 2,
            rejected_count: 1,
            open_count: 1,
            confirmed_rate: 0.75,
            accepted_rate: 0.75,
            rejected_rate: 0.25,
            findings_per_100_lines: 2.5,
            total_tokens: 16800,
            avg_tokens: 4200,
            total_cost_usd: 0.05,
            avg_cost_usd: 0.0125,
          },
          {
            author_provider: 'openai-codex',
            author_model: 'gpt-6.1-sol',
            reviewer_provider: 'google-antigravity',
            reviewer_model: 'gemini-4-hydrogen',
            reviews_count: 2,
            pass_count: 2,
            fail_count: 0,
            inconclusive_count: 0,
            total_findings: 0,
            proven_findings: 0,
            unproven_findings: 0,
            confirmed_count: 0,
            fixed_count: 0,
            rejected_count: 0,
            open_count: 0,
            confirmed_rate: null,
            accepted_rate: null,
            rejected_rate: null,
            findings_per_100_lines: null,
            total_tokens: 5100,
            avg_tokens: 2550,
            total_cost_usd: null,
            avg_cost_usd: null,
          },
        ],
      })
    })
    const client = new ApiClient({ fetchImpl: fetchMock })

    render(<VerifierMetricsTable client={client} />)

    const table = await screen.findByRole('table', { name: /Métricas do verificador por par/i })
    const rows = within(table).getAllByRole('row')
    // Header + 2 data rows
    expect(rows).toHaveLength(3)

    const firstRowCells = within(rows[1]).getAllByRole('cell')
    expect(firstRowCells[0]).toHaveTextContent('claude-opus-5-5 (claude-cli)')
    expect(firstRowCells[0]).toHaveTextContent('gpt-6.1-sol (openai-codex)')
    expect(firstRowCells[1]).toHaveTextContent('4')
    expect(firstRowCells[2]).toHaveTextContent('75,0%')
    expect(firstRowCells[3]).toHaveTextContent('25,0%')
    expect(firstRowCells[4]).toHaveTextContent('2,50')
    expect(firstRowCells[5]).toHaveTextContent('4.200')
    expect(firstRowCells[6]).toHaveTextContent('$0.0125')

    const secondRowCells = within(rows[2]).getAllByRole('cell')
    expect(secondRowCells[0]).toHaveTextContent('gpt-6.1-sol (openai-codex)')
    expect(secondRowCells[0]).toHaveTextContent('gemini-4-hydrogen (google-antigravity)')
    expect(secondRowCells[1]).toHaveTextContent('2')
    expect(secondRowCells[2]).toHaveTextContent('—')
    expect(secondRowCells[3]).toHaveTextContent('—')
    expect(secondRowCells[4]).toHaveTextContent('—')
    expect(secondRowCells[5]).toHaveTextContent('2.550')
    expect(secondRowCells[6]).toHaveTextContent('—')

    // Changing period requests updated query string
    expect(requestedUrls[0]).toContain('/v1/orchestration/verifier/metrics?days=30')
    await user.selectOptions(screen.getByLabelText(/Período das métricas do verificador/i), '7')
    await waitFor(() => {
      expect(requestedUrls.some((url) => url.includes('/v1/orchestration/verifier/metrics?days=7'))).toBe(true)
    })
  })
})

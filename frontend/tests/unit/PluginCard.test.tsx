import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { PluginCard } from '../../src/features/plugins/PluginCard'
import type { PluginSummary } from '../../src/api/plugins'

const basePlugin: PluginSummary = {
  plugin_id: 'demo', version: '1.0.0', display_name: 'Demo', description: 'd', author: 'a',
  homepage: null, state: 'active', warnings: [], contribution_count: 1, contributions: [],
}

function client(fetchImpl: typeof fetch) { return new ApiClient({ fetchImpl, maxAttempts: 1 }) }
function response(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }) }

describe('PluginCard hooks toggle', () => {
  it('offers the hooks toggle only for a plugin contributing hooks', () => {
    const { rerender } = render(<PluginCard plugin={{ ...basePlugin, contributions: [{ kind: 'skill', reference: 's', display_name: 's', enabled: true }] }} client={client(vi.fn())} onChanged={() => {}} />)
    expect(screen.queryByRole('switch', { name: /hooks/i })).not.toBeInTheDocument()

    rerender(<PluginCard plugin={{ ...basePlugin, contributions: [{ kind: 'hook', reference: 'demo:SessionStart:0', display_name: 'SessionStart', enabled: false }] }} client={client(vi.fn())} onChanged={() => {}} />)
    expect(screen.getByRole('switch', { name: /hooks/i })).toBeInTheDocument()
  })

  it('reflects and toggles the consent state', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(response({ ...basePlugin, contributions: [{ kind: 'hook', reference: 'h', display_name: 'SessionStart', enabled: true }] }))
    const onChanged = vi.fn()
    render(<PluginCard plugin={{ ...basePlugin, contributions: [{ kind: 'hook', reference: 'h', display_name: 'SessionStart', enabled: false }] }} client={client(fetchImpl)} onChanged={onChanged} />)

    const toggle = screen.getByRole('switch', { name: /hooks/i })
    expect(toggle).toHaveAttribute('aria-checked', 'false')

    await userEvent.click(toggle)

    expect(fetchImpl).toHaveBeenCalled()
    expect(String(fetchImpl.mock.calls[0][0])).toContain('/hooks-enabled')
    expect(JSON.parse(String(fetchImpl.mock.calls[0][1]?.body))).toEqual({ enabled: true })
    expect(onChanged).toHaveBeenCalled()
  })
})

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ApiClient } from '../../api/client'
import type { PluginContribution, PluginSummary } from '../../api/plugins'
import { PluginCard } from './PluginCard'

const client = {} as ApiClient

function plugin(overrides: Partial<PluginSummary> & { contributions: PluginContribution[] }): PluginSummary {
  return {
    plugin_id: 'kit',
    version: '1.0.0',
    display_name: 'Kit',
    description: '',
    author: '',
    homepage: null,
    state: 'active',
    warnings: [],
    contribution_count: overrides.contributions.length,
    ...overrides,
  }
}

function hooksSwitch() {
  return screen.queryByRole('switch', { name: 'Permitir execução de hooks' })
}

describe('PluginCard hooks switch', () => {
  it('shows the switch for a plugin whose only consent row is a language server', () => {
    render(<PluginCard plugin={plugin({ contributions: [{ kind: 'lsp', reference: 'kit:pylsp#abc', display_name: 'pylsp', enabled: false }] })} client={client} onChanged={() => {}} />)

    expect(hooksSwitch()).toBeInTheDocument()
    expect(hooksSwitch()).toHaveAttribute('aria-checked', 'false')
  })

  it('is on only when every hook and language-server row is allowed', () => {
    render(<PluginCard plugin={plugin({ contributions: [
      { kind: 'hook', reference: 'kit:SessionStart:0#1', display_name: 'SessionStart', enabled: true },
      { kind: 'lsp', reference: 'kit:pylsp#abc', display_name: 'pylsp', enabled: false },
    ] })} client={client} onChanged={() => {}} />)

    expect(hooksSwitch()).toHaveAttribute('aria-checked', 'false')
  })

  it('cannot be changed while the plugin waits for its approval', () => {
    render(<PluginCard plugin={plugin({ state: 'pending_approval', contributions: [
      { kind: 'hook', reference: 'kit:PreToolUse:0#2', display_name: 'PreToolUse', enabled: false },
    ] })} client={client} onChanged={() => {}} />)

    expect(hooksSwitch()).toBeDisabled()
  })

  it('can be changed once an active plugin has its rows', () => {
    render(<PluginCard plugin={plugin({ state: 'active', contributions: [
      { kind: 'hook', reference: 'kit:SessionStart:0#1', display_name: 'SessionStart', enabled: false },
    ] })} client={client} onChanged={() => {}} />)

    expect(hooksSwitch()).toBeEnabled()
  })
})

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PluginApprovalCard } from '../../src/features/conversations/PluginApprovalCard'

const plugin = { plugin_id:'demo', version:'1.0.0', display_name:'Demo', description:'A plugin', author:'Ana', warnings:['hooks não suportados'], skills:[{skill_id:'demo:s',name:'Skill'}], mcp_servers:[{slug:'demo-mcp',display_name:'MCP',transport:'stdio'}], agents:[{agent_id:'demo:a',name:'Agent'}], commands:[], hooks:[], contribution_count:3 }

const HOOK = {
  hook_id: 'obsidian:SessionStart:0', event: 'SessionStart', matcher: '',
  command: 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/load_vault_context.py"', timeout_seconds: 10,
}

describe('PluginApprovalCard', () => {
  it('shows contributions and warnings', () => {
    render(<PluginApprovalCard plugin={plugin} active onApprove={vi.fn()} onDecline={vi.fn()} />)

    expect(screen.getByText('Demo · v1.0.0')).toBeInTheDocument()
    expect(screen.getByText(/hooks não suportados/)).toBeInTheDocument()
    expect(screen.getByText(/precisa de aprovação separada/)).toBeInTheDocument()
  })

  it('does not keep the approval dialog after the decision is resolved', () => {
    render(<PluginApprovalCard plugin={plugin} active={false} onApprove={vi.fn()} onDecline={vi.fn()} />)

    expect(screen.queryByText('Demo · v1.0.0')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Instalar' })).not.toBeInTheDocument()
  })

  it('lists commands and no longer claims a commands-only plugin is incompatible', () => {
    render(<PluginApprovalCard plugin={{ ...plugin, skills: [], mcp_servers: [], agents: [], commands: [
      { command_id: 'demo:daily', slug: 'daily', description: 'Nota diária' },
    ] }} active onApprove={vi.fn()} onDecline={vi.fn()} />)

    expect(screen.getByText(/\/daily/)).toBeInTheDocument()
    expect(screen.queryByText(/não oferece contribuições compatíveis/)).not.toBeInTheDocument()
  })

  it('shows the exact hook command in full, untruncated', () => {
    render(<PluginApprovalCard plugin={{ ...plugin, skills: [], mcp_servers: [], agents: [], commands: [], hooks: [HOOK] }} active onApprove={vi.fn()} onDecline={vi.fn()} />)

    expect(screen.getByText(HOOK.command)).toBeInTheDocument()
    expect(screen.getByText(/SessionStart/)).toBeInTheDocument()
  })

  it('says that approving does not authorize execution', () => {
    render(<PluginApprovalCard plugin={{ ...plugin, hooks: [HOOK] }} active onApprove={vi.fn()} onDecline={vi.fn()} />)

    expect(screen.getByText(/não autoriza a execução/i)).toBeInTheDocument()
  })

  it('does not show the hook notice for a plugin without hooks', () => {
    render(<PluginApprovalCard plugin={{ ...plugin, hooks: [] }} active onApprove={vi.fn()} onDecline={vi.fn()} />)

    expect(screen.queryByText(/não autoriza a execução/i)).not.toBeInTheDocument()
  })
})

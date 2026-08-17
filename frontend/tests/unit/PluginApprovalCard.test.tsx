import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PluginApprovalCard } from '../../src/features/conversations/PluginApprovalCard'

const plugin = { plugin_id:'demo', version:'1.0.0', display_name:'Demo', description:'A plugin', author:'Ana', warnings:['hooks não suportados'], skills:[{skill_id:'demo:s',name:'Skill'}], mcp_servers:[{slug:'demo-mcp',display_name:'MCP',transport:'stdio'}], agents:[{agent_id:'demo:a',name:'Agent'}], commands:[], contribution_count:3 }

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
})

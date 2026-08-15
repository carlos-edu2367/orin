import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PluginApprovalCard } from '../../src/features/conversations/PluginApprovalCard'

const plugin = { plugin_id:'demo', version:'1.0.0', display_name:'Demo', description:'A plugin', author:'Ana', warnings:['hooks não suportados'], skills:[{skill_id:'demo:s',name:'Skill'}], mcp_servers:[{slug:'demo-mcp',display_name:'MCP',transport:'stdio'}], agents:[{agent_id:'demo:a',name:'Agent'}], contribution_count:3 }
describe('PluginApprovalCard', () => { it('shows contributions and warnings', () => { render(<PluginApprovalCard plugin={plugin} active onApprove={vi.fn()} onDecline={vi.fn()} />); expect(screen.getByText('Demo · v1.0.0')).toBeInTheDocument(); expect(screen.getByText(/hooks não suportados/)).toBeInTheDocument(); expect(screen.getByText(/precisa de aprovação separada/)).toBeInTheDocument() }) })

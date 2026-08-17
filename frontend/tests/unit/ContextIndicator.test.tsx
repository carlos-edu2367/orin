import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ContextIndicator } from '../../src/features/conversations/ContextIndicator'

describe('ContextIndicator', () => {
  it('stays visible before the first context snapshot is available', () => {
    render(<ContextIndicator usage={null} />)

    expect(screen.getByLabelText('Contexto: aguardando cálculo')).toBeInTheDocument()
    expect(screen.getByText('Contexto —')).toBeInTheDocument()
    expect(screen.getByText('O uso detalhado será calculado quando esta conversa executar a próxima mensagem.')).toBeInTheDocument()
  })

  it('shows the total and detailed prompt components on focus', () => {
    render(<ContextIndicator usage={{
      used_tokens: 4200, limit_tokens: 16000, percentage: 26,
      system_prompt_tokens: 700, history_tokens: 1900, input_tokens: 300,
      tools_tokens: 800, skills_tokens: 300, mcps_tokens: 200,
      omitted_messages: 0, compaction_count: 0, compaction_enabled: true,
    }} />)

    expect(screen.queryByRole('button', { name: /contexto/i })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Contexto: 4.200 de 16.000 tokens')).toBeInTheDocument()
    expect(screen.getByText('System prompt')).toBeInTheDocument()
    expect(screen.getByText('MCPs')).toBeInTheDocument()
    expect(screen.getByText('Compactação automática ativa.')).toBeInTheDocument()
  })
})

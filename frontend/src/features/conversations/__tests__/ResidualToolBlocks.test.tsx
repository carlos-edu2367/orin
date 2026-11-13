import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MarkdownMessage } from '../MarkdownMessage'
import { hasResidualToolMarkers, parseResidualToolBlocks } from '../residualToolBlocks'

afterEach(() => {
  cleanup()
})

describe('ResidualToolBlocks', () => {
  it('collapses HTML-escaped &#91;Tool Use] + &#91;Tool Result] + glued Assistant into an expandable ActivityCard and renders only the final markdown text', () => {
    const leakedContent = [
      '&#91;Tool Use id=call_abc name=manage_session]',
      '{"name": "manage_session", "arguments": {"action": "inspect"}}',
      '&#91;/Tool Use]&#91;Tool Result id=call_abc name=manage_session status=succeeded]',
      '{"ok": true, "state": "running"}',
      '&#91;/Tool Result]AssistantTexto final',
    ].join('\n')

    const { container } = render(<MarkdownMessage content={leakedContent} />)

    // Renders the final markdown text without the glued "Assistant" prefix or raw tool protocol
    expect(screen.getByText('Texto final')).toBeInTheDocument()
    expect(container.textContent).not.toContain('AssistantTexto')
    expect(container.textContent).not.toContain('&#91;Tool Use')
    expect(container.textContent).not.toContain('[Tool Use')
    expect(container.textContent).not.toContain('"action": "inspect"')
    expect(container.textContent).not.toContain('"state": "running"')

    // Renders a collapsed ActivityCard by default
    const card = container.querySelector('.activity-card--tool')
    expect(card).not.toBeNull()
    expect(card).toHaveAttribute('data-state', 'completed')

    const trigger = container.querySelector('.activity-card__trigger') as HTMLButtonElement
    expect(trigger).not.toBeNull()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')

    // Expanding the card reveals the underlying parameters and output
    fireEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(container.textContent).toContain('manage_session')
    expect(container.textContent).toContain('"action": "inspect"')
    expect(container.textContent).toContain('"state": "running"')
  })

  it('handles plain [Tool Use ...] + [Tool Result status=failed] and batches consecutive tool calls', () => {
    const content = [
      'Vou verificar os arquivos primeiro.',
      '',
      '[Tool Use id=call_1 name=Read]',
      '{"file_path": "src/main.ts"}',
      '[/Tool Use]',
      '[Tool Result id=call_1 name=Read status=succeeded]',
      'console.log("ok")',
      '[/Tool Result]',
      '[Tool Use id=call_2 name=Bash]',
      '{"command": "npm test"}',
      '[/Tool Use]',
      '[Tool Result id=call_2 name=Bash status=failed]',
      'Error: test suite failed with exit code 1',
      '[/Tool Result][Assistant] O comando de teste falhou.',
    ].join('\n')

    const segments = parseResidualToolBlocks(content)
    expect(segments).toHaveLength(3)
    expect(segments[0]).toEqual({
      kind: 'markdown',
      content: 'Vou verificar os arquivos primeiro.\n\n',
    })
    expect(segments[1].kind).toBe('activity')
    if (segments[1].kind === 'activity') {
      expect(segments[1].group.kind).toBe('tool')
      expect(segments[1].group.state).toBe('failed')
      expect(segments[1].group.failed).toBe(true)
      expect(segments[1].group.count).toBe(2)
      expect(segments[1].group.events).toHaveLength(2)
      expect(segments[1].group.events[0].state).toBe('completed')
      expect(segments[1].group.events[1].state).toBe('failed')
    }
    expect(segments[2]).toEqual({
      kind: 'markdown',
      content: 'O comando de teste falhou.',
    })

    const { container } = render(<MarkdownMessage content={content} />)
    expect(screen.getByText('Vou verificar os arquivos primeiro.')).toBeInTheDocument()
    expect(screen.getByText('O comando de teste falhou.')).toBeInTheDocument()
    expect(container.textContent).not.toContain('[Assistant]')
    expect(container.textContent).not.toContain('Error: test suite failed')

    const card = container.querySelector('.activity-card--tool')
    expect(card).toHaveAttribute('data-state', 'failed')
    expect(container.textContent).toContain('1 falha')

    // Click to inspect failed output
    const trigger = container.querySelector('.activity-card__trigger') as HTMLButtonElement
    fireEvent.click(trigger)
    expect(container.textContent).toContain('Error: test suite failed with exit code 1')
  })

  it('detects partial streaming unclosed &#91;Tool Use ...] at the end of content as working state', () => {
    const streamingContent =
      'Analisando o arquivo solicitado...\n\n&#91;Tool Use id=1 name=Read]{"file_path":"a.py"}'

    const segments = parseResidualToolBlocks(streamingContent)
    expect(segments).toHaveLength(2)
    expect(segments[0].kind).toBe('markdown')
    expect(segments[1].kind).toBe('activity')
    if (segments[1].kind === 'activity') {
      expect(segments[1].group.state).toBe('working')
      expect(segments[1].group.events[0].state).toBe('working')
      expect(segments[1].group.events[0].summary).toBe('Lendo a.py')
    }

    const { container } = render(<MarkdownMessage content={streamingContent} />)
    expect(screen.getByText('Analisando o arquivo solicitado...')).toBeInTheDocument()
    expect(container.textContent).not.toContain('&#91;Tool Use')
    expect(container.textContent).not.toContain('{"file_path":"a.py"}')

    const card = container.querySelector('.activity-card--tool')
    expect(card).toHaveAttribute('data-state', 'working')
    expect(container.textContent).toContain('Lendo a.py')

    // Expanding shows the partial JSON payload
    const trigger = container.querySelector('.activity-card__trigger') as HTMLButtonElement
    fireEvent.click(trigger)
    expect(container.textContent).toContain('{"file_path":"a.py"}')
  })

  it('preserves [Tool Use ...] inside fenced markdown code blocks untouched', () => {
    const docContent = [
      'Exemplo de bloco de protocolo na documentação:',
      '',
      '```text',
      '[Tool Use id=1 name=test]',
      '{"hello": "world"}',
      '[/Tool Use]',
      '```',
      '',
      'Assistant roles in prose are also preserved.',
    ].join('\n')

    const segments = parseResidualToolBlocks(docContent)
    expect(segments).toHaveLength(1)
    expect(segments[0].kind).toBe('markdown')

    const { container } = render(<MarkdownMessage content={docContent} />)
    expect(container.querySelector('.activity-card')).toBeNull()
    const codeBlock = screen.getByTestId('markdown-code-block')
    expect(codeBlock.textContent).toContain('[Tool Use id=1 name=test]')
    expect(codeBlock.textContent).toContain('{"hello": "world"}')
    expect(screen.getByText('Assistant roles in prose are also preserved.')).toBeInTheDocument()
  })

  it('converts <orin_tool_call>, &lt;orin_tool_call&gt;, <tool_call>, and &#x5b;Tool Use] blocks and strips <speak> wrapper tags', () => {
    const xmlContent = [
      '<speak>Iniciando verificação.</speak>',
      '<orin_tool_call>{"name": "manage_session", "arguments": {"action": "status"}}</orin_tool_call>',
      '&lt;tool_call&gt;{"name": "Read", "arguments": {"file_path": "README.md"}}&lt;/tool_call&gt;',
      '&#x5B;Tool Result id=2 name=Read status=succeeded]Conteúdo do README&#x5B;/Tool Result]&#91;Assistant]: <speak>Tudo pronto.</speak>',
    ].join('\n')

    expect(hasResidualToolMarkers(xmlContent)).toBe(true)
    const { container } = render(<MarkdownMessage content={xmlContent} />)
    expect(screen.getByText('Iniciando verificação.')).toBeInTheDocument()
    expect(screen.getByText('Tudo pronto.')).toBeInTheDocument()
    expect(container.textContent).not.toContain('<speak>')
    expect(container.textContent).not.toContain('orin_tool_call')
    expect(container.textContent).not.toContain('&#91;Assistant]')
    expect(container.querySelector('.activity-card--tool')).not.toBeNull()
  })
})

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MarkdownMessage } from '../MarkdownMessage'
import { hasResidualToolMarkers, parseResidualToolBlocks } from '../residualToolBlocks'

afterEach(() => {
  cleanup()
})

const PRINT_TEXT = [
  '</output>',
  '</result>',
  '</function_results>Por código: os 10 PACOTE_344 são idênticos ao texto oficial...',
  'Gravo os 21 suspeitos da w1 como meus bloqueios e listo os 19 restantes para ler.',
  '<invoke name="Bash"> <parameter name="command">cd /c/Users/... && node -e " ... "',
].join('\n')

describe('Anthropic XML tool call written as text', () => {
  it('hides the leaked call and the made-up result in already persisted history', () => {
    expect(hasResidualToolMarkers(PRINT_TEXT)).toBe(true)
    const { container } = render(<MarkdownMessage content={PRINT_TEXT} />)
    expect(container.textContent).toContain('Por código: os 10 PACOTE_344')
    expect(container.textContent).toContain('listo os 19 restantes para ler.')
    expect(container.textContent).not.toContain('function_results')
    expect(container.textContent).not.toContain('invoke')
    expect(container.textContent).not.toContain('node -e')
    expect(container.textContent).not.toContain('</output>')
    expect(container.querySelector('.activity-card')).toBeNull()
  })

  it('removes closed function_calls/function_results blocks, literal or HTML-escaped', () => {
    const content = [
      'Antes.',
      '<function_calls><invoke name="Read"><parameter name="file_path">a.py</parameter></invoke></function_calls>',
      '<function_results><result><output>conteudo</output></result></function_results>',
      '&lt;function_calls&gt;&lt;invoke name=&quot;Bash&quot;&gt;ls&lt;/invoke&gt;&lt;/function_calls&gt;',
      'Depois.',
    ].join('\n')
    const segments = parseResidualToolBlocks(content)
    expect(segments).toHaveLength(1)
    const only = segments[0]
    expect(only.kind).toBe('markdown')
    if (only.kind === 'markdown') {
      expect(only.content).toContain('Antes.')
      expect(only.content).toContain('Depois.')
      for (const leaked of ['invoke', 'function_', 'conteudo', 'parameter', '&lt;']) {
        expect(only.content).not.toContain(leaked)
      }
    }
  })

  it('keeps standalone <result>/<output>/</output> and unrelated XML', () => {
    const content = 'Use <result>ok</result> e <output>1</output>, feche </output> e veja <invoice>1</invoice>.'
    expect(hasResidualToolMarkers(content)).toBe(false)
    expect(parseResidualToolBlocks(content)).toEqual([{ kind: 'markdown', content }])
  })

  it('leaves fenced code untouched', () => {
    const content = [
      'Exemplo:',
      '',
      '```xml',
      '<function_calls><invoke name="Bash"></invoke></function_calls>',
      '</output></result></function_results>',
      '```',
      '',
      'Fim.',
    ].join('\n')
    const { container } = render(<MarkdownMessage content={content} />)
    const code = screen.getByTestId('markdown-code-block')
    expect(code.textContent).toContain('<function_calls>')
    expect(code.textContent).toContain('</function_results>')
    expect(container.textContent).toContain('Fim.')
  })
})

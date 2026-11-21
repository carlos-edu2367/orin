import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../../api/client'
import { MemorySettingsPanel } from '../MemorySettingsPanel'

type RawMemorySetting = {
  path: string
  type: 'boolean' | 'number'
  group: string
  label: string
  description: string
  scope: string
  default: boolean | number
  value: boolean | number
  layer: string
  user_value: boolean | number | null
  has_user_override: boolean
  minimum: number | null
  maximum: number | null
  choices: string[] | null
  locked: boolean
}

function initialMemorySettings(): RawMemorySetting[] {
  return [
    {
      path: 'memory.quarantine.enabled',
      type: 'boolean',
      group: 'memory',
      label: 'Quarentena de memórias vindas de ferramentas e web',
      description: 'Mantém em quarentena memórias originadas de ferramentas externas.',
      scope: 'user_only',
      default: true,
      value: true,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      minimum: null,
      maximum: null,
      choices: null,
      locked: false,
    },
    {
      path: 'memory.conflicts.llmAdjudication',
      type: 'boolean',
      group: 'memory',
      label: 'Usar modelo para decidir conflitos ambíguos',
      description: 'Consulta um modelo auxiliar para conflitos ambíguos.',
      scope: 'user_only',
      default: false,
      value: false,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      minimum: null,
      maximum: null,
      choices: null,
      locked: false,
    },
    {
      path: 'memory.agent.enabled',
      type: 'boolean',
      group: 'memory',
      label: 'Memória própria de cada agente',
      description: 'Ativa o escopo de memória dedicado para subagentes.',
      scope: 'user_only',
      default: false,
      value: false,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      minimum: null,
      maximum: null,
      choices: null,
      locked: false,
    },
    {
      path: 'memory.agent.maxPromptChars',
      type: 'number',
      group: 'memory',
      label: 'Tamanho do resumo por agente',
      description: 'Limite máximo de caracteres para o resumo congelado.',
      scope: 'user_only',
      default: 4000,
      value: 4000,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      minimum: 200,
      maximum: 20000,
      choices: null,
      locked: false,
    },
    {
      path: 'memory.agent.learnFromVerifiedOnly',
      type: 'boolean',
      group: 'memory',
      label: 'Só aprender de trabalho verificado',
      description: 'Promove automaticamente propostas apenas após verificação aprovada.',
      scope: 'user_only',
      default: true,
      value: true,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      minimum: null,
      maximum: null,
      choices: null,
      locked: false,
    },
    {
      path: 'memory.instructionLint.enabled',
      type: 'boolean',
      group: 'memory',
      label: 'Conferir memórias contra CLAUDE.md/ORIN.md/AGENTS.md',
      description: 'Habilita verificação automática contra arquivos de instrução.',
      scope: 'user_only',
      default: false,
      value: false,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      minimum: null,
      maximum: null,
      choices: null,
      locked: false,
    },
    {
      path: 'memory.tombstoneRetentionDays',
      type: 'number',
      group: 'memory',
      label: 'Retenção de memórias esquecidas (dias)',
      description: 'Dias de retenção do marcador de memória esquecida.',
      scope: 'user_only',
      default: 90,
      value: 90,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      minimum: 1,
      maximum: 3650,
      choices: null,
      locked: false,
    },
  ]
}

describe('MemorySettingsPanel', () => {
  it('renders all 7 memory keys with origin layer, manual approval warning, toggles and resets to default', async () => {
    const user = userEvent.setup()
    let items = initialMemorySettings()

    const request = vi.fn(
      async (opts: {
        path: string
        method?: string
        query?: Record<string, unknown>
        body?: { changes?: Record<string, unknown> }
        parse: (value: unknown) => unknown
      }) => {
        expect(opts.path).toBe('/v1/settings/orchestration')
        if (opts.method === 'PUT' && opts.body?.changes) {
          for (const [key, value] of Object.entries(opts.body.changes)) {
            items = items.map((entry) => {
              if (entry.path !== key) return entry
              if (value === null) {
                return {
                  ...entry,
                  value: entry.default,
                  layer: 'default',
                  user_value: null,
                  has_user_override: false,
                }
              }
              const typed = value as boolean | number
              return {
                ...entry,
                value: typed,
                layer: 'user',
                user_value: typed,
                has_user_override: true,
              }
            })
          }
        }
        return opts.parse({ project_root: null, trusted: false, items })
      },
    )

    const client = { request } as unknown as ApiClient
    const { container } = render(<MemorySettingsPanel client={client} />)

    await waitFor(() => {
      expect(screen.getByTestId('memory-setting-row-memory.agent.enabled')).toBeInTheDocument()
    })

    for (const path of [
      'memory.agent.enabled',
      'memory.agent.maxPromptChars',
      'memory.agent.learnFromVerifiedOnly',
      'memory.quarantine.enabled',
      'memory.conflicts.llmAdjudication',
      'memory.instructionLint.enabled',
      'memory.tombstoneRetentionDays',
    ]) {
      expect(screen.getByTestId(`memory-setting-row-${path}`)).toBeInTheDocument()
      expect(screen.getByTestId(`memory-setting-layer-${path}`)).toHaveTextContent('Padrão')
    }

    // Warning about manual approval when selfVerify and verifier are off
    expect(screen.getByTestId('memory-verified-only-warning')).toHaveTextContent(/aprovação manual/i)

    // Enable memory.agent.enabled -> layer becomes Usuário and reset button appears
    const agentRow = screen.getByTestId('memory-setting-row-memory.agent.enabled')
    await user.click(within(agentRow).getByRole('checkbox'))

    await waitFor(() => {
      expect(screen.getByTestId('memory-setting-layer-memory.agent.enabled')).toHaveTextContent('Usuário')
      expect(screen.getByTestId('memory-setting-reset-memory.agent.enabled')).toBeInTheDocument()
    })

    // Click "Restaurar padrão" -> sends null and restores Padrão
    await user.click(screen.getByTestId('memory-setting-reset-memory.agent.enabled'))
    await waitFor(() => {
      expect(screen.getByTestId('memory-setting-layer-memory.agent.enabled')).toHaveTextContent('Padrão')
    })

    // Update numeric setting memory.tombstoneRetentionDays
    const retentionRow = screen.getByTestId('memory-setting-row-memory.tombstoneRetentionDays')
    const input = within(retentionRow).getByRole('spinbutton')
    await user.clear(input)
    await user.type(input, '120')
    await user.click(within(retentionRow).getByRole('button', { name: 'Aplicar' }))

    await waitFor(() => {
      expect(screen.getByTestId('memory-setting-layer-memory.tombstoneRetentionDays')).toHaveTextContent('Usuário')
    })

    expect(container.textContent).not.toMatch(/\p{Extended_Pictographic}/u)
  })
})

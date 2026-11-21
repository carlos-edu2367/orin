import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import { OrchestrationSection } from '../OrchestrationSection'
import { findSettingsItem, SETTINGS_GROUPS } from '../sections'

type RawSettingItem = {
  path: string
  type: 'boolean' | 'choice'
  group: string
  label: string
  description: string
  scope: 'user_only' | 'project_off_only'
  default: unknown
  value: unknown
  layer: 'default' | 'user' | 'project'
  user_value: unknown
  has_user_override: boolean
  choices: string[] | null
  locked: boolean
}

function initialItems(): RawSettingItem[] {
  return [
    {
      path: 'orchestration.agentMessages.parentTool',
      type: 'boolean',
      group: 'messages',
      label: 'Pai pode mandar mensagem a subagente',
      description: 'Permite que o agente pai envie orientações adicionais a subagentes.',
      scope: 'project_off_only',
      default: true,
      value: true,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      choices: null,
      locked: false,
    },
    {
      path: 'orchestration.agentMessages.continueOnUnread',
      type: 'boolean',
      group: 'messages',
      label: 'Continuar o subagente quando chega mensagem no fim do turno',
      description: 'Inicia rodada adicional quando chegam mensagens não lidas no fim do turno.',
      scope: 'user_only',
      default: true,
      value: true,
      layer: 'default',
      user_value: null,
      has_user_override: false,
      choices: null,
      locked: false,
    },
    {
      path: 'friend.sessionDelivery',
      type: 'choice',
      group: 'friend',
      label: 'Friend: entrega em sessões em andamento',
      description: 'Define entrega no turno em andamento (now) ou fila para depois do turno (queue).',
      scope: 'user_only',
      default: 'now',
      value: 'now',
      layer: 'default',
      user_value: null,
      has_user_override: false,
      choices: ['now', 'queue'],
      locked: false,
    },
  ]
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('OrchestrationSection — Mensagens e Friend (WS-B9a)', () => {
  it('registers "Orquestração" under "Inteligência" in sections.ts', () => {
    const intel = SETTINGS_GROUPS.find((group) => group.title === 'Inteligência')
    expect(intel).toBeDefined()
    const item = intel?.items.find((entry) => entry.path === '/settings/orchestration')
    expect(item?.label).toBe('Orquestração')
    expect(findSettingsItem('/settings/orchestration')?.id).toBe('orchestration')
  })

  it('loads all three keys, defaults friend.sessionDelivery to "now", saves changes, shows layer and resets to default', async () => {
    const user = userEvent.setup()
    let items = initialItems()
    const putPayloads: Array<Record<string, unknown>> = []

    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      expect(url).toContain('/v1/settings/orchestration')
      if (init?.method === 'PUT') {
        const body = JSON.parse(String(init.body ?? '{}')) as { changes: Record<string, unknown> }
        putPayloads.push(body.changes)
        items = items.map((item) => {
          if (!(item.path in body.changes)) return item
          const next = body.changes[item.path]
          if (next === null) {
            return {
              ...item,
              value: item.default,
              user_value: null,
              has_user_override: false,
              layer: 'default',
            }
          }
          return {
            ...item,
            value: next,
            user_value: next,
            has_user_override: true,
            layer: 'user',
          }
        })
      }
      return jsonResponse({
        project_root: null,
        trusted: false,
        items,
      })
    })
    const client = new ApiClient({ fetchImpl: fetchMock })

    render(
      <MemoryRouter initialEntries={['/settings/orchestration']}>
        <OrchestrationSection client={client} />
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Orquestração' })).toBeInTheDocument()

    // Agent messages block
    const messagesBlock = screen.getByTestId('orchestration-section-messages')
    const parentSwitch = within(messagesBlock).getByRole('switch', {
      name: /Pai pode mandar mensagem a subagente/i,
    })
    const continueSwitch = within(messagesBlock).getByRole('switch', {
      name: /Continuar o subagente quando chega mensagem no fim do turno/i,
    })
    expect(parentSwitch).toBeChecked()
    expect(continueSwitch).toBeChecked()

    // Friend block starts at 'now' with 'Padrão' layer badge
    const friendBlock = screen.getByTestId('orchestration-section-friend')
    const deliverySelect = within(friendBlock).getByRole('combobox', {
      name: /Friend: entrega em sessões em andamento/i,
    }) as HTMLSelectElement
    expect(deliverySelect.value).toBe('now')
    expect(within(friendBlock).getByText('Padrão')).toBeInTheDocument()

    // Toggle parentTool off -> saves and shows 'Usuário' layer + reset button
    await user.click(parentSwitch)
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'orchestration.agentMessages.parentTool': false })
    })
    expect(parentSwitch).not.toBeChecked()
    expect(within(messagesBlock).getByText('Usuário')).toBeInTheDocument()

    // Change friend.sessionDelivery to 'queue'
    await user.selectOptions(deliverySelect, 'queue')
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'friend.sessionDelivery': 'queue' })
    })
    expect(deliverySelect.value).toBe('queue')
    expect(within(friendBlock).getByText('Usuário')).toBeInTheDocument()

    // Reset friend.sessionDelivery back to default ('now')
    const resetFriendButton = within(friendBlock).getByRole('button', { name: /Restaurar padrão/i })
    await user.click(resetFriendButton)
    await waitFor(() => {
      expect(putPayloads).toContainEqual({ 'friend.sessionDelivery': null })
    })
    expect(deliverySelect.value).toBe('now')
    expect(within(friendBlock).getByText('Padrão')).toBeInTheDocument()
  })
})

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../../api/client'
import type { DiscordIntegrationState } from '../../../api/discord'
import { McpSection } from '../McpSection'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('Discord (Orin Friend) connection in McpSection', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('displays Discord (Orin Friend) card, validates Snowflakes/fail-closed rules, saves config, tests DM, and disconnects', async () => {
    const user = userEvent.setup()

    let currentDiscordState: DiscordIntegrationState = {
      enabled: false,
      has_bot_token: false,
      masked_token_hint: null,
      allowed_discord_user_ids: [],
      allowed_channel_ids: [],
      default_notify_channel_id: null,
      bot_application_id: null,
      bot_user_id: null,
      bot_username: null,
      runtime: {
        status: 'disconnected',
        last_connected_at: null,
        last_error: null,
        bot_user_id: null,
        bot_username: null,
        latency_ms: null,
      },
    }

    const putPayloads: Array<Record<string, unknown>> = []
    const testPayloads: Array<Record<string, unknown>> = []
    let deleteCalled = false

    const fetchImpl = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      const method = (init?.method ?? 'GET').toUpperCase()

      if (method === 'GET' && /\/v1\/mcp\/servers$/.test(url)) {
        return jsonResponse([])
      }
      if (method === 'GET' && /\/v1\/integrations\/discord$/.test(url)) {
        return jsonResponse(currentDiscordState)
      }
      if (method === 'PUT' && /\/v1\/integrations\/discord$/.test(url)) {
        const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
        putPayloads.push(body)
        currentDiscordState = {
          enabled: Boolean(body.enabled),
          has_bot_token: true,
          masked_token_hint: '...a9Xz',
          allowed_discord_user_ids: (body.allowed_discord_user_ids as string[]) ?? [],
          allowed_channel_ids: (body.allowed_channel_ids as string[]) ?? [],
          default_notify_channel_id: null,
          bot_application_id: '111222333444555666',
          bot_user_id: '999888777666555444',
          bot_username: 'OrinFriendBot',
          runtime: {
            status: body.enabled ? 'connected' : 'disconnected',
            last_connected_at: '2026-11-13T10:00:00Z',
            last_error: null,
            bot_user_id: '999888777666555444',
            bot_username: 'OrinFriendBot',
            latency_ms: 38,
          },
        }
        return jsonResponse(currentDiscordState)
      }
      if (method === 'POST' && /\/v1\/integrations\/discord\/test$/.test(url)) {
        const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>
        testPayloads.push(body)
        return jsonResponse({
          ...currentDiscordState,
          ok: true,
          connected: true,
          dm_sent: true,
          dm_channel_id: '777666555444333222',
          recipient_discord_user_id: '123456789012345678',
          message: 'Orin conectado a este PC e pronto para comandos via Orin Friend.',
          warning: null,
        })
      }
      if (method === 'DELETE' && /\/v1\/integrations\/discord$/.test(url)) {
        deleteCalled = true
        currentDiscordState = {
          enabled: false,
          has_bot_token: false,
          masked_token_hint: null,
          allowed_discord_user_ids: [],
          allowed_channel_ids: [],
          default_notify_channel_id: null,
          bot_application_id: null,
          bot_user_id: null,
          bot_username: null,
          runtime: {
            status: 'disconnected',
            last_connected_at: null,
            last_error: null,
            bot_user_id: null,
            bot_username: null,
            latency_ms: null,
          },
        }
        return jsonResponse({ deleted: true, ...currentDiscordState })
      }
      throw new Error(`Unhandled request: ${method} ${url}`)
    })

    const client = new ApiClient({
      fetchImpl,
      csrfToken: 'csrf-test',
      maxAttempts: 1,
      createIdempotencyKey: () => 'intent-discord-test',
    })

    render(
      <MemoryRouter initialEntries={['/connections']}>
        <McpSection client={client} />
      </MemoryRouter>,
    )

    const discordCard = await screen.findByTestId('connection-card-conn-discord')
    expect(within(discordCard).getByText('Discord (Orin Friend)')).toBeInTheDocument()
    expect(
      within(discordCard).getByText(
        'Controle projetos e sessões Chat/Code pelo celular via Orin Friend',
      ),
    ).toBeInTheDocument()
    expect(within(discordCard).getByTestId('discord-card-status')).toHaveTextContent('Desconectado')

    // Click Discord card to open the Discord inspector
    await user.click(discordCard)

    const inspector = screen.getByTestId('connections-inspector-panel')
    const scope = within(inspector)

    expect(scope.getByRole('heading', { name: 'Discord (Orin Friend)' })).toBeInTheDocument()
    expect(scope.getByTestId('discord-setup-guide')).toBeInTheDocument()

    // Try saving with enabled=true but no token -> fail-closed error
    const enableToggle = scope.getByTestId('discord-enabled-toggle')
    await user.click(enableToggle)
    await user.click(scope.getByTestId('discord-save-button'))
    expect(await scope.findByTestId('discord-feedback')).toHaveTextContent(/Bot Token/i)
    expect(putPayloads).toHaveLength(0)

    // Enter token and invalid Snowflake -> validation error
    const tokenInput = scope.getByTestId('discord-bot-token-input')
    const usersInput = scope.getByTestId('discord-allowed-users-input')
    await user.type(tokenInput, 'MTIzNDU2Nzg5MDEyMzQ1Njc4.fake.a9Xz')
    await user.type(usersInput, 'abc-invalid-id')
    await user.click(scope.getByTestId('discord-save-button'))
    expect(await scope.findByTestId('discord-feedback')).toHaveTextContent(/17 a 20 dígitos/i)
    expect(putPayloads).toHaveLength(0)

    // Enter valid Snowflake ID and save
    await user.clear(usersInput)
    await user.type(usersInput, '123456789012345678')
    await user.click(scope.getByTestId('discord-save-button'))

    expect(putPayloads).toHaveLength(1)
    expect(putPayloads[0]).toEqual({
      enabled: true,
      bot_token: 'MTIzNDU2Nzg5MDEyMzQ1Njc4.fake.a9Xz',
      allowed_discord_user_ids: ['123456789012345678'],
      allowed_channel_ids: [],
    })

    // Status badge updates to Conectado · @OrinFriendBot and placeholder shows masked token hint
    expect(await scope.findByTestId('discord-inspector-status')).toHaveTextContent(
      'Conectado · @OrinFriendBot',
    )
    expect(within(discordCard).getByTestId('discord-card-status')).toHaveTextContent(
      'Conectado · @OrinFriendBot',
    )
    expect(tokenInput).toHaveValue('')
    expect(tokenInput).toHaveAttribute(
      'placeholder',
      expect.stringContaining('...a9Xz'),
    )

    // Test DM send
    await user.click(scope.getByTestId('discord-test-button'))
    expect(testPayloads).toHaveLength(1)
    expect(await scope.findByTestId('discord-feedback')).toHaveTextContent(
      'Orin conectado a este PC e pronto para comandos via Orin Friend.',
    )

    // Disconnect / remove token
    await user.click(scope.getByTestId('discord-delete-button'))
    expect(deleteCalled).toBe(true)
    expect(await scope.findByTestId('discord-inspector-status')).toHaveTextContent('Desconectado')
    expect(discordCard).toHaveAttribute('role', 'button')
    expect(discordCard).toHaveAttribute('aria-selected', 'true')
  })
})

import { describe, expect, it } from 'vitest'
import { parseClaudeMcpAddCommand, validateMcpHeaderName } from '../claudeCommandParser'

describe('parseClaudeMcpAddCommand', () => {
  it('parses the user clickup command with http transport, name, url, and Authorization header', () => {
    const result = parseClaudeMcpAddCommand(
      'claude mcp add --transport http clickup https://mcp.team.contraestatisticallc.com/mcp --header "Authorization: Bearer <chave>"',
    )

    expect(result).toEqual({
      ok: true,
      displayName: 'clickup',
      transport: 'http',
      url: 'https://mcp.team.contraestatisticallc.com/mcp',
      headers: [{ name: 'Authorization', value: 'Bearer <chave>' }],
      secretNames: [],
      scope: undefined,
    })
  })

  it('supports -H and --header= forms', () => {
    const result = parseClaudeMcpAddCommand(
      'claude mcp add -t http my-api https://example.com/mcp -H "X-Api-Key: key-123" --header="X-Tenant: acme"',
    )

    expect(result).toEqual({
      ok: true,
      displayName: 'my-api',
      transport: 'http',
      url: 'https://example.com/mcp',
      headers: [
        { name: 'X-Api-Key', value: 'key-123' },
        { name: 'X-Tenant', value: 'acme' },
      ],
      secretNames: [],
      scope: undefined,
    })
  })

  it('supports quoted header values with spaces', () => {
    const result = parseClaudeMcpAddCommand(
      `claude mcp add --transport http custom https://example.com/mcp --header 'Authorization: Bearer token with spaces' --header "X-Custom-Note: valor com espacos"`,
    )

    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.headers).toEqual([
        { name: 'Authorization', value: 'Bearer token with spaces' },
        { name: 'X-Custom-Note', value: 'valor com espacos' },
      ])
    }
  })

  it('supports multi-line backslash line continuation', () => {
    const cmd = [
      'claude mcp add --transport http clickup \\',
      '  https://mcp.team.contraestatisticallc.com/mcp \\',
      '  --header "Authorization: Bearer <chave>"',
    ].join('\n')

    const result = parseClaudeMcpAddCommand(cmd)
    expect(result).toEqual({
      ok: true,
      displayName: 'clickup',
      transport: 'http',
      url: 'https://mcp.team.contraestatisticallc.com/mcp',
      headers: [{ name: 'Authorization', value: 'Bearer <chave>' }],
      secretNames: [],
      scope: undefined,
    })
  })

  it('infers http transport when --transport is omitted but a URL is given', () => {
    const result = parseClaudeMcpAddCommand(
      'claude mcp add clickup https://mcp.team.contraestatisticallc.com/mcp',
    )

    expect(result).toEqual({
      ok: true,
      displayName: 'clickup',
      transport: 'http',
      url: 'https://mcp.team.contraestatisticallc.com/mcp',
      headers: [],
      secretNames: [],
      scope: undefined,
    })
  })

  it('parses stdio command with --env KEY=val into secretNames and command/args', () => {
    const result = parseClaudeMcpAddCommand(
      'claude mcp add --env KEY=val github -- npx -y @modelcontextprotocol/server-github',
    )

    expect(result).toEqual({
      ok: true,
      displayName: 'github',
      transport: 'stdio',
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-github'],
      headers: [],
      secretNames: ['KEY'],
      scope: undefined,
    })
  })

  it('returns an error for --transport sse', () => {
    const result = parseClaudeMcpAddCommand(
      'claude mcp add --transport sse legacy https://example.com/sse',
    )

    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.error).toMatch(/SSE/i)
    }
  })

  it('returns an error for an unknown flag', () => {
    const result = parseClaudeMcpAddCommand(
      'claude mcp add --unknown-flag clickup https://example.com/mcp',
    )

    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.error).toMatch(/--unknown-flag/)
    }
  })

  it('returns an error when command does not start with claude mcp add', () => {
    const result = parseClaudeMcpAddCommand('npm install @modelcontextprotocol/sdk')

    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.error).toMatch(/claude mcp add/i)
    }
  })

  it('validates header names and rejects reserved headers case-insensitively', () => {
    expect(validateMcpHeaderName('Authorization')).toBeNull()
    expect(validateMcpHeaderName('X-Custom-Token')).toBeNull()
    expect(validateMcpHeaderName('Host')).toMatch(/reservado/i)
    expect(validateMcpHeaderName('cookie')).toMatch(/reservado/i)
    expect(validateMcpHeaderName('mcp-session-id')).toMatch(/reservado/i)
    expect(validateMcpHeaderName('Invalid Header Name')).toMatch(/inválido/i)
  })
})

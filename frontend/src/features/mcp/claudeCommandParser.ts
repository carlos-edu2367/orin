import type { McpTransport } from '../../api/mcp'

export type McpHeaderInput = {
  name: string
  value: string
}

export type ParsedClaudeMcpCommand =
  | {
      ok: true
      displayName: string
      transport: McpTransport
      url?: string
      command?: string
      args?: string[]
      headers: McpHeaderInput[]
      secretNames: string[]
      scope?: string
    }
  | {
      ok: false
      error: string
    }

export const HEADER_NAME_PATTERN = /^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,128}$/

export const RESERVED_MCP_HEADERS = new Set([
  'accept',
  'connection',
  'content-length',
  'content-type',
  'cookie',
  'host',
  'keep-alive',
  'mcp-protocol-version',
  'mcp-session-id',
  'proxy-authorization',
  'proxy-connection',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
])

export function validateMcpHeaderName(name: string): string | null {
  const trimmed = name.trim()
  if (!trimmed || !HEADER_NAME_PATTERN.test(trimmed)) {
    return `Nome de cabeçalho HTTP inválido: "${trimmed}".`
  }
  if (RESERVED_MCP_HEADERS.has(trimmed.toLowerCase())) {
    return `O cabeçalho HTTP "${trimmed}" é reservado e não pode ser configurado.`
  }
  return null
}

/**
 * Tokenizes a shell command line supporting single quotes, double quotes,
 * escaped characters, and multi-line backslash continuations.
 */
export function tokenizeCommand(raw: string): string[] {
  const normalized = raw.replace(/\\\r?\n/g, ' ').trim()
  const tokens: string[] = []
  let current = ''
  let inToken = false
  let quote: "'" | '"' | null = null

  for (let i = 0; i < normalized.length; i += 1) {
    const ch = normalized[i]
    if (quote === "'") {
      if (ch === "'") {
        quote = null
      } else {
        current += ch
      }
      inToken = true
      continue
    }
    if (quote === '"') {
      if (ch === '"') {
        quote = null
      } else if (ch === '\\' && i + 1 < normalized.length) {
        const next = normalized[i + 1]
        if (next === '"' || next === '\\' || next === '$' || next === '`') {
          current += next
          i += 1
        } else {
          current += ch
        }
      } else {
        current += ch
      }
      inToken = true
      continue
    }

    if (ch === "'" || ch === '"') {
      quote = ch
      inToken = true
      continue
    }

    if (ch === '\\' && i + 1 < normalized.length) {
      current += normalized[i + 1]
      inToken = true
      i += 1
      continue
    }

    if (/\s/.test(ch)) {
      if (inToken) {
        tokens.push(current)
        current = ''
        inToken = false
      }
      continue
    }

    current += ch
    inToken = true
  }

  if (inToken) {
    tokens.push(current)
  }

  return tokens
}

function parseHeaderSpec(spec: string): { ok: true; header: McpHeaderInput } | { ok: false; error: string } {
  const colonIndex = spec.indexOf(':')
  if (colonIndex <= 0) {
    return {
      ok: false,
      error: `Cabeçalho inválido "${spec}". Use o formato "Nome: Valor" (ex.: Authorization: Bearer sua-chave).`,
    }
  }
  const name = spec.slice(0, colonIndex).trim()
  const value = spec.slice(colonIndex + 1).trim()
  if (!name || !HEADER_NAME_PATTERN.test(name)) {
    return {
      ok: false,
      error: `Nome de cabeçalho HTTP inválido: "${name}".`,
    }
  }
  return { ok: true, header: { name, value } }
}

/**
 * Parses a pasted `claude mcp add ...` command into fields for `McpServerForm`.
 */
export function parseClaudeMcpAddCommand(input: string): ParsedClaudeMcpCommand {
  const trimmed = input.trim().replace(/^[$>]\s*/, '')
  if (!trimmed) {
    return { ok: false, error: 'Cole um comando `claude mcp add ...` para importar.' }
  }

  const tokens = tokenizeCommand(trimmed)
  if (tokens.length === 0) {
    return { ok: false, error: 'Cole um comando `claude mcp add ...` para importar.' }
  }

  if (tokens[0].toLowerCase() === 'claude' || tokens[0].toLowerCase() === 'orin') {
    if (tokens[1]?.toLowerCase() !== 'mcp' || tokens[2]?.toLowerCase() !== 'add') {
      return {
        ok: false,
        error: 'Comando não reconhecido. Use `claude mcp add --transport http <nome> <url> --header "Authorization: Bearer ..."`.',
      }
    }
  } else {
    return {
      ok: false,
      error: 'O comando deve começar com `claude mcp add`.',
    }
  }

  let idx = 3

  let transportRaw: string | null = null
  let scope: string | undefined
  const headers: McpHeaderInput[] = []
  const secretNames: string[] = []
  const positionals: string[] = []
  let afterDoubleDash = false

  while (idx < tokens.length) {
    const token = tokens[idx]
    if (afterDoubleDash) {
      positionals.push(token)
      idx += 1
      continue
    }

    if (token === '--') {
      afterDoubleDash = true
      idx += 1
      continue
    }

    if (token === '--transport' || token === '-t') {
      const val = tokens[idx + 1]
      if (!val) {
        return { ok: false, error: 'Informe o valor de `--transport` (ex.: `http` ou `stdio`).' }
      }
      transportRaw = val.trim().toLowerCase()
      idx += 2
      continue
    }
    if (token.startsWith('--transport=') || token.startsWith('-t=')) {
      transportRaw = token.slice(token.indexOf('=') + 1).trim().toLowerCase()
      idx += 1
      continue
    }

    if (token === '--header' || token === '-H') {
      const val = tokens[idx + 1]
      if (!val) {
        return { ok: false, error: 'Informe o cabeçalho após `--header` (ex.: `"Authorization: Bearer ..."`).' }
      }
      const parsed = parseHeaderSpec(val)
      if (!parsed.ok) return parsed
      headers.push(parsed.header)
      idx += 2
      continue
    }
    if (token.startsWith('--header=') || token.startsWith('-H=')) {
      const val = token.slice(token.indexOf('=') + 1)
      const parsed = parseHeaderSpec(val)
      if (!parsed.ok) return parsed
      headers.push(parsed.header)
      idx += 1
      continue
    }

    if (token === '--scope' || token === '-s') {
      scope = tokens[idx + 1]
      idx += 2
      continue
    }
    if (token.startsWith('--scope=') || token.startsWith('-s=')) {
      scope = token.slice(token.indexOf('=') + 1)
      idx += 1
      continue
    }

    if (token === '--env' || token === '-e') {
      const envSpec = tokens[idx + 1] ?? ''
      const eqIdx = envSpec.indexOf('=')
      const envName = (eqIdx > 0 ? envSpec.slice(0, eqIdx) : envSpec).trim()
      if (envName && !secretNames.includes(envName)) {
        secretNames.push(envName)
      }
      idx += 2
      continue
    }
    if (token.startsWith('--env=') || token.startsWith('-e=')) {
      const envSpec = token.slice(token.indexOf('=') + 1)
      const eqIdx = envSpec.indexOf('=')
      const envName = (eqIdx > 0 ? envSpec.slice(0, eqIdx) : envSpec).trim()
      if (envName && !secretNames.includes(envName)) {
        secretNames.push(envName)
      }
      idx += 1
      continue
    }

    if (token.startsWith('-')) {
      return { ok: false, error: `Opção não reconhecida no comando: "${token}".` }
    }

    positionals.push(token)
    idx += 1
  }

  if (transportRaw === 'sse') {
    return {
      ok: false,
      error: 'O transporte SSE (`--transport sse`) não é suportado para servidores adicionados na lista do Orin. Use `--transport http` ou configure o servidor em `.mcp.json`.',
    }
  }
  if (transportRaw === 'ws' || transportRaw === 'websocket') {
    return {
      ok: false,
      error: 'O transporte WebSocket não é suportado na lista do Orin. Use `--transport http`.',
    }
  }

  let transport: McpTransport
  if (!transportRaw) {
    const secondPositional = positionals[1] ?? ''
    transport = /^https?:\/\//i.test(secondPositional) || headers.length > 0 ? 'http' : 'stdio'
  } else if (transportRaw === 'http' || transportRaw === 'streamable-http' || transportRaw === 'streamable_http') {
    transport = 'http'
  } else if (transportRaw === 'stdio') {
    transport = 'stdio'
  } else {
    return {
      ok: false,
      error: `Transporte "${transportRaw}" não é suportado na lista do Orin (use "http" ou "stdio").`,
    }
  }

  if (transport === 'http') {
    const displayName = (positionals[0] ?? '').trim()
    const url = (positionals[1] ?? '').trim()
    if (!displayName || !url) {
      return {
        ok: false,
        error: 'Informe o nome e a URL do servidor HTTP (ex.: `claude mcp add --transport http clickup https://...`).',
      }
    }
    return {
      ok: true,
      displayName,
      transport: 'http',
      url,
      headers,
      secretNames,
      scope,
    }
  }

  const displayName = (positionals[0] ?? '').trim()
  const command = (positionals[1] ?? '').trim()
  const args = positionals.slice(2)
  if (!displayName || !command) {
    return {
      ok: false,
      error: 'Informe o nome e o comando do servidor stdio (ex.: `claude mcp add github npx -y @modelcontextprotocol/server-github`).',
    }
  }
  return {
    ok: true,
    displayName,
    transport: 'stdio',
    command,
    args,
    headers: [],
    secretNames,
    scope,
  }
}

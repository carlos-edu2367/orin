/**
 * What each built-in slash command does in a chat (W7). Commands that need a surface of a later batch are added with
 * that surface; until then they are not offered (`offered` lists only what has a handler here).
 */
import { useMemo } from 'react'
import type { ApiClient } from '../../api/client'
import { ApiError } from '../../api/errors'
import { CODE_PERMISSION_MODES } from '../../api/codeMode'
import { getConversationOverview } from '../../api/conversations'
import { addDirectory, compactConversation, getContext, getInstance, type ContextReport } from '../../api/sessionTools'
import { BUILTIN_COMMANDS, type BuiltinCommand, findBuiltin, isExcludedCommand, parseSlash } from './builtins'

export type InfoPanel =
  | { kind: 'help' }
  | { kind: 'text'; title: string; lines: string[] }
  | { kind: 'context'; report: ContextReport }

export type ChatCommandDeps = {
  client: ApiClient
  conversationId: string
  /** The conversation's messages, oldest first (for /copy and /export). */
  messages: ReadonlyArray<{ role: string; content: string }>
  title: string
  modelId: string
  provider: string
  permissionMode: string
  mode: string
  workspacePath: string | null
  navigate: (path: string) => void
  /** Sends text as a message of this conversation. */
  send: (text: string) => Promise<void>
  reload: () => Promise<void>
  notify: (text: string) => void
  openInfo: (panel: InfoPanel) => void
  openPreferences: () => void
  openModelPicker: () => void
  setPermissionMode: (mode: 'plan_mode') => Promise<void> | void
  copyText?: (text: string) => Promise<void>
  download?: (name: string, text: string) => void
  /** W7 surfaces (Batch D): offered only when the chat provides them. */
  openRewind?: () => void
  rename?: (title: string) => Promise<void>
  branch?: (title: string) => Promise<void>
  fork?: (prompt: string) => Promise<void>
  /** W7 surfaces (Batch D2). */
  openTasks?: () => void
}

type Handler = (args: string) => Promise<void> | void

// Orin's own words for the prompt commands (not Claude Code's prompts).
export const INIT_PROMPT = [
  'Analise este repositório e crie (ou atualize) o arquivo ORIN.md na raiz com o que um agente precisa saber para trabalhar aqui:',
  'como instalar, rodar e testar; a estrutura das pastas; as convenções de código; e cuidados específicos do projeto.',
  'Seja conciso e factual; não invente comandos que você não verificou.',
].join(' ')
export const REVIEW_PROMPT = 'Revise as mudanças atuais do repositório (git diff, incluindo arquivos novos): aponte bugs, riscos, testes que faltam e melhorias, em ordem de gravidade, com arquivo e linha.'
export const SECURITY_REVIEW_PROMPT = 'Faça uma revisão de segurança das mudanças atuais do repositório: injeção, autenticação e autorização, segredos expostos, validação de entrada, dependências e dados sensíveis. Liste cada achado com gravidade, arquivo, linha e correção sugerida.'
export const STATUSLINE_PROMPT = [
  'Configure a linha de status do Orin (a configuração `statusLine`, no formato { "type": "command", "command": "..." }).',
  'O comando recebe na entrada padrão um JSON com a sessão (modelo, pasta, custo, contexto) e imprime uma ou mais linhas curtas; cores ANSI são aceitas.',
  'Use o settings.json do usuário do Orin (~/.orin/settings.json) a menos que eu peça o da pasta (.orin/settings.json).',
  'Teste o comando com um JSON de exemplo antes de salvar e me mostre o resultado.',
].join(' ')
export const SANDBOX_TEXT = [
  'O Orin não isola os comandos numa sandbox do sistema operacional.',
  'No perfil local, os comandos de shell rodam com as permissões da sua conta, limitados pelas regras de permissão e pelo modo de permissão da sessão.',
  'Num Orin compartilhado (modo servidor), shell, MCP por stdio e hooks de plugins ficam desligados.',
]

function lastAssistant(messages: ChatCommandDeps['messages'], nth: number): string | null {
  const answers = messages.filter((item) => item.role === 'assistant' && item.content.trim())
  return answers[answers.length - nth]?.content ?? null
}

function transcript(deps: ChatCommandDeps): string {
  const lines = [`# ${deps.title || 'Conversa do Orin'}`, '']
  for (const item of deps.messages) {
    if (!item.content.trim()) continue
    lines.push(item.role === 'user' ? 'Você:' : 'Orin:', item.content.trim(), '')
  }
  return lines.join('\n')
}

function defaultDownload(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function chatCommandHandlers(deps: ChatCommandDeps): Record<string, Handler> {
  const copy = deps.copyText ?? ((text: string) => navigator.clipboard.writeText(text))
  const download = deps.download ?? defaultDownload
  const home = () => deps.navigate('/')
  const surfaces: Record<string, Handler> = {
    ...(deps.openRewind ? { rewind: () => deps.openRewind?.() } : {}),
    ...(deps.rename ? {
      rename: async (args: string) => {
        if (!args) { deps.notify('Diga o novo nome: /rename <nome>'); return }
        await deps.rename?.(args)
      },
    } : {}),
    ...(deps.branch ? { branch: (args: string) => deps.branch?.(args) } : {}),
    ...(deps.fork ? { fork: (args: string) => deps.fork?.(args) } : {}),
    ...(deps.openTasks ? { tasks: () => deps.openTasks?.() } : {}),
  }
  return {
    ...surfaces,
    usage: async () => {
      const overview = await getConversationOverview(deps.client, deps.conversationId)
      const usage = overview.token_usage
      const tokens = (value: number | null) => (value === null ? '—' : value.toLocaleString('pt-BR'))
      deps.openInfo({
        kind: 'text', title: 'Uso da sessão',
        lines: [
          `Tokens de entrada: ${tokens(usage.input_tokens)} · saída: ${tokens(usage.output_tokens)}`,
          typeof usage.estimated_cost_usd === 'number' ? `Custo estimado: US$ ${usage.estimated_cost_usd.toFixed(4)}` : 'Custo estimado: indisponível para este provedor',
          usage.usage_reported ? 'Valores informados pelo provedor.' : 'O provedor não informou o uso de todas as chamadas.',
        ],
      })
    },
    context: async () => {
      const report = await getContext(deps.client, deps.conversationId)
      if (!report.available) {
        deps.openInfo({ kind: 'text', title: 'Uso do contexto', lines: ['Ainda não há medição do contexto: ela aparece depois da primeira resposta.'] })
        return
      }
      deps.openInfo({ kind: 'context', report })
    },
    statusline: (args) => deps.send(args ? `${STATUSLINE_PROMPT}\nO que mostrar: ${args}` : STATUSLINE_PROMPT),
    help: () => deps.openInfo({ kind: 'help' }),
    clear: home,
    exit: home,
    compact: async (args) => {
      await compactConversation(deps.client, deps.conversationId, args || undefined)
      deps.notify('Compactando a conversa…')
      await deps.reload()
    },
    copy: async (args) => {
      const nth = /^\d+$/.test(args) ? Math.max(1, Number(args)) : 1
      const text = lastAssistant(deps.messages, nth)
      if (!text) { deps.notify('Não há resposta para copiar.'); return }
      await copy(text)
      deps.notify(nth === 1 ? 'Última resposta copiada.' : `Resposta ${nth} copiada.`)
    },
    export: (args) => {
      const name = (args.trim() || `orin-${deps.conversationId}.txt`).replace(/[\\/:*?"<>|]/g, '_')
      download(name.endsWith('.txt') || name.endsWith('.md') ? name : `${name}.txt`, transcript(deps))
      deps.notify('Conversa exportada.')
    },
    model: () => deps.openModelPicker(),
    plan: async (args) => {
      await deps.setPermissionMode('plan_mode')
      deps.notify('Modo Plano ativado.')
      if (args) await deps.send(args)
    },
    config: () => deps.navigate('/settings/general'),
    mcp: () => deps.navigate('/settings/mcp'),
    plugin: () => deps.navigate('/settings/plugins'),
    skills: () => deps.navigate('/settings/skills'),
    memory: () => deps.navigate('/settings/memory'),
    theme: () => deps.openPreferences(),
    init: () => deps.send(INIT_PROMPT),
    review: (args) => deps.send(args ? `${REVIEW_PROMPT}\nFoco: ${args}` : REVIEW_PROMPT),
    'security-review': () => deps.send(SECURITY_REVIEW_PROMPT),
    sandbox: () => deps.openInfo({ kind: 'text', title: 'Isolamento dos comandos', lines: SANDBOX_TEXT }),
    'add-dir': async (args) => {
      if (!args) { deps.notify('Diga a pasta: /add-dir <pasta>'); return }
      try {
        const added = await addDirectory(deps.client, deps.conversationId, args)
        deps.notify(`Pasta adicionada: ${added.path}`)
      } catch (error) {
        deps.notify(error instanceof ApiError && error.status === 404
          ? 'Adicionar pastas não está disponível neste Orin.'
          : 'Não foi possível adicionar a pasta. Confira o caminho.')
      }
    },
    version: async () => {
      const instance = await getInstance(deps.client)
      deps.openInfo({ kind: 'text', title: 'Versão', lines: [`Orin ${instance.version}`] })
    },
    'release-notes': async () => {
      const instance = await getInstance(deps.client)
      window.open(`https://github.com/carlos-edu2367/orin/releases/tag/v${encodeURIComponent(instance.version)}`, '_blank', 'noopener')
    },
    status: async () => {
      const instance = await getInstance(deps.client).catch(() => null)
      deps.openInfo({
        kind: 'text', title: 'Status da sessão',
        lines: [
          `Orin ${instance?.version ?? '—'} (${instance?.mode === 'server' ? 'servidor' : 'local'})`,
          `Modelo: ${deps.provider ? `${deps.provider} · ` : ''}${deps.modelId || '—'}`,
          `Modo: ${deps.mode === 'code' ? 'Orin Code' : 'Chat'} · permissões: ${CODE_PERMISSION_MODES.find((item) => item.mode === deps.permissionMode)?.label ?? (deps.permissionMode || '—')}`,
          `Pasta: ${deps.workspacePath ?? 'pasta gerenciada pelo Orin'}`,
        ],
      })
    },
  }
}

export type SlashOutcome = { kind: 'handled' } | { kind: 'excluded'; name: string } | { kind: 'message' }

export function useChatCommands(deps: ChatCommandDeps) {
  const handlers = useMemo(() => chatCommandHandlers(deps), [deps])
  const offered = useMemo<BuiltinCommand[]>(() => BUILTIN_COMMANDS.filter((command) => command.name in handlers), [handlers])
  async function run(text: string): Promise<SlashOutcome> {
    const parsed = parseSlash(text)
    if (!parsed) return { kind: 'message' }
    const builtin = findBuiltin(parsed.name)
    if (builtin && handlers[builtin.name]) {
      await handlers[builtin.name](parsed.args)
      return { kind: 'handled' }
    }
    if (!builtin && isExcludedCommand(parsed.name)) return { kind: 'excluded', name: parsed.name }
    // A skill, a plugin's command or an MCP prompt: the server expands it (and a built-in not offered yet is sent too).
    return { kind: 'message' }
  }
  return { offered, run }
}

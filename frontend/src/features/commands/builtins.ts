/**
 * Built-in slash commands with Claude Code's names and aliases (spec §10.1). The interface runs them itself; nothing
 * is sent to the model unless the command says so. A command is offered only once the chat implements its handler,
 * so the picker never lists something that does nothing.
 */

export type BuiltinCategory = 'session' | 'config' | 'info' | 'flow'

export type BuiltinCommand = {
  name: string
  aliases: string[]
  description: string
  argumentHint?: string
  category: BuiltinCategory
}

const c = (name: string, category: BuiltinCategory, description: string, aliases: string[] = [], argumentHint?: string): BuiltinCommand =>
  ({ name, aliases, description, category, argumentHint })

export const BUILTIN_COMMANDS: readonly BuiltinCommand[] = [
  // Session and context
  c('add-dir', 'session', 'Adiciona uma pasta de trabalho a esta sessão', [], '<pasta>'),
  c('agents', 'session', 'Gerencia os agentes definidos em arquivos'),
  c('autocompact', 'session', 'Mostra ou muda a compactação automática', [], '[on|off]'),
  c('btw', 'session', 'Pergunta paralela que não entra no histórico', [], '<pergunta>'),
  c('clear', 'session', 'Começa uma conversa nova', ['reset', 'new']),
  c('compact', 'session', 'Resume a conversa para liberar contexto', [], '[instruções]'),
  c('context', 'session', 'Mostra o uso do contexto em grade'),
  c('copy', 'session', 'Copia a última resposta', [], '[N]'),
  c('cd', 'session', 'Muda a pasta de trabalho da sessão', [], '<pasta>'),
  c('diff', 'session', 'Mostra as mudanças no diretório de trabalho'),
  c('export', 'session', 'Exporta a conversa como texto', [], '[arquivo]'),
  c('rename', 'session', 'Renomeia a conversa', ['name'], '<nome>'),
  c('resume', 'session', 'Retoma uma conversa anterior', ['continue']),
  c('rewind', 'session', 'Volta o código e/ou a conversa a uma mensagem anterior', ['checkpoint', 'undo']),
  c('branch', 'session', 'Ramifica a conversa e muda para o ramo', [], '[nome]'),
  c('fork', 'session', 'Copia a conversa para uma nova sessão e fica nesta', [], '[prompt]'),
  c('subtask', 'session', 'Delega uma tarefa a um subagente que herda a conversa', [], '<tarefa>'),
  c('exit', 'session', 'Sai da sessão', ['quit']),
  // Configuration
  c('config', 'config', 'Abre as configurações', ['settings']),
  c('doctor', 'config', 'Verifica a instalação do Orin'),
  c('effort', 'config', 'Muda o nível de esforço', [], '[low|medium|high|xhigh|max]'),
  c('fast', 'config', 'Liga ou desliga o modo rápido', [], '[on|off]'),
  c('model', 'config', 'Escolhe o modelo'),
  c('output-style', 'config', 'Escolhe o estilo de resposta', [], '[estilo]'),
  c('permissions', 'config', 'Gerencia as regras de permissão', ['allowed-tools']),
  c('hooks', 'config', 'Mostra e edita os hooks'),
  c('mcp', 'config', 'Gerencia os servidores MCP'),
  c('plugin', 'config', 'Gerencia os plugins', ['plugins', 'marketplace']),
  c('reload-plugins', 'config', 'Recarrega os plugins'),
  c('reload-skills', 'config', 'Recarrega as skills'),
  c('skills', 'config', 'Lista as skills'),
  c('memory', 'config', 'Edita os arquivos de memória'),
  c('pause-memory', 'config', 'Pausa ou retoma o aprendizado da memória', ['memory-pause', 'toggle-memory']),
  c('keybindings', 'config', 'Edita os atalhos de teclado'),
  c('statusline', 'config', 'Configura a linha de status', [], '[descrição]'),
  c('theme', 'config', 'Muda o tema de cores'),
  c('color', 'config', 'Muda a cor da barra de mensagem', [], '[cor|default]'),
  c('auto-mode-setup', 'config', 'Rascunha o ambiente do modo automático'),
  c('import', 'config', 'Importa configurações de outra ferramenta'),
  c('init', 'config', 'Cria o ORIN.md do projeto'),
  // Information
  c('help', 'info', 'Mostra os comandos e os atalhos'),
  c('status', 'info', 'Mostra a versão, o modelo, a pasta e as configurações'),
  c('tasks', 'info', 'Mostra os processos e agentes em segundo plano', ['bashes']),
  c('usage', 'info', 'Mostra o custo e o uso da sessão', ['cost', 'stats']),
  c('version', 'info', 'Mostra a versão do Orin'),
  c('release-notes', 'info', 'Mostra as notas da versão'),
  c('insights', 'info', 'Relatório das sessões recentes'),
  c('skill-doctor', 'info', 'Mostra o custo de contexto de cada skill'),
  c('sandbox', 'info', 'Explica o isolamento dos comandos'),
  // Flow
  c('plan', 'flow', 'Entra no Modo Plano', [], '[descrição]'),
  c('loop', 'flow', 'Repete um prompt enquanto a sessão estiver aberta', ['proactive'], '[intervalo] [prompt]'),
  c('loops', 'flow', 'Mostra as repetições ativas'),
  c('review', 'flow', 'Revisa as mudanças', [], '[alvo]'),
  c('security-review', 'flow', 'Revisão de segurança das mudanças'),
  c('ultrareview', 'flow', 'Revisão completa em vários agentes'),
  c('restart', 'flow', 'Reinicia o Orin', ['update']),
  c('desktop', 'flow', 'Abre esta sessão no app de desktop', ['app']),
]

/** Claude Code commands Orin leaves out (accounts, cloud, terminal-only): typing one says so instead of sending it. */
export const EXCLUDED_COMMANDS = new Set([
  'login', 'logout', 'voice', 'mobile', 'chrome', 'ide', 'install-github-app', 'install-slack-app', 'teleport', 'remote-env',
  'session', 'cloud-plugins', 'artifacts', 'autofix-pr', 'privacy-settings', 'passes', 'stickers', 'radio', 'powerup',
  'wellbeing', 'advisor', 'focus', 'upgrade', 'usage-credits', 'extra-usage', 'pro-trial-expired', 'rate-limit-options',
  'setup-bedrock', 'setup-vertex', 'feedback', 'bug', 'heapdump', 'team-onboarding', '__remote-workflow',
  'workflow-launch-exec', 'tui', 'terminal-setup', 'scroll-speed', 'ultraplan',
])

const BY_NAME = new Map<string, BuiltinCommand>()
for (const command of BUILTIN_COMMANDS) {
  BY_NAME.set(command.name, command)
  for (const alias of command.aliases) BY_NAME.set(alias, command)
}

export function findBuiltin(name: string): BuiltinCommand | null {
  return BY_NAME.get(name.trim().toLowerCase()) ?? null
}

export function isExcludedCommand(name: string): boolean {
  const clean = name.trim().toLowerCase()
  return EXCLUDED_COMMANDS.has(clean) || clean.startsWith('design-')
}

/** `/name rest` → `{ name, args }` for a message that is a slash command; null otherwise. */
export function parseSlash(text: string): { name: string; args: string } | null {
  const match = /^\/([A-Za-z0-9_:.-]+)(?:\s+([\s\S]*))?$/.exec(text.trim())
  return match ? { name: match[1], args: (match[2] ?? '').trim() } : null
}

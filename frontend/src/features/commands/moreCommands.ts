/**
 * The rest of spec §10.1's built-ins (W7 Batch F). Each one either does what Claude Code's does with what Orin has, or
 * — when Orin has no such thing — says so plainly and points to what it has. None is sent to the model by mistake
 * (a built-in without a handler would be).
 */
import type { ApiClient } from '../../api/client'
import { getInstallationStatus } from '../../api/installation'
import { getInstance } from '../../api/sessionTools'
import type { ThemeAccent } from '../../app/ThemeContext'

export type MoreCommandDeps = {
  client: ApiClient
  navigate: (path: string) => void
  send: (text: string) => Promise<void>
  notify: (text: string) => void
  openInfo: (panel: { kind: 'text'; title: string; lines: string[] }) => void
  setAccent?: (accent: ThemeAccent) => void
}

type Handler = (args: string) => Promise<void> | void

// Orin's own words for the prompt commands (not Claude Code's prompts).
export const SUBTASK_PROMPT = 'Delegue a tarefa a seguir a um subagente que receba o contexto desta conversa, espere o resultado e me resuma o que ele fez:'
export const DIFF_PROMPT = 'Mostre as mudanças atuais do diretório de trabalho: rode git status e git diff --stat (e o diff dos arquivos pequenos), sem alterar nada, e resuma o que mudou.'
export const LOOP_PROMPT = 'Use a ferramenta CronCreate para repetir nesta conversa, enquanto ela estiver aberta,'

const ACCENTS: Record<string, ThemeAccent> = {
  roxo: 'violet', violeta: 'violet', violet: 'violet', purple: 'violet', default: 'violet', padrao: 'violet', padrão: 'violet',
  azul: 'sapphire', blue: 'sapphire', sapphire: 'sapphire', safira: 'sapphire',
  verde: 'emerald', green: 'emerald', emerald: 'emerald', esmeralda: 'emerald',
  ambar: 'amber', âmbar: 'amber', amarelo: 'amber', amber: 'amber', yellow: 'amber', orange: 'amber', laranja: 'amber',
  mono: 'mono', cinza: 'mono', gray: 'mono', grey: 'mono',
}

/** Text answers: what Orin does instead, in one place so the wording stays honest and consistent. */
export const NOT_IN_ORIN: Record<string, { title: string; lines: string[] }> = {
  autocompact: { title: 'Compactação automática', lines: ['No Orin a compactação automática está sempre ligada: a conversa é resumida quando o contexto enche.', 'Para compactar agora, use /compact [instruções].'] },
  btw: { title: 'Pergunta paralela', lines: ['O Orin ainda não tem perguntas fora do histórico.', 'Para perguntar sem mexer nesta conversa, use /fork e pergunte na cópia.'] },
  cd: { title: 'Pasta de trabalho', lines: ['Para trocar a pasta desta sessão, use o seletor de pasta no topo da conversa.', 'Para acrescentar outra pasta, use /add-dir <pasta>.'] },
  effort: { title: 'Esforço', lines: ['O Orin não ajusta o esforço do modelo por sessão.', 'Escolha um modelo com /model; o esforço de um subagente vai no arquivo dele (/agents).'] },
  fast: { title: 'Modo rápido', lines: ['O Orin não tem um modo rápido.', 'Para respostas mais rápidas, escolha um modelo menor com /model.'] },
  'reload-plugins': { title: 'Plugins', lines: ['O Orin lê os plugins de novo a cada turno: a próxima mensagem já usa o que mudou.'] },
  'reload-skills': { title: 'Skills', lines: ['O Orin lê as skills de novo a cada turno: a próxima mensagem já usa o que mudou.'] },
  'auto-mode-setup': { title: 'Modo automático', lines: ['O modo automático do Orin não precisa de preparo: escolha "Automático" no modo de permissão (Shift+Tab).', 'As regras de permissão ficam em /permissions.'] },
  import: { title: 'Importar configurações', lines: ['O Orin já lê as configurações do Claude Code (~/.claude, .claude/ e CLAUDE.md) ao lado das suas: não há o que importar.'] },
  insights: { title: 'Relatório das sessões', lines: ['O Orin ainda não gera um relatório das sessões recentes.', 'O uso desta conversa está em /usage; o contexto, em /context.'] },
  'skill-doctor': { title: 'Custo das skills', lines: ['O Orin ainda não mostra o custo de cada skill.', 'O total que as skills ocupam no contexto está em /context.'] },
  ultrareview: { title: 'Revisão completa', lines: ['A revisão em vários agentes ainda não está disponível no Orin.', 'Use /review para revisar as mudanças, ou /security-review.'] },
  desktop: { title: 'App de desktop', lines: ['Para continuar no app, abra o Orin para desktop: ele mostra as mesmas conversas deste perfil local.'] },
  resume: { title: 'Retomar uma conversa', lines: ['As suas conversas ficam na barra lateral (Chats); Ctrl+K busca por título.'] },
}

export function moreCommandHandlers(deps: MoreCommandDeps): Record<string, Handler> {
  const answers = Object.fromEntries(Object.entries(NOT_IN_ORIN).map(([name, panel]) => [name, () => deps.openInfo({ kind: 'text', ...panel })]))
  return {
    ...answers,
    subtask: async (args) => {
      if (!args) { deps.notify('Diga a tarefa: /subtask <tarefa>'); return }
      await deps.send(`${SUBTASK_PROMPT}\n${args}`)
    },
    diff: () => deps.send(DIFF_PROMPT),
    loop: async (args) => {
      if (!args) { deps.notify('Diga o intervalo e o pedido: /loop 10m rode os testes'); return }
      const match = /^(\d+\s*[smhd])\s+([\s\S]+)$/i.exec(args.trim())
      await deps.send(match ? `${LOOP_PROMPT} a cada ${match[1]}, este pedido: ${match[2]}` : `${LOOP_PROMPT} num intervalo que faça sentido, este pedido: ${args}`)
    },
    loops: () => deps.navigate('/settings/schedules'),
    'pause-memory': () => deps.navigate('/settings/memory'),
    restart: () => deps.navigate('/settings/about'),
    color: (args) => {
      const wanted = ACCENTS[args.trim().toLowerCase() || 'default']
      if (!wanted || !deps.setAccent) {
        deps.notify('Cores: roxo, azul, verde, âmbar, mono ou default.')
        return
      }
      deps.setAccent(wanted)
      deps.notify(`Cor de destaque: ${args.trim() || 'default'}.`)
    },
    doctor: async () => {
      const [status, instance] = await Promise.allSettled([getInstallationStatus(deps.client), getInstance(deps.client)])
      const lines: string[] = []
      if (instance.status === 'fulfilled') {
        lines.push(`Orin ${instance.value.version} (${instance.value.mode === 'server' ? 'servidor compartilhado' : 'local'})`)
        const off = Object.entries(instance.value.capabilities).filter(([, on]) => !on).map(([name]) => name)
        lines.push(off.length ? `Desligado nesta instância: ${off.join(', ')}` : 'Todos os recursos desta instância estão ligados.')
      }
      if (status.status === 'fulfilled') {
        const value = status.value
        lines.push(`Instalação: ${value.installation_kind === 'development' ? 'desenvolvimento' : 'instalada'} · versões: ${value.installed_versions.map((item) => item.version).join(', ') || value.current_version}`)
        lines.push(value.update_available && value.latest_release ? `Há uma versão nova: ${value.latest_release.version} (Configurações › Sobre).`
          : value.latest_release_error ? 'Não foi possível verificar se há versão nova.' : 'O Orin está atualizado.')
      } else {
        lines.push('Não foi possível ler o estado da instalação.')
      }
      deps.openInfo({ kind: 'text', title: 'Diagnóstico', lines })
    },
  }
}

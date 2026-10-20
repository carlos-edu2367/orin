/** The single source of truth for settings navigation. */
import type { Capabilities } from '../../api/session'

export type SettingsBadge = 'memory' | 'providers' | 'skills' | 'mcp' | 'plugins' | 'schedules' | 'version'

export type SettingsItem = {
  id: string
  label: string
  path: string
  lede: string
  badge?: SettingsBadge
  /** `admin`: an administrator of a shared server; `host_folders`: a local Orin (the person's own files). */
  requires?: 'admin' | 'host_folders'
}

export type SettingsGroup = { title: string; items: SettingsItem[] }

export const SETTINGS_GROUPS: SettingsGroup[] = [
  {
    title: 'Sessão',
    items: [
      { id: 'general', label: 'General', path: '/settings/general', lede: 'Preferências locais e limites de execução do runtime.' },
      { id: 'memory', label: 'Memory', path: '/settings/memory', lede: 'Fatos que o agente lembra entre conversas.', badge: 'memory' },
    ],
  },
  {
    title: 'Inteligência',
    items: [
      { id: 'providers', label: 'Providers', path: '/settings/providers', lede: 'Configure ou revogue o acesso de cada provider. A chave nunca é reexibida depois do envio.', badge: 'providers' },
      { id: 'vision', label: 'Leitura visual', path: '/settings/vision', lede: 'Qual modelo lê imagens e páginas escaneadas quando o modelo do turno não enxerga.' },
    ],
  },
  {
    title: 'Extensões',
    items: [
      { id: 'skills', label: 'Skills', path: '/settings/skills', lede: 'Procedimentos que o agente carrega quando a tarefa pede.', badge: 'skills' },
      { id: 'mcp', label: 'MCP', path: '/settings/mcp', lede: 'Servidores MCP conectados e as tools que cada um publica.', badge: 'mcp' },
      { id: 'plugins', label: 'Plugins', path: '/settings/plugins', lede: 'Pacotes instalados e o que cada um contribui.', badge: 'plugins' },
    ],
  },
  {
    title: 'Orin Code',
    items: [
      { id: 'permissions', label: 'Permissões', path: '/settings/permissions', lede: 'As regras de permissão em vigor, de onde vem cada uma, e as que você pode mudar.', requires: 'host_folders' },
      { id: 'hooks', label: 'Hooks', path: '/settings/hooks', lede: 'Comandos que rodam em momentos do agente, e os arquivos onde ficam.', requires: 'host_folders' },
      { id: 'mcp-scopes', label: 'MCP por escopo', path: '/settings/mcp-scopes', lede: 'Servidores MCP dos arquivos de configuração, por escopo, e a aprovação dos servidores de um projeto.', requires: 'host_folders' },
      { id: 'agents', label: 'Agentes', path: '/settings/agents', lede: 'Subagentes definidos em arquivos: quando usar, instruções, ferramentas e modelo.', requires: 'host_folders' },
      { id: 'output-styles', label: 'Estilo de resposta', path: '/settings/output-styles', lede: 'Como o agente escreve as respostas.', requires: 'host_folders' },
      { id: 'keybindings', label: 'Atalhos', path: '/settings/keybindings', lede: 'As teclas da interface, no formato do keybindings.json do Claude Code.', requires: 'host_folders' },
      { id: 'trusted-folders', label: 'Pastas confiáveis', path: '/settings/trusted-folders', lede: 'As pastas cujas configurações e extensões o Orin carrega.', requires: 'host_folders' },
    ],
  },
  {
    title: 'Sistema',
    items: [
      { id: 'workspace', label: 'Workspace', path: '/settings/workspace', lede: 'Onde os arquivos de cada conversa são gravados.' },
      { id: 'background', label: 'Segundo plano', path: '/settings/background', lede: 'Iniciar com o computador e o que acontece ao fechar a janela.' },
      { id: 'browser', label: 'Navegador', path: '/settings/browser', lede: 'O Chromium que o agente usa para abrir páginas. É um download opcional.' },
      { id: 'schedules', label: 'Agendamentos', path: '/settings/schedules', lede: 'Conversas que começam sozinhas em um horário.', badge: 'schedules' },
      { id: 'users', label: 'Perfis', path: '/settings/users', lede: 'Quem acessa esta instância e com qual papel.', requires: 'admin' },
      { id: 'about', label: 'Sobre', path: '/settings/about', lede: 'Versão instalada, atualização e remoção.', badge: 'version' },
    ],
  },
]

export function settingsItems(): SettingsItem[] {
  return SETTINGS_GROUPS.flatMap((group) => group.items)
}

export type SettingsAudience = { isAdmin: boolean; capabilities: Capabilities }

export function visibleSettingsGroups(audience: SettingsAudience): SettingsGroup[] {
  return SETTINGS_GROUPS
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => (item.requires !== 'admin' || (audience.isAdmin && audience.capabilities.user_admin))
        && (item.requires !== 'host_folders' || audience.capabilities.host_folders)),
    }))
    .filter((group) => group.items.length > 0)
}

export function visibleSettingsItems(audience: SettingsAudience): SettingsItem[] {
  return visibleSettingsGroups(audience).flatMap((group) => group.items)
}

export function findSettingsItem(pathname: string): SettingsItem | undefined {
  return settingsItems()
    .filter((item) => pathname === item.path || pathname.startsWith(`${item.path}/`))
    .sort((left, right) => right.path.length - left.path.length)[0]
}

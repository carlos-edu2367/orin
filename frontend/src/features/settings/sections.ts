/** The single source of truth for settings navigation. */
import type { Capabilities } from '../../api/session'

export type SettingsBadge = 'memory' | 'providers' | 'skills' | 'mcp' | 'plugins' | 'schedules' | 'version'

export type SettingsItem = {
  id: string
  label: string
  path: string
  lede: string
  badge?: SettingsBadge
  requires?: 'admin'
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
    .map((group) => ({ ...group, items: group.items.filter((item) => item.requires !== 'admin' || (audience.isAdmin && audience.capabilities.user_admin)) }))
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

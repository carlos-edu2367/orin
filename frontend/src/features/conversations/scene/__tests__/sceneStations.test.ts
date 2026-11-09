import { describe, expect, it } from 'vitest'
import {
  STATION_LABELS,
  STATION_SHORT_LABELS,
  TOOL_STATION_TABLE,
  isAgentTool,
  stationForTool,
} from '../sceneStations'

describe('sceneStations', () => {
  it('mapeia todas as ferramentas da tabela da spec para a estação correta', () => {
    // Terminal
    expect(stationForTool('run_command')).toBe('terminal')
    expect(stationForTool('powershell')).toBe('terminal')
    expect(stationForTool('PowerShell')).toBe('terminal')
    expect(stationForTool('verify_project')).toBe('terminal')
    expect(stationForTool('VerifyProject')).toBe('terminal')
    expect(stationForTool('bash')).toBe('terminal')
    expect(stationForTool('Bash')).toBe('terminal')
    expect(stationForTool('task_output')).toBe('terminal')
    expect(stationForTool('TaskOutput')).toBe('terminal')
    expect(stationForTool('task_stop')).toBe('terminal')
    expect(stationForTool('TaskStop')).toBe('terminal')

    // Files
    expect(stationForTool('read_file')).toBe('files')
    expect(stationForTool('ReadFile')).toBe('files')
    expect(stationForTool('Read')).toBe('files')
    expect(stationForTool('read')).toBe('files')
    expect(stationForTool('list_files')).toBe('files')
    expect(stationForTool('ListFiles')).toBe('files')
    expect(stationForTool('ls')).toBe('files')
    expect(stationForTool('glob_files')).toBe('files')
    expect(stationForTool('glob')).toBe('files')
    expect(stationForTool('Glob')).toBe('files')
    expect(stationForTool('search_files')).toBe('files')
    expect(stationForTool('search_code')).toBe('files')
    expect(stationForTool('SearchCode')).toBe('files')
    expect(stationForTool('view_file')).toBe('files')
    expect(stationForTool('ViewFile')).toBe('files')
    expect(stationForTool('transcribe_pdf')).toBe('files')
    expect(stationForTool('TranscribePdf')).toBe('files')
    expect(stationForTool('grep')).toBe('files')
    expect(stationForTool('Grep')).toBe('files')
    expect(stationForTool('project_map')).toBe('files')
    expect(stationForTool('ProjectMap')).toBe('files')

    // Writing
    expect(stationForTool('write_file')).toBe('writing')
    expect(stationForTool('WriteFile')).toBe('writing')
    expect(stationForTool('Write')).toBe('writing')
    expect(stationForTool('write')).toBe('writing')
    expect(stationForTool('edit_file')).toBe('writing')
    expect(stationForTool('EditFile')).toBe('writing')
    expect(stationForTool('Edit')).toBe('writing')
    expect(stationForTool('edit')).toBe('writing')
    expect(stationForTool('notebook_edit')).toBe('writing')
    expect(stationForTool('NotebookEdit')).toBe('writing')
    expect(stationForTool('multi_edit')).toBe('writing')

    // Browser
    expect(stationForTool('fetch_url')).toBe('browser')
    expect(stationForTool('web_fetch')).toBe('browser')
    expect(stationForTool('WebFetch')).toBe('browser')
    expect(stationForTool('web_search')).toBe('browser')
    expect(stationForTool('browser_navigate')).toBe('browser')
    expect(stationForTool('browser_click')).toBe('browser')
    expect(stationForTool('BrowserSnapshot')).toBe('browser')
    expect(stationForTool('preview_start')).toBe('browser')
    expect(stationForTool('PreviewStart')).toBe('browser')
    expect(stationForTool('preview_stop')).toBe('browser')
    expect(stationForTool('preview_open')).toBe('browser')
    expect(stationForTool('preview_logs')).toBe('browser')

    // Planning
    expect(stationForTool('write_contract')).toBe('planning')
    expect(stationForTool('todo_write')).toBe('planning')
    expect(stationForTool('TodoWrite')).toBe('planning')
    expect(stationForTool('task_create')).toBe('planning')
    expect(stationForTool('TaskCreate')).toBe('planning')
    expect(stationForTool('task_update')).toBe('planning')
    expect(stationForTool('task_list')).toBe('planning')
    expect(stationForTool('task_get')).toBe('planning')
    expect(stationForTool('task_custom_step')).toBe('planning')
    expect(stationForTool('enter_plan_mode')).toBe('planning')
    expect(stationForTool('EnterPlanMode')).toBe('planning')
    expect(stationForTool('exit_plan_mode')).toBe('planning')
    expect(stationForTool('ExitPlanMode')).toBe('planning')

    // Meeting
    expect(stationForTool('run_subagent')).toBe('meeting')
    expect(stationForTool('ask_agent')).toBe('meeting')
    expect(stationForTool('ask_agents')).toBe('meeting')
    expect(stationForTool('send_message')).toBe('meeting')
    expect(stationForTool('SendMessage')).toBe('meeting')
    expect(stationForTool('agent')).toBe('meeting')
    expect(stationForTool('Agent')).toBe('meeting')
    expect(stationForTool('task')).toBe('meeting')
    expect(stationForTool('Task')).toBe('meeting')
    expect(stationForTool('ask_user')).toBe('meeting')
    expect(stationForTool('ask_user_question')).toBe('meeting')
    expect(stationForTool('AskUserQuestion')).toBe('meeting')

    // Extensions
    expect(stationForTool('mcp__github__create_issue')).toBe('extensions')
    expect(stationForTool('mcp_orin_Read')).toBe('extensions')
    expect(stationForTool('use_skill')).toBe('extensions')
    expect(stationForTool('skill')).toBe('extensions')
    expect(stationForTool('Skill')).toBe('extensions')
    expect(stationForTool('install_plugin')).toBe('extensions')
    expect(stationForTool('InstallPlugin')).toBe('extensions')
    expect(stationForTool('configure_mcp')).toBe('extensions')
    expect(stationForTool('ConfigureMcp')).toBe('extensions')
  })

  it('usa fallback por toolKind quando toolName é desconhecido ou vazio, e generic caso contrário', () => {
    expect(stationForTool('unknown_tool', 'terminal')).toBe('terminal')
    expect(stationForTool(null, 'filesystem')).toBe('files')
    expect(stationForTool(undefined, 'web')).toBe('browser')
    expect(stationForTool('', 'browser')).toBe('browser')
    expect(stationForTool('custom', 'agent')).toBe('meeting')
    expect(stationForTool('custom', 'user_input')).toBe('meeting')
    expect(stationForTool('custom', 'memory')).toBe('planning')
    expect(stationForTool('custom', 'artifact')).toBe('writing')
    expect(stationForTool('completely_unknown_tool', 'unknown_kind')).toBe('generic')
    expect(stationForTool(null, null)).toBe('generic')
    expect(stationForTool()).toBe('generic')
  })

  it('identifica ferramentas de delegação de agentes e expõe rótulos pt-BR das estações', () => {
    expect(isAgentTool('run_subagent')).toBe(true)
    expect(isAgentTool('Agent')).toBe(true)
    expect(isAgentTool('Task')).toBe(true)
    expect(isAgentTool('send_message')).toBe(true)
    expect(isAgentTool('custom', 'agent')).toBe(true)
    expect(isAgentTool('ask_user_question', 'user_input')).toBe(false)
    expect(isAgentTool('read_file', 'filesystem')).toBe(false)

    expect(TOOL_STATION_TABLE.read_file).toBe('files')
    expect(STATION_LABELS).toEqual({
      terminal: 'Mesa do terminal',
      files: 'Estante de arquivos',
      writing: 'Bancada de escrita',
      browser: 'Janela do navegador',
      planning: 'Quadro de planos',
      meeting: 'Mesa de reunião',
      extensions: 'Painel de extensões',
      generic: 'Mesa geral',
      lounge: 'Sofá',
    })
    expect(STATION_SHORT_LABELS).toEqual({
      terminal: 'Terminal',
      files: 'Arquivos',
      writing: 'Escrita',
      browser: 'Navegador',
      planning: 'Planos',
      meeting: 'Reunião',
      extensions: 'Extensões',
      generic: 'Mesa geral',
      lounge: 'Sofá',
    })
    for (const key of Object.keys(STATION_SHORT_LABELS) as Array<keyof typeof STATION_SHORT_LABELS>) {
      expect(STATION_LABELS[key].toLowerCase()).toContain(STATION_SHORT_LABELS[key].toLowerCase())
    }
  })

  it('leva os nomes reais do backend (MCP, CreateAgent, processos) e os tool_kind emitidos à estação certa', () => {
    for (const name of [
      'call_mcp_tool',
      'CallMcpTool',
      'inspect_mcp_server',
      'InspectMcpServer',
      'refresh_mcp_tools',
      'search_mcp_tools',
      'list_mcp_resources',
      'read_mcp_resource',
      'ReadMcpResourceDir',
      'run_mcp_thing',
    ]) {
      expect(stationForTool(name)).toBe('extensions')
    }
    expect(stationForTool('create_agent')).toBe('meeting')
    expect(stationForTool('CreateAgent')).toBe('meeting')
    expect(isAgentTool('CreateAgent')).toBe(true)
    expect(stationForTool('read_process_output')).toBe('terminal')
    expect(stationForTool('stop_process')).toBe('terminal')

    expect(stationForTool('ferramenta_x', 'mcp')).toBe('extensions')
    expect(stationForTool('ferramenta_x', 'skill')).toBe('extensions')
    expect(stationForTool('ferramenta_x', 'plugin')).toBe('extensions')
    expect(stationForTool('ferramenta_x', 'planning')).toBe('planning')
    expect(stationForTool('ferramenta_x', 'shell')).toBe('terminal')
  })
})

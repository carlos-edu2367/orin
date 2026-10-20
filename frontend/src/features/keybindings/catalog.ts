/**
 * Contexts and actions of Claude Code's `keybindings.json` (its public documentation, 2026-10-20), and the defaults Orin
 * uses in a browser and in the desktop app. A browser keeps some keys for itself (`ctrl+t`, `ctrl+w`, `ctrl+n`,
 * `ctrl+tab`, `ctrl+l`, …), so those actions have no browser default.
 */

export const CLAUDE_CONTEXTS = [
  'Global', 'Chat', 'Autocomplete', 'Settings', 'Confirmation', 'Tabs', 'Help', 'Transcript', 'HistorySearch', 'Task',
  'ThemePicker', 'Attachments', 'Footer', 'MessageSelector', 'DiffDialog', 'DiffPanel', 'ModelPicker', 'EffortSlider',
  'Select', 'Plugin', 'AbovePrompt', 'AbovePromptInput', 'AbovePromptSelect', 'Pane', 'PaneField', 'Agents', 'Scroll',
] as const
export type KeyContext = (typeof CLAUDE_CONTEXTS)[number]

export type ActionSpec = { context: KeyContext; label: string; browser: string[]; electron: string[] }

const both = (context: KeyContext, label: string, ...keys: string[]): ActionSpec => ({ context, label, browser: keys, electron: keys })

/** What Orin's interface does on each action (the rest of Claude Code's actions are recognized and reported). */
export const ACTIONS: Record<string, ActionSpec> = {
  'app:toggleTranscript': both('Global', 'Abrir ou fechar a transcrição detalhada', 'ctrl+o'),
  'app:toggleTodos': { context: 'Global', label: 'Mostrar ou esconder as tarefas do agente', browser: [], electron: ['ctrl+t'] },
  'chat:submit': both('Chat', 'Enviar a mensagem', 'enter'),
  'chat:newline': both('Chat', 'Nova linha', 'shift+enter', 'ctrl+j'),
  'chat:cancel': both('Chat', 'Interromper o agente', 'escape'),
  'chat:cycleMode': both('Chat', 'Trocar o modo de permissão', 'shift+tab'),
  'chat:modelPicker': both('Chat', 'Escolher o modelo', 'alt+p'),
  'chat:thinkingToggle': both('Chat', 'Ligar ou desligar o raciocínio estendido', 'alt+t'),
  'chat:sendNow': both('Chat', 'Enviar agora (interrompe e envia)', 'ctrl+enter'),
  'chat:clearInput': { context: 'Chat', label: 'Limpar o campo de mensagem', browser: [], electron: ['ctrl+l'] },
  'history:search': both('Chat', 'Buscar no histórico de mensagens', 'ctrl+r'),
  'history:previous': both('Chat', 'Mensagem anterior do histórico', 'up'),
  'history:next': both('Chat', 'Próxima mensagem do histórico', 'down'),
  'autocomplete:accept': both('Autocomplete', 'Aceitar a sugestão', 'tab'),
  'autocomplete:dismiss': both('Autocomplete', 'Fechar as sugestões', 'escape'),
  'autocomplete:previous': both('Autocomplete', 'Sugestão anterior', 'up'),
  'autocomplete:next': both('Autocomplete', 'Próxima sugestão', 'down'),
  'confirm:yes': both('Confirmation', 'Confirmar', 'enter'),
  'confirm:no': both('Confirmation', 'Recusar', 'escape'),
  'confirm:previous': both('Confirmation', 'Opção anterior', 'up'),
  'confirm:next': both('Confirmation', 'Próxima opção', 'down'),
  'transcript:toggleShowAll': both('Transcript', 'Mostrar tudo na transcrição', 'ctrl+e'),
  'transcript:exit': both('Transcript', 'Fechar a transcrição', 'escape', 'q'),
  'historySearch:next': both('HistorySearch', 'Próximo resultado', 'ctrl+r'),
  'historySearch:accept': both('HistorySearch', 'Usar o resultado', 'tab'),
  'historySearch:cancel': both('HistorySearch', 'Cancelar a busca', 'escape'),
  'historySearch:execute': both('HistorySearch', 'Enviar o resultado', 'enter'),
  'select:next': both('Select', 'Próximo item', 'down'),
  'select:previous': both('Select', 'Item anterior', 'up'),
  'select:accept': both('Select', 'Escolher', 'enter'),
  'select:cancel': both('Select', 'Cancelar', 'escape'),
  'tabs:next': both('Tabs', 'Próxima aba', 'right'),
  'tabs:previous': both('Tabs', 'Aba anterior', 'left'),
  'help:dismiss': both('Help', 'Fechar a ajuda', 'escape'),
  'settings:search': both('Settings', 'Buscar nas configurações', '/'),
  'diff:dismiss': both('DiffDialog', 'Fechar o diff', 'escape'),
  'diff:previousFile': both('DiffDialog', 'Arquivo anterior', 'up'),
  'diff:nextFile': both('DiffDialog', 'Próximo arquivo', 'down'),
  'modelPicker:decreaseEffort': both('ModelPicker', 'Menos esforço', 'left'),
  'modelPicker:increaseEffort': both('ModelPicker', 'Mais esforço', 'right'),
  'effortSlider:decreaseEffort': both('EffortSlider', 'Menos esforço', 'left'),
  'effortSlider:increaseEffort': both('EffortSlider', 'Mais esforço', 'right'),
}

/** Claude Code actions Orin's interface does not have (a binding to one is kept but reported as a warning). */
export const UNSUPPORTED_ACTIONS = new Set([
  'app:interrupt', 'app:exit', 'app:redraw', 'app:toggleReplTab', 'app:toggleDiffNoiseFilter', 'app:toggleDiffPreSession',
  'app:cycleDiffBase', 'app:diffFileListUp', 'app:diffFileListDown', 'chat:clearScreen', 'chat:killAgents', 'chat:fastMode',
  'chat:queueSubmit', 'chat:undo', 'chat:externalEditor', 'chat:stash', 'chat:imagePaste', 'abovePrompt:toggle', 'abovePrompt:focus',
  'voice:pushToTalk', 'task:background', 'confirm:nextField', 'confirm:previousField', 'confirm:toggle', 'confirm:cycleMode',
  'permission:toggleDebug', 'historySearch:cycleScope', 'diff:previousSource', 'diff:nextSource', 'diff:back', 'scroll:pageUp',
  'scroll:pageDown', 'scroll:fullPageUp', 'scroll:fullPageDown', 'scroll:top', 'scroll:bottom', 'scroll:lineUp', 'scroll:lineDown',
  'scroll:halfPageUp', 'scroll:halfPageDown', 'selection:copy', 'selection:clear', 'selection:extendLeft', 'selection:extendRight',
  'selection:extendUp', 'selection:extendDown', 'selection:extendLineStart', 'selection:extendLineEnd',
  'theme:toggleSyntaxHighlighting', 'attachments:next', 'attachments:previous', 'attachments:remove', 'attachments:exit',
  'footer:next', 'footer:previous', 'footer:up', 'footer:down', 'footer:openSelected', 'footer:clearSelection', 'footer:close',
  'footer:dismiss', 'modelPicker:thisSessionOnly', 'effortSlider:toggleUltracode', 'effortSlider:thisSessionOnly',
  'select:pageUp', 'select:pageDown', 'select:first', 'select:last', 'plugin:toggle', 'plugin:install', 'plugin:favorite',
  'abovePrompt:next', 'abovePrompt:previous', 'abovePrompt:press', 'abovePrompt:leave', 'abovePrompt:highlightNext',
  'abovePrompt:highlightPrevious', 'pane:scrollUp', 'pane:scrollDown', 'pane:pageUp', 'pane:pageDown', 'pane:top',
  'pane:bottom', 'pane:grow', 'pane:shrink', 'pane:close', 'pane:next', 'pane:previous', 'settings:retry',
  'agents:switchView', 'agents:togglePin', 'agents:find', 'agents:rename', 'agents:previousGroup', 'agents:nextGroup',
])

/** Keys no binding may take: copy, paste and the text editing every field relies on (Claude Code reserves ctrl+c/ctrl+d). */
export const RESERVED_KEYS = new Set(['ctrl+c', 'ctrl+d', 'ctrl+v', 'ctrl+x', 'ctrl+a', 'ctrl+z', 'cmd+c', 'cmd+v', 'cmd+x', 'cmd+a', 'cmd+z', 'ctrl+m', 'ctrl+i', 'ctrl+h', 'ctrl+['])

/** Keys a browser keeps for itself: they work only in the desktop app. */
export const BROWSER_RESERVED_KEYS = new Set([
  'ctrl+t', 'ctrl+w', 'ctrl+n', 'ctrl+tab', 'ctrl+shift+tab', 'ctrl+shift+t', 'ctrl+shift+n', 'ctrl+shift+w', 'ctrl+l',
  'ctrl+q', 'alt+f4', 'f11', 'cmd+t', 'cmd+w', 'cmd+n', 'cmd+q', 'cmd+l', 'ctrl+pageup', 'ctrl+pagedown',
])

export type Environment = 'browser' | 'electron'

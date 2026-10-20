/** Fixtures and routes for the W7 Batch E settings editors (browser tests). Every other API call is aborted. */
import type { Page, Route } from '@playwright/test'

export type SettingsRequest = { method: string; path: string; body: unknown }

const PROJECT = 'C:/Projetos/pagamentos'
export const EFFECTIVE = {
  project_root: PROJECT, trusted: true, values: {}, provenance: {}, layers: [], files: {}, errors: [], untrusted_ignored: [],
  writable: { user: 'C:/Users/p/.orin/settings.json', project: `${PROJECT}/.orin/settings.json`, local: `${PROJECT}/.orin/settings.local.json` },
  permissions: {
    allow: [
      { rule: 'Bash(npm test:*)', layer: 'project', file: `${PROJECT}/.orin/settings.json`, duplicate: false },
      { rule: 'Read(./src/**)', layer: 'user', file: 'C:/Users/p/.orin/settings.json', duplicate: false },
    ],
    ask: [{ rule: 'Bash(git push:*)', layer: 'local', file: `${PROJECT}/.orin/settings.local.json`, duplicate: false }],
    deny: [{ rule: 'Read(./.env)', layer: 'managed', file: 'C:/ProgramData/Orin/managed-settings.json', duplicate: false }],
    additionalDirectories: [],
  },
}
export const HOOKS = {
  disabled: false, errors: [],
  effective: [
    { event: 'PostToolUse', matcher: 'Edit|Write', type: 'command', source: 'project', label: 'npm run lint', timeout: 60 },
    { event: 'Stop', matcher: '', type: 'command', source: 'user', label: 'notify-send "Orin terminou"', timeout: 10 },
  ],
  editable: {
    user: { file: 'C:/Users/p/.orin/settings.json', hooks: { Stop: [{ hooks: [{ type: 'command', command: 'notify-send "Orin terminou"', timeout: 10 }] }] } },
    project: { file: `${PROJECT}/.orin/settings.json`, hooks: { PostToolUse: [{ matcher: 'Edit|Write', hooks: [{ type: 'command', command: 'npm run lint', timeout: 60 }] }] } },
  },
}
export const AGENTS = {
  errors: [], shadowed: [],
  agents: [
    { name: 'revisor-de-testes', description: 'Revisa testes novos e aponta casos que faltam.', origin: 'project', path: `${PROJECT}/.orin/agents/revisor-de-testes.md`, editable: true, prompt: 'Revise os testes.', tools: ['Read', 'Grep', 'Glob'], disallowed_tools: [], model: 'anthropic/claude-haiku-5.5', permission_mode: 'plan', max_turns: 20, effort: 'medium' },
    { name: 'explorador', description: 'Procura código no repositório.', origin: 'plugin', path: null, editable: false, prompt: '', tools: null, disallowed_tools: [] },
  ],
}
export const STYLES = {
  active: 'explanatory', active_layer: 'local',
  styles: [
    { key: 'default', name: 'Padrão', description: 'Respostas diretas para trabalho de engenharia.', source: 'builtin' },
    { key: 'explanatory', name: 'Explicativo', description: 'Explica as escolhas enquanto trabalha.', source: 'builtin' },
    { key: 'learning', name: 'Aprendizado', description: 'Pede que você escreva partes do código.', source: 'builtin' },
  ],
}
export const KEYBINDINGS = {
  orin: { path: 'C:/Users/p/.orin/keybindings.json', text: '{\n  "bindings": [\n    { "context": "Chat", "bindings": { "ctrl+enter": "chat:submit" } }\n  ]\n}\n', error: null },
  claude: { path: 'C:/Users/p/.claude/keybindings.json', text: null, error: null },
}

async function fulfill(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

/** Answers the settings routes; `override(request)` answers first (a refusal, an empty list). */
export async function mockSettings(page: Page, override: (request: SettingsRequest) => { status?: number; body: unknown } | undefined = () => undefined): Promise<SettingsRequest[]> {
  const sent: SettingsRequest[] = []
  await page.route('**/v1/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    let body: unknown
    try { body = request.postDataJSON() } catch { body = undefined }
    sent.push({ method, path, body })
    const custom = override({ method, path, body })
    if (custom) { await fulfill(route, custom.body, custom.status ?? 200); return }
    if (path === '/v1/settings/effective') { await fulfill(route, EFFECTIVE); return }
    if (path === '/v1/settings/permissions') { await fulfill(route, { permissions: EFFECTIVE.permissions }); return }
    if (path === '/v1/settings/hooks') { await fulfill(route, HOOKS); return }
    if (path.startsWith('/v1/settings/agents')) { await fulfill(route, AGENTS); return }
    if (path === '/v1/settings/output-styles') { await fulfill(route, STYLES); return }
    if (path === '/v1/settings/keybindings') { await fulfill(route, KEYBINDINGS); return }
    if (path.startsWith('/v1/settings/trusted-folders')) { await fulfill(route, { folders: [PROJECT] }); return }
    await route.abort() // the settings shell's badges are runtime data: keep them out of the baselines
  })
  return sent
}

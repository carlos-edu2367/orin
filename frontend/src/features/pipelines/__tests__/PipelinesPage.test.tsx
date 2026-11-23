import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { PipelinesPage } from '../PipelinesPage'
import { apiError, mockApi, rawDetail, rawEstimate, rawList, rawPipeline } from './fixtures'

function renderPage(api: ReturnType<typeof mockApi>, path = '/pipelines?conversation=conv-1') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/pipelines" element={<PipelinesPage client={api.client} />} />
        <Route path="/projects/:projectId/pipelines" element={<PipelinesPage client={api.client} />} />
      </Routes>
    </MemoryRouter>,
  )
}

const baseRoutes = {
  'GET /v1/pipeline-runs': () => ({ runs: [] }),
  'GET /v1/pipelines/implementar-verificar': () => rawDetail(),
  'POST /v1/pipelines/implementar-verificar/estimate': () => rawEstimate(),
}

describe('PipelinesPage', () => {
  it('groups the list by origin and shows what each pipeline needs', async () => {
    const api = mockApi({
      ...baseRoutes,
      'GET /v1/pipelines': () => rawList({
        pipelines: [
          rawPipeline(),
          rawPipeline({ id: 'meu-fluxo', source: 'user', name: 'Meu fluxo', approval_required: false, gate_commands: 0 }),
          rawPipeline({ id: 'do-repo', source: 'project', name: 'Do repositório', approval_required: true, approved: false, gate_commands: 2 }),
        ],
      }),
    })
    renderPage(api)
    const list = await screen.findByTestId('pipeline-list')
    const groups = within(list).getAllByRole('region').map((region) => region.getAttribute('aria-label'))
    expect(groups.slice(0, 3)).toEqual(['Prontos', 'Seus pipelines', 'Do projeto'])
    expect(within(screen.getByTestId('pipeline-row-do-repo')).getByText('Aprovação necessária')).toBeInTheDocument()
    expect(within(screen.getByTestId('pipeline-row-do-repo')).getByText('2 comandos')).toBeInTheDocument()
  })

  it('shows the errors of a broken file with their line, and the shadowed ones', async () => {
    const api = mockApi({
      ...baseRoutes,
      'GET /v1/pipelines': () => rawList({
        pipelines: [
          rawPipeline({
            id: 'quebrado', source: 'user', name: 'quebrado', valid: false, steps: [], inputs: [],
            errors: [{ code: 'E_UNKNOWN_KEY', message: 'chave desconhecida `agnt` (você quis dizer `agent`?)', line: 7, column: 5, path: 'steps[0].agnt' }],
          }),
        ],
        shadowed: [{ id: 'pesquisa', source: 'builtin', definition_hash: 'b'.repeat(64) }],
      }),
    })
    renderPage(api)
    const errors = await screen.findByRole('list', { name: 'Erros de quebrado' })
    expect(errors).toHaveTextContent('linha 7')
    expect(errors).toHaveTextContent('você quis dizer `agent`')
    expect(screen.getByTestId('pipeline-row-quebrado')).toHaveTextContent('Com erros')
    expect(screen.getByTestId('pipeline-shadowed')).toHaveTextContent('pesquisa')
  })

  it('an untrusted project shows only the count of ignored pipelines and the action to trust the folder', async () => {
    const api = mockApi({ ...baseRoutes, 'GET /v1/pipelines': () => rawList({ project_skipped: 3 }) })
    renderPage(api)
    const note = await screen.findByTestId('project-skipped')
    expect(note).toHaveTextContent('3 pipelines do projeto ignorados: pasta não confiável')
    const link = within(note).getByRole('link', { name: 'Confiar nesta pasta' })
    expect(link).toHaveAttribute('href', '/settings/trusted-folders?conversation=conv-1')
    expect(screen.queryByTestId('pipeline-row-do-repo')).not.toBeInTheDocument()
  })

  it('with pipelines switched off (E_DISABLED) it lists nothing and explains where to turn them on', async () => {
    const api = mockApi({
      'GET /v1/pipelines': () => rawList({ enabled: false, pipelines: [], errors: [{ code: 'E_DISABLED', message: 'desligado', line: null, column: null, path: null }] }),
      'GET /v1/pipeline-runs': () => ({ runs: [] }),
    })
    renderPage(api)
    const note = await screen.findByTestId('pipelines-disabled')
    expect(note).toHaveTextContent('Pipelines desligados')
    expect(within(note).getByRole('link', { name: /Configurações > Orquestração/ })).toHaveAttribute('href', '/settings/orchestration')
    expect(screen.queryByTestId('pipeline-row-implementar-verificar')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Novo pipeline' })).toBeDisabled()
  })

  it('opens the run form already filled in from /pipeline chave=valor', async () => {
    const api = mockApi({ ...baseRoutes, 'GET /v1/pipelines': () => rawList() })
    renderPage(api, '/pipelines?conversation=conv-1&pipeline=implementar-verificar&run=1&in.objetivo=criar%20o%20login')
    expect(await screen.findByLabelText(/objetivo/)).toHaveValue('criar o login')
    expect(screen.getByRole('tab', { name: 'Rodar' })).toHaveAttribute('aria-selected', 'true')
  })

  it('shows the YAML and the commands for review in the definition tab', async () => {
    const user = userEvent.setup()
    const api = mockApi({ ...baseRoutes, 'GET /v1/pipelines': () => rawList() })
    renderPage(api, '/pipelines?conversation=conv-1&pipeline=implementar-verificar')
    await user.click(await screen.findByRole('tab', { name: 'Definição' }))
    expect(await screen.findByTestId('pipeline-yaml')).toHaveTextContent('id: implementar-verificar')
    expect(screen.getByTestId('gate-review')).toHaveTextContent('pytest -q')
  })

  it('on a project route the newest chat of the project is the context for the folder', async () => {
    const api = mockApi({
      'GET /v1/projects/sidebar': () => ({
        items: [{
          project_id: 'proj-1', name: 'Loja', description: null,
          chats: [
            { conversation_id: 'old', title: 'Antiga', state: 'idle', updated_at: '2026-11-01T10:00:00Z', mode: 'code' },
            { conversation_id: 'new', title: 'Recente', state: 'idle', updated_at: '2026-11-22T10:00:00Z', mode: 'code' },
          ],
        }],
      }),
      ...baseRoutes,
      'GET /v1/pipelines': () => rawList(),
    })
    renderPage(api, '/projects/proj-1/pipelines')
    await waitFor(() => expect(api.calls.some((call) => call.key === 'GET /v1/pipelines' && call.query.get('conversation_id') === 'new')).toBe(true))
    expect(await screen.findByRole('heading', { name: 'Pipelines · Loja' })).toBeInTheDocument()
  })

  it('explains an unavailable server (shared) instead of an empty page', async () => {
    const api = mockApi({ 'GET /v1/pipelines': () => apiError(404, 'NOT_FOUND', 'nope') as never, 'GET /v1/pipeline-runs': () => ({ runs: [] }) })
    renderPage(api)
    expect(await screen.findByRole('alert')).toHaveTextContent(/não estão disponíveis neste Orin/)
  })
})

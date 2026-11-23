import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { parsePipelineDetail } from '../../../api/pipelines'
import { idOfText, PipelineEditor } from '../PipelineEditor'
import { apiError, mockApi, rawDetail } from './fixtures'

const LONG = { timeout: 4000 }
const ERROR = { code: 'E_UNKNOWN_KEY', message: 'chave desconhecida `agnt` (você quis dizer `agent`?)', line: 3, column: 5, path: 'steps[0].agnt' }
const TEXT = 'version: 1\nid: meu-fluxo\nsteps:\n  - id: a\n    agnt: x\n'

function setup(options: { errors?: unknown[]; save?: () => unknown; original?: boolean } = {}) {
  const api = mockApi({
    'POST /v1/pipelines/validate': () => ({ valid: (options.errors ?? []).length === 0, id: 'meu-fluxo', errors: options.errors ?? [], warnings: [] }),
    'PUT /v1/pipelines/meu-fluxo': () => (options.save ? options.save() : rawDetail({ id: 'meu-fluxo' })),
  })
  const onSaved = vi.fn()
  const onReload = vi.fn()
  const original = options.original ? parsePipelineDetail(rawDetail({ id: 'meu-fluxo', source: 'user', definition_hash: 'c'.repeat(64) })) : null
  render(<PipelineEditor client={api.client} initialText={TEXT} original={original} conversationId="conv-1" scope="user" onSaved={onSaved} onReload={onReload} />)
  return { ...api, onSaved, onReload }
}

describe('PipelineEditor', () => {
  it('reads the pipeline id from the id: line', () => {
    expect(idOfText(TEXT)).toBe('meu-fluxo')
    expect(idOfText('name: sem id\n')).toBe('')
  })

  it('validates after a pause in typing and lists the errors with their line and the API suggestion', async () => {
    const { count } = setup({ errors: [ERROR] })
    const list = await screen.findByTestId('editor-errors', undefined, LONG)
    expect(list).toHaveTextContent('linha 3, coluna 5')
    expect(list).toHaveTextContent('você quis dizer `agent`')
    expect(screen.getByTestId('editor-status')).toHaveTextContent('1 erro')
    expect(count('POST /v1/pipelines/validate')).toBe(1)
  })

  it('debounces: typing does not validate on every key', async () => {
    const user = userEvent.setup()
    const { count } = setup({ errors: [] })
    await waitFor(() => expect(screen.getByTestId('editor-status')).toHaveTextContent('Sem erros'), LONG)
    const before = count('POST /v1/pipelines/validate')
    await user.type(screen.getByLabelText('Texto YAML do pipeline'), 'abc')
    expect(count('POST /v1/pipelines/validate')).toBe(before)
    await waitFor(() => expect(count('POST /v1/pipelines/validate')).toBe(before + 1), LONG)
  })

  it('takes the cursor to the line of an error', async () => {
    const user = userEvent.setup()
    setup({ errors: [ERROR] })
    await user.click(await screen.findByRole('button', { name: 'linha 3, coluna 5' }, LONG))
    const area = screen.getByLabelText('Texto YAML do pipeline') as HTMLTextAreaElement
    const lineThreeStart = TEXT.split('\n').slice(0, 2).join('\n').length + 1
    expect(area.selectionStart).toBe(lineThreeStart)
    expect(document.activeElement).toBe(area)
  })

  it('keeps "Gravar" disabled while there is an error and enables it when the text is valid', async () => {
    const bad = setup({ errors: [ERROR] })
    await screen.findByTestId('editor-errors', undefined, LONG)
    expect(screen.getByRole('button', { name: /Gravar/ })).toBeDisabled()
    expect(bad.count('PUT /v1/pipelines/meu-fluxo')).toBe(0)
  })

  it('saves with the hash of the file as If-Match and hands the saved pipeline back', async () => {
    const user = userEvent.setup()
    const { calls, onSaved } = setup({ original: true })
    const button = await screen.findByRole('button', { name: 'Gravar alterações' }, LONG)
    await waitFor(() => expect(button).toBeEnabled(), LONG)
    await user.click(button)
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    const put = calls.find((call) => call.key === 'PUT /v1/pipelines/meu-fluxo')
    expect(put?.body).toMatchObject({ scope: 'user', conversation_id: 'conv-1', if_match: 'c'.repeat(64) })
  })

  it('a new file is saved without If-Match', async () => {
    const user = userEvent.setup()
    const { calls } = setup({ original: false })
    const button = await screen.findByRole('button', { name: 'Gravar como novo' }, LONG)
    await waitFor(() => expect(button).toBeEnabled(), LONG)
    await user.click(button)
    await waitFor(() => expect(calls.some((call) => call.key === 'PUT /v1/pipelines/meu-fluxo')).toBe(true))
    expect(calls.find((call) => call.key === 'PUT /v1/pipelines/meu-fluxo')?.body).not.toHaveProperty('if_match')
  })

  it('on an If-Match conflict it warns, does not overwrite, and offers to reload the file', async () => {
    const user = userEvent.setup()
    const { onSaved, onReload } = setup({ original: true, save: () => apiError(409, 'PIPELINE_CONFLICT', 'mudou', { current_hash: 'd'.repeat(64) }) })
    const button = await screen.findByRole('button', { name: 'Gravar alterações' }, LONG)
    await waitFor(() => expect(button).toBeEnabled(), LONG)
    await user.click(button)
    const alert = await screen.findByTestId('editor-save-error')
    expect(alert).toHaveTextContent(/O arquivo mudou/)
    expect(onSaved).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Recarregar o arquivo' }))
    expect(onReload).toHaveBeenCalledTimes(1)
  })

  it('keeps line numbers in step with the text', async () => {
    setup({ errors: [] })
    const area = screen.getByLabelText('Texto YAML do pipeline')
    fireEvent.change(area, { target: { value: 'a\nb\nc\nd' } })
    const gutter = document.querySelector('.pipeline-editor__gutter')
    expect(gutter?.children).toHaveLength(4)
  })
})

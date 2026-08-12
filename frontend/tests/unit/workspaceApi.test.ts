import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { attachWorkspaceFolder, detachWorkspaceFolder, inspectWorkspaceFolder } from '../../src/api/workspace'

function clientWith(response: unknown, status = 200) {
  const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify(response), { status, headers: { 'Content-Type': 'application/json' } }))
  return { client: new ApiClient({ fetchImpl }), fetchImpl }
}

describe('workspace api', () => {
  it('reads an inspection', async () => {
    const { client } = clientWith({ path: 'D:/site', exists: true, is_directory: true, writable: true, entry_count: 3, entries_truncated: false, risk: 'none' })

    const result = await inspectWorkspaceFolder(client, 'chat_a', null)

    expect(result).toEqual({ kind: 'folder', path: 'D:/site', exists: true, isDirectory: true, writable: true, entryCount: 3, entriesTruncated: false, risk: 'none' })
  })

  it('reads a cancelled dialog and an unavailable dialog', async () => {
    const cancelled = clientWith({ cancelled: true })
    expect(await inspectWorkspaceFolder(cancelled.client, 'chat_a', null)).toEqual({ kind: 'cancelled' })

    const unavailable = clientWith({ dialog_unavailable: true })
    expect(await inspectWorkspaceFolder(unavailable.client, 'chat_a', null)).toEqual({ kind: 'unavailable' })
  })

  it('sends the acknowledgement when attaching', async () => {
    const { client, fetchImpl } = clientWith({ kind: 'local', path: 'C:/', folder_name: 'C:', scope: 'chat', project_name: null })

    const state = await attachWorkspaceFolder(client, 'chat_a', 'C:/', true)

    expect(state).toEqual({ kind: 'local', path: 'C:/', folderName: 'C:', scope: 'chat', projectName: null })
    const init = fetchImpl.mock.calls[0][1]
    expect(JSON.parse(String(init?.body))).toEqual({ path: 'C:/', acknowledged_risk: true })
  })

  it('reads the state returned by detach', async () => {
    const { client } = clientWith({ kind: 'managed', path: null, folder_name: null, scope: 'chat', project_name: null })

    expect(await detachWorkspaceFolder(client, 'chat_a')).toEqual({ kind: 'managed', path: null, folderName: null, scope: 'chat', projectName: null })
  })
})

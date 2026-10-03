import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type FileEntry = { name: string; kind: 'directory' | 'file'; bytes: number; modifiedAt: string }
export type FileListing = { path: string; entries: FileEntry[]; truncated: boolean }

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidResponseError()
  return value as Record<string, unknown>
}

function parseListing(value: unknown): FileListing {
  const data = record(value)
  if (typeof data.path !== 'string' || !Array.isArray(data.entries)) throw invalidResponseError()
  return {
    path: data.path,
    truncated: data.truncated === true,
    entries: data.entries.map((item) => {
      const entry = record(item)
      if (typeof entry.name !== 'string' || (entry.kind !== 'directory' && entry.kind !== 'file')) throw invalidResponseError()
      return { name: entry.name, kind: entry.kind, bytes: typeof entry.bytes === 'number' ? entry.bytes : 0, modifiedAt: String(entry.modified_at ?? '') }
    }),
  }
}

function parsePath(value: unknown): string {
  const data = record(value)
  if (typeof data.path !== 'string') throw invalidResponseError()
  return data.path
}

export function listFiles(client: ApiClient, path: string, signal?: AbortSignal): Promise<FileListing> {
  return client.request({ path: '/v1/files', query: { path }, signal, parse: parseListing })
}

export function createFolder(client: ApiClient, parent: string, name: string): Promise<string> {
  return client.request({ path: '/v1/files/folders', method: 'POST', body: { parent, name }, expectedStatus: 201, parse: parsePath })
}

export function importArchive(client: ApiClient, file: File, folderName: string, parent = ''): Promise<string> {
  const body = new FormData()
  body.set('file', file)
  body.set('folder_name', folderName)
  body.set('parent', parent)
  return client.upload({ path: '/v1/files/import', body, expectedStatus: 201, parse: parsePath })
}

export function fileDownloadUrl(path: string): string {
  return `/v1/files/download?${new URLSearchParams({ path }).toString()}`
}

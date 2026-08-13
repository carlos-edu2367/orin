import type { ApiClient } from './client'
import { invalidResponseError } from './errors'

export type UploadedFile = {
  upload_id: string
  filename: string
  media_type: string
  kind: 'text' | 'image' | 'pdf' | 'office'
  bytes: number
}

export function uploadFile(client: ApiClient, file: File): Promise<UploadedFile> {
  const body = new FormData()
  body.append('file', file, file.name)
  return client.upload({ path: '/v1/uploads', body, expectedStatus: 201, parse: parseUploadedFile })
}

export function deleteUpload(client: ApiClient, uploadId: string): Promise<void> {
  return client.request({ path: `/v1/uploads/${encodeURIComponent(uploadId)}`, method: 'DELETE', expectedStatus: 204, parse: () => undefined })
}

function parseUploadedFile(value: unknown): UploadedFile {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw invalidResponseError()
  const data = value as Record<string, unknown>
  const kind = String(data.kind)
  if (kind !== 'text' && kind !== 'image' && kind !== 'pdf' && kind !== 'office') throw invalidResponseError()
  return {
    upload_id: String(data.upload_id), filename: String(data.filename),
    media_type: String(data.media_type), kind, bytes: Number(data.bytes),
  }
}

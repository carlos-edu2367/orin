import { describe, expect, it, vi } from 'vitest'
import { ApiClient } from '../../src/api/client'
import { deleteUpload, uploadFile } from '../../src/api/uploads'

function clientWith(response: Response) {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(response)
  const client = new ApiClient({ baseUrl: 'http://localhost', fetchImpl: fetchMock })
  return { client, fetchMock }
}

describe('uploads api', () => {
  it('posts multipart form data without a JSON content type', async () => {
    const body = { upload_id: 'upl_1', filename: 'foto.png', media_type: 'image/png', kind: 'image', bytes: 40 }
    const { client, fetchMock } = clientWith(new Response(JSON.stringify(body), { status: 201 }))
    const result = await uploadFile(client, new File([new Uint8Array([1, 2, 3])], 'foto.png', { type: 'image/png' }))
    expect(result).toEqual(body)
    const request = fetchMock.mock.calls[0][1]
    expect(request?.body).toBeInstanceOf(FormData)
    expect(new Headers(request?.headers).get('Content-Type')).toBeNull()
  })

  it('deletes an upload', async () => {
    const { client, fetchMock } = clientWith(new Response(null, { status: 204 }))
    await deleteUpload(client, 'upl_1')
    expect(fetchMock.mock.calls[0][0]).toContain('/v1/uploads/upl_1')
  })
})

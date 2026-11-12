import { describe, expect, it } from 'vitest'
import type { ApiClient } from '../client'
import { uploadFile } from '../uploads'

function clientReplying(payload: unknown): ApiClient {
  return {
    upload: async ({ parse }: { parse: (value: unknown) => unknown }) => parse(payload),
  } as unknown as ApiClient
}

describe('uploadFile', () => {
  it.each(['audio', 'video', 'image', 'pdf', 'office', 'text'] as const)('accepts a %s upload', async (kind) => {
    const file = new File([new Uint8Array([1, 2, 3])], `arquivo.${kind}`)
    const uploaded = await uploadFile(
      clientReplying({ upload_id: 'upl_1', filename: file.name, media_type: 'x/y', kind, bytes: 3 }),
      file,
    )
    expect(uploaded.kind).toBe(kind)
    expect(uploaded.bytes).toBe(3)
  })

  it('still rejects a kind it does not know', async () => {
    const file = new File([new Uint8Array([1])], 'a.bin')
    await expect(
      uploadFile(clientReplying({ upload_id: 'upl_1', filename: 'a.bin', media_type: 'x/y', kind: 'archive', bytes: 1 }), file),
    ).rejects.toThrow()
  })
})

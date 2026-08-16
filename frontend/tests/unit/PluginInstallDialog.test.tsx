import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PluginInstallDialog } from '../../src/features/plugins/PluginInstallDialog'
import { ApiClient } from '../../src/api/client'

function errorResponse(code: string, status = 409): Response {
  return new Response(JSON.stringify({ error: { code, category: 'CONFLICT', message_key: code, correlation_id: 'c1', retryable: false, retry_after: null } }), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('PluginInstallDialog', () => {
  it('calls onNoManifest and does not show the generic error when inspection fails with plugin_no_manifest', async () => {
    const onNoManifest = vi.fn()
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(errorResponse('plugin_no_manifest'))
    render(<PluginInstallDialog client={new ApiClient({ fetchImpl, maxAttempts: 1 })} initialReference="https://github.com/acme/no-manifest.git" onClose={vi.fn()} onInstalled={vi.fn()} onNoManifest={onNoManifest} />)

    await waitFor(() => expect(onNoManifest).toHaveBeenCalledOnce())
    expect(screen.queryByText('Não foi possível inspecionar este plugin.')).not.toBeInTheDocument()
  })

  it('shows the generic error for any other failure code, even with onNoManifest provided', async () => {
    const onNoManifest = vi.fn()
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(errorResponse('plugin_operation_rejected'))
    render(<PluginInstallDialog client={new ApiClient({ fetchImpl, maxAttempts: 1 })} initialReference="https://github.com/acme/broken.git" onClose={vi.fn()} onInstalled={vi.fn()} onNoManifest={onNoManifest} />)

    expect(await screen.findByText('Não foi possível inspecionar este plugin.')).toBeInTheDocument()
    expect(onNoManifest).not.toHaveBeenCalled()
  })

  it('shows the generic error as before when onNoManifest is not provided', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(errorResponse('plugin_no_manifest'))
    render(<PluginInstallDialog client={new ApiClient({ fetchImpl, maxAttempts: 1 })} initialReference="https://github.com/acme/no-manifest.git" onClose={vi.fn()} onInstalled={vi.fn()} />)

    expect(await screen.findByText('Não foi possível inspecionar este plugin.')).toBeInTheDocument()
  })
})

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { ApiClient } from '../../src/api/client'
import { RuntimeSettingsPage } from '../../src/features/settings/RuntimeSettingsPage'

function client(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ fetchImpl, createIdempotencyKey: () => 'runtime-settings-test' })
}

describe('RuntimeSettingsPage', () => {
  it('lets the user switch an unlimited iteration budget to a numeric limit', async () => {
    const fetchImpl = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify({ max_iterations: null }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ autonomy: 'approval_required', system_notifications: false, monitoring_enabled: true }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ installation_kind: 'development', current_version: '0.1.12', installed_versions: [], latest_release: null, latest_release_error: 'unavailable', checked_at: '2026-08-14T00:00:00Z' }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ max_iterations: 48 }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    render(<MemoryRouter><RuntimeSettingsPage client={client(fetchImpl)} /></MemoryRouter>)

    const unlimited = await screen.findByRole('checkbox', { name: 'Sem limite de interações' })
    expect(unlimited).toBeChecked()
    fireEvent.click(unlimited)
    fireEvent.change(screen.getByLabelText('Máximo de interações por turno'), { target: { value: '48' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar limite' }))

    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(4))
    expect(String(fetchImpl.mock.calls[3][1]?.body)).toBe('{"max_iterations":48}')
    expect(screen.getByText('Limite salvo.')).toBeInTheDocument()
  })
})

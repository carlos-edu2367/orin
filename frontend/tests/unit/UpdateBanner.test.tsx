import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { UpdateBanner } from '../../src/components/UpdateBanner'

describe('UpdateBanner', () => {
  let listener: ((update: { currentVersion: string; latestVersion: string }) => void) | undefined
  const runUpdate = vi.fn<() => Promise<boolean>>()

  beforeEach(() => {
    listener = undefined
    runUpdate.mockReset()
    runUpdate.mockResolvedValue(true)
    window.orinDesktop = {
      onUpdateAvailable: (callback) => {
        listener = callback
        return () => { listener = undefined }
      },
      runUpdate,
    }
  })

  afterEach(() => {
    delete window.orinDesktop
  })

  it('shows both versions and runs the packaged update command', async () => {
    render(<UpdateBanner />)
    listener?.({ currentVersion: '0.1.10', latestVersion: '0.1.11' })

    expect(await screen.findByText('Versão atual 0.1.10 - Versão mais recente 0.1.11')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Atualizar' }))

    await waitFor(() => expect(runUpdate).toHaveBeenCalledOnce())
  })
})

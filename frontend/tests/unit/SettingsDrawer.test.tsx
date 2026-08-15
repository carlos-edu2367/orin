import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { SettingsDrawer } from '../../src/features/settings/SettingsDrawer'

describe('SettingsDrawer', () => {
  it('is a labelled region, moves focus inside and closes on Escape or button', async () => {
    const onClose = vi.fn()
    render(<SettingsDrawer title="OpenAI" onClose={onClose}><p>corpo</p></SettingsDrawer>)
    const region = screen.getByRole('region', { name: 'OpenAI' })
    expect(region).toHaveFocus()
    expect(region).not.toHaveAttribute('aria-modal', 'true')
    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledOnce()
    await userEvent.click(screen.getByRole('button', { name: /Fechar/ }))
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('restores focus to the opener on unmount', () => {
    const opener = document.createElement('button')
    document.body.append(opener)
    opener.focus()
    const { unmount } = render(<SettingsDrawer title="OpenAI" onClose={() => {}}><p>corpo</p></SettingsDrawer>)
    unmount()
    expect(opener).toHaveFocus()
    opener.remove()
  })
})

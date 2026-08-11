import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { SettingsPage } from '../../src/features/settings/SettingsPage'

describe('SettingsPage', () => {
  it('keeps global management in one quiet navigation area', () => {
    render(<MemoryRouter><SettingsPage /></MemoryRouter>)

    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'General' })).toHaveAttribute('href', '/settings/general')
    expect(screen.getByRole('link', { name: 'Memory' })).toHaveAttribute('href', '/settings/memory')
    expect(screen.getByRole('link', { name: 'OmniRoute' })).toHaveAttribute('href', '/settings/omniroute')
  })
})

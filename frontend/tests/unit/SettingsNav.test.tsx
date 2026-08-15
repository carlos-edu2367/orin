import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { SettingsNav } from '../../src/features/settings/SettingsNav'

function renderNav(pathname: string, badges = {}) {
  return render(<MemoryRouter initialEntries={[pathname]}><SettingsNav badges={badges} /></MemoryRouter>)
}

describe('SettingsNav', () => {
  it('renders groups and marks the current section', () => {
    renderNav('/settings/providers/openai')
    expect(screen.getByRole('navigation', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByText('Sessão')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Providers/ })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: /Skills/ })).not.toHaveAttribute('aria-current')
  })

  it('renders known values and a labelled pending marker', () => {
    renderNav('/settings/general', { skills: { value: '18' }, mcp: { value: '2', pending: true } })
    expect(within(screen.getByRole('link', { name: /Skills/ })).getByText('18')).toBeInTheDocument()
    expect(within(screen.getByRole('link', { name: /MCP/ })).getByLabelText('Aguardando sua ação')).toBeInTheDocument()
  })

  it('does not render an unknown badge as zero', () => {
    renderNav('/settings/general')
    expect(within(screen.getByRole('link', { name: /Skills/ })).queryByTestId('settings-nav-badge')).toBeNull()
  })
})

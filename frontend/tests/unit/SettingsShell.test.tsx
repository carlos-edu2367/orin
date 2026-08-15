import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { SettingsShell } from '../../src/features/settings/SettingsShell'
import { SettingsSection } from '../../src/features/settings/SettingsSection'

describe('SettingsShell', () => {
  it('renders one main landmark, navigation, content and chat link', () => {
    render(<MemoryRouter initialEntries={['/settings/general']}><SettingsShell badges={{}}><p>conteúdo</p></SettingsShell></MemoryRouter>)
    expect(screen.getByRole('main')).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Settings' })).toBeInTheDocument()
    expect(screen.getByText('conteúdo')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Voltar ao chat/ })).toHaveAttribute('href', '/')
  })

  it('renders the drawer slot only when supplied', () => {
    const { rerender } = render(<MemoryRouter initialEntries={['/settings/general']}><SettingsShell badges={{}}><p>conteúdo</p></SettingsShell></MemoryRouter>)
    expect(screen.queryByTestId('settings-drawer-slot')).toBeNull()
    rerender(<MemoryRouter initialEntries={['/settings/providers/openai']}><SettingsShell badges={{}} drawer={<p>detalhe</p>}><p>conteúdo</p></SettingsShell></MemoryRouter>)
    expect(screen.getByText('detalhe')).toBeInTheDocument()
  })
})

describe('SettingsSection', () => {
  it('uses the declarative title and lede and allows overrides', () => {
    render(<MemoryRouter initialEntries={['/settings/providers']}><SettingsSection eyebrow="PROVIDERS / CONEXÕES"><p>corpo</p></SettingsSection></MemoryRouter>)
    expect(screen.getByRole('heading', { level: 1, name: 'Providers' })).toBeInTheDocument()
    expect(screen.getByText(/Configure ou revogue/)).toBeInTheDocument()
    render(<MemoryRouter initialEntries={['/settings/providers']}><SettingsSection eyebrow="X" title="Outro" lede="Outra descrição."><p>corpo</p></SettingsSection></MemoryRouter>)
    expect(screen.getByRole('heading', { level: 1, name: 'Outro' })).toBeInTheDocument()
  })
})

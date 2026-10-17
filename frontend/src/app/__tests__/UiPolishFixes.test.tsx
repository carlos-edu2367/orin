import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SchedulesPage } from '../../features/schedules/SchedulesPage'
import stylesheet from '../../styles/agentos.css?raw'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('UI polish fixes (schedules padding, home composer shadow, settings internal scroll)', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('wraps embedded SchedulesPage in a padded hub-page container so the heading is never flush against canvas border', async () => {
    vi.stubGlobal('fetch', vi.fn<typeof fetch>().mockImplementation(async () => json({ items: [] })))

    render(
      <MemoryRouter initialEntries={['/schedules']}>
        <SchedulesPage embedded />
      </MemoryRouter>,
    )

    const wrapper = screen.getByTestId('schedules-hub-page')
    expect(wrapper).toHaveClass('hub-page', 'hub-page--schedules')
    expect(screen.getByRole('heading', { level: 1, name: 'Agendamentos' })).toBeInTheDocument()
    expect(stylesheet).toMatch(/\.hub-page--schedules\s*\{[^}]*max-width:\s*1120px/)
  })

  it('keeps .home__stage full-width so overflow-y: auto never clips the left/right box-shadow of .composer__surface', () => {
    expect(stylesheet).toMatch(/\.home__stage\s*\{[^}]*width:\s*100%/)
    expect(stylesheet).toMatch(/\.home__composer\s*\{[^}]*width:\s*min\(100%,\s*680px\)/)
    expect(stylesheet).toMatch(/\[data-theme="light"\]\s*\.composer__surface\s*\{/)
  })

  it('bounds .settings-shell, .workspace-shell, and .skills-list vertically with internal overflow-y: auto scrollbars', () => {
    expect(stylesheet).toMatch(/\.settings-shell\s*\{[^}]*height:\s*100dvh[^}]*max-height:\s*100dvh[^}]*overflow:\s*hidden/)
    expect(stylesheet).toMatch(/\.settings-content\s*\{[^}]*overflow-y:\s*auto/)
    expect(stylesheet).toMatch(/\.settings-nav\s*\{[^}]*overflow-y:\s*auto/)
    expect(stylesheet).toMatch(/\.workspace-shell\s*\{[^}]*grid-template-rows:\s*minmax\(0,\s*1fr\)/)
    expect(stylesheet).toMatch(/\.skills-list\s*\{[^}]*max-height:\s*360px[^}]*overflow-y:\s*auto/)
  })
})

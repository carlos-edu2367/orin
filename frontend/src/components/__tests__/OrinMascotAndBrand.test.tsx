import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { Brand } from '../Brand'
import { OrinMascot } from '../OrinMascot'
import agentosCss from '../../styles/agentos.css?raw'

describe('OrinMascot & Brand components', () => {
  it('renders OrinMascot flat variant with canonical SVG silhouette, eyes, smile, and theme-synced fill', () => {
    const { container } = render(<OrinMascot size={28} variant="flat" />)
    const mascot = screen.getByRole('img', { name: /Mascote Orin/i })
    expect(mascot).toBeInTheDocument()
    expect(mascot).toHaveAttribute('data-variant', 'flat')

    const bodyPath = container.querySelector('[data-mascot-part="body"]')
    expect(bodyPath).not.toBeNull()
    expect(bodyPath?.getAttribute('d')).toContain('M 55 46')
    expect(bodyPath?.getAttribute('fill')).toBe('var(--orin-accent, #B4A4EF)')

    const eyes = container.querySelectorAll('[data-mascot-part="eye"]')
    expect(eyes).toHaveLength(2)
    expect(container.querySelector('[data-mascot-part="smile"]')).not.toBeNull()
  })

  it('renders soft3d variant with radial gradient shading and base shadow', () => {
    const { container } = render(<OrinMascot size={88} variant="soft3d" ariaLabel="Orin 3D" />)
    expect(screen.getByRole('img', { name: 'Orin 3D' })).toHaveAttribute('data-variant', 'soft3d')
    expect(container.querySelector('radialGradient')).not.toBeNull()
    expect(container.querySelector('[data-mascot-part="shadow"]')).not.toBeNull()
  })

  it('renders waving variant with waving arm and sparkles for onboarding', () => {
    const { container } = render(<OrinMascot size={112} variant="waving" />)
    expect(screen.getByRole('img', { name: /Mascote Orin/i })).toHaveAttribute('data-variant', 'waving')
    expect(container.querySelector('[data-mascot-part="waving-arm"]')).not.toBeNull()
    expect(container.querySelectorAll('[data-mascot-part="sparkle"]').length).toBeGreaterThanOrEqual(3)
  })

  it('renders statusDot="online" and statusDot="busy" and falls back safely on invalid size', () => {
    const { rerender } = render(<OrinMascot size={-20} statusDot="online" />)
    const wrapper = screen.getByTestId('orin-mascot')
    expect(wrapper).toHaveAttribute('data-status-dot', 'online')
    expect(screen.getByTestId('orin-mascot-status-dot')).toHaveClass('orin-mascot__status-dot--online')

    rerender(<OrinMascot size="NaN" statusDot="busy" color="#7AA2F7" />)
    expect(screen.getByTestId('orin-mascot')).toHaveAttribute('data-status-dot', 'busy')
    expect(screen.getByTestId('orin-mascot-status-dot')).toHaveClass('orin-mascot__status-dot--busy')
  })

  it('renders Brand with OrinMascot mark and lowercase "orin" wordmark while preserving accessible label', () => {
    const { container } = render(
      <MemoryRouter>
        <Brand to="/" />
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: 'Orin, início' })
    expect(link).toHaveAttribute('href', '/')
    expect(container.querySelector('.brand__word')).toHaveTextContent('orin')
    expect(container.querySelector('[data-mascot-part="body"]')).not.toBeNull()
  })

  it('applies animated class with prefers-reduced-motion CSS guard and derives soft3d gradient stops from custom color prop', () => {
    const { container } = render(<OrinMascot size={64} variant="soft3d" animated color="#4ADE80" />)
    const wrapper = screen.getByTestId('orin-mascot')
    expect(wrapper).toHaveClass('is-animated')
    expect(agentosCss).toMatch(/\.orin-mascot\.is-animated/)
    expect(agentosCss).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*?\.orin-mascot\.is-animated/)

    const stops = container.querySelectorAll('radialGradient stop')
    expect(stops).toHaveLength(3)
    expect(stops[0]?.getAttribute('stop-color')).toContain('#4ADE80')
    expect(stops[1]?.getAttribute('stop-color')).toBe('#4ADE80')
    expect(stops[2]?.getAttribute('stop-color')).toContain('#4ADE80')
  })
})

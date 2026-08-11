import { MemoryRouter } from 'react-router-dom'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Brand } from '../../src/components/Brand'
import { BRAND_LOGO_PATH } from '../../src/config/brand'

describe('Brand', () => {
  it('renders Orin with the supplied logo and preserves router navigation', () => {
    const { container } = render(<MemoryRouter><Brand to="/" /></MemoryRouter>)

    expect(screen.getByRole('link', { name: 'Orin, início' })).toHaveAttribute('href', '/')
    expect(screen.getByText('Orin')).toBeInTheDocument()
    expect(BRAND_LOGO_PATH).not.toBe('/orin-logo.png')
    expect(BRAND_LOGO_PATH).toMatch(/orin-logo\.png/)
    expect(container.querySelector('.brand__mark img')).toHaveAttribute('src', BRAND_LOGO_PATH)
  })
})

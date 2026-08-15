import { describe, expect, it } from 'vitest'
import { PROVIDER_NAMES } from '../../src/api/providers'
import { providerBrand } from '../../src/features/providers/providerBrand'

describe('providerBrand', () => {
  it('gives every known provider a local mark and distinct accent', () => {
    for (const provider of PROVIDER_NAMES) {
      const brand = providerBrand(provider)
      expect(brand.label.length).toBeGreaterThan(0)
      expect(brand.accent).toMatch(/^#[0-9a-f]{6}$/i)
      expect(brand.mark).toContain('viewBox="0 0 24 24"')
      expect(brand.mark).not.toMatch(/https?:\/\//)
    }
    expect(new Set(PROVIDER_NAMES.map((provider) => providerBrand(provider).accent)).size).toBe(PROVIDER_NAMES.length)
  })

  it('falls back to a neutral local mark', () => {
    const brand = providerBrand('unknown' as never)
    expect(brand.label).toBe('unknown')
    expect(brand.mark).toContain('viewBox="0 0 24 24"')
  })
})

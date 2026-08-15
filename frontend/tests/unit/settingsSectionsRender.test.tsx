import { describe, expect, it } from 'vitest'
import { routes } from '../../src/app/routes'
import { SETTINGS_GROUPS, settingsItems } from '../../src/features/settings/sections'

describe('settings route composition', () => {
  it('has a route for every declarative section and no legacy section', () => {
    const paths = new Set(routes.map((route) => route.path))
    for (const item of settingsItems()) expect(paths).toContain(item.path)
    expect(SETTINGS_GROUPS.flatMap((group) => group.items).map((item) => item.id)).not.toContain('advanced')
    expect(paths).toContain('/settings/providers/:provider')
    expect(paths).toContain('/settings/skills/:skillId')
  })

  it('keeps compatibility aliases and redirects in the route table', () => {
    const paths = new Set(routes.map((route) => route.path))
    expect([...paths]).toEqual(expect.arrayContaining(['/settings/omniroute', '/settings/agents', '/providers', '/skills', '/schedules']))
  })
})

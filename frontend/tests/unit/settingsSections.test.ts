import { describe, expect, it } from 'vitest'
import { SETTINGS_GROUPS, findSettingsItem, settingsItems } from '../../src/features/settings/sections'

describe('settings sections', () => {
  it('groups every item under a titled group', () => {
    for (const group of SETTINGS_GROUPS) {
      expect(group.title.length).toBeGreaterThan(0)
      expect(group.items.length).toBeGreaterThan(0)
    }
  })

  it('gives every item a unique id, path and label', () => {
    const items = settingsItems()
    expect(new Set(items.map((item) => item.id)).size).toBe(items.length)
    expect(new Set(items.map((item) => item.path)).size).toBe(items.length)
    for (const item of items) {
      expect(item.label.length).toBeGreaterThan(0)
      expect(item.path.startsWith('/settings/')).toBe(true)
    }
  })

  it('resolves exact, nested and outside paths', () => {
    expect(findSettingsItem('/settings/providers')?.id).toBe('providers')
    expect(findSettingsItem('/settings/providers/openai')?.id).toBe('providers')
    expect(findSettingsItem('/chats/abc')).toBeUndefined()
  })

  it('declares status badges only where they are needed', () => {
    expect(findSettingsItem('/settings/mcp')?.badge).toBe('mcp')
    expect(findSettingsItem('/settings/general')?.badge).toBeUndefined()
  })
})

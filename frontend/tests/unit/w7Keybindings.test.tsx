import { act, render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { defaultKeymap, keysFor, resolveKeymap, validateKeybindings } from '../../src/features/keybindings/bindings'
import { createMatcher, KeybindingsProvider, PENDING, useKeybindingHandlers } from '../../src/features/keybindings/engine'
import { displayKeys, normalizeStroke, parseKeys, strokeFromEvent } from '../../src/features/keybindings/keys'

const key = (key: string, extra: Partial<KeyboardEvent> & { code?: string } = {}) =>
  ({ key, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...extra })

describe('key syntax', () => {
  it('normalizes modifiers, aliases and chords', () => {
    expect(normalizeStroke('Shift+Ctrl+K')).toBe('ctrl+shift+k')
    expect(normalizeStroke('meta+p')).toBe('alt+p')
    expect(normalizeStroke('opt+P')).toBe('alt+p')
    expect(normalizeStroke('win+e')).toBe('cmd+e')
    expect(normalizeStroke('esc')).toBe('escape')
    expect(normalizeStroke('ctrl++')).toBe('ctrl++')
    expect(normalizeStroke('ctrl+hyper+k')).toBeNull()
    expect(normalizeStroke('ctrl+ctrl+k')).toBeNull()
    expect(parseKeys('ctrl+k ctrl+s')).toEqual({ ok: true, keys: ['ctrl+k', 'ctrl+s'] })
    expect(parseKeys('a b c d').ok).toBe(false)
  })

  it('reads keyboard events like the file names keys', () => {
    expect(strokeFromEvent(key('Enter', { shiftKey: true }))).toBe('shift+enter')
    expect(strokeFromEvent(key('K', { ctrlKey: true, shiftKey: true }))).toBe('ctrl+shift+k')
    expect(strokeFromEvent(key('?', { shiftKey: true }))).toBe('?')
    expect(strokeFromEvent(key('π', { altKey: true, code: 'KeyP' }))).toBe('alt+p')
    expect(strokeFromEvent(key('Shift', { shiftKey: true }))).toBeNull()
    expect(displayKeys(['ctrl+k', 'ctrl+s'])).toBe('Ctrl+K Ctrl+S')
  })
})

describe('keybindings.json', () => {
  it('accepts both shapes, unbinds with null and resolves later files over earlier ones', () => {
    const claude = validateKeybindings(JSON.stringify([{ context: 'Chat', bindings: { 'ctrl+k ctrl+s': 'chat:submit' } }]), 'browser')
    const orin = validateKeybindings(JSON.stringify({ $schema: 'x', bindings: [{ context: 'Chat', bindings: { enter: null, 'ctrl+k ctrl+s': 'chat:newline' } }] }), 'browser')
    expect(claude.errors).toEqual([])
    expect(orin.errors).toEqual([])
    const map = resolveKeymap('browser', [claude.bindings, orin.bindings])
    expect(map.get('Chat')!.get('enter')).toBeUndefined()
    expect(map.get('Chat')!.get('ctrl+k ctrl+s')).toBe('chat:newline')
    expect(keysFor(map, 'chat:submit')).toEqual([])
  })

  it('reports errors and warnings without throwing', () => {
    const result = validateKeybindings(JSON.stringify([
      { context: 'Nowhere', bindings: {} },
      { context: 'Chat', bindings: { 'ctrl+c': 'chat:submit', 'ctrl+t': 'chat:submit', 'alt+x': 'chat:fastMode', 'alt+y': 'chat:explode', 'hyper+z': 'chat:submit', 'alt+q': 3 } },
    ]), 'browser')
    expect(result.errors.some((item) => item.includes('Nowhere'))).toBe(true)
    expect(result.errors.some((item) => item.includes('reservada'))).toBe(true)
    expect(result.errors.some((item) => item.includes('chat:explode'))).toBe(true)
    expect(result.errors.some((item) => item.includes('hyper+z'))).toBe(true)
    expect(result.warnings.some((item) => item.includes('chat:fastMode'))).toBe(true)
    expect(result.warnings.some((item) => item.includes('navegador'))).toBe(true)
    expect(result.bindings.map((item) => item.keys.join(' '))).toEqual(['ctrl+t'])
    expect(validateKeybindings('{oops', 'browser').errors).toHaveLength(1)
    expect(validateKeybindings('{"bindings": 3}', 'browser').errors).toHaveLength(1)
  })

  it('has different defaults in a browser and in the desktop app', () => {
    expect(defaultKeymap('electron').get('Global')!.get('ctrl+t')).toBe('app:toggleTodos')
    expect(defaultKeymap('browser').get('Global')!.get('ctrl+t')).toBeUndefined()
    expect(defaultKeymap('browser').get('Global')!.get('ctrl+o')).toBe('app:toggleTranscript')
  })
})

describe('matcher', () => {
  it('waits inside a chord and gives up after the timeout', () => {
    let now = 0
    const map = resolveKeymap('browser', [[{ context: 'Chat', keys: ['ctrl+k', 'ctrl+s'], action: 'chat:submit' }]])
    const match = createMatcher(map, () => now)
    expect(match(key('k', { ctrlKey: true }), ['Chat'])).toBe(PENDING)
    expect(match(key('s', { ctrlKey: true }), ['Chat'])).toBe('chat:submit')
    expect(match(key('k', { ctrlKey: true }), ['Chat'])).toBe(PENDING)
    now = 3500
    expect(match(key('s', { ctrlKey: true }), ['Chat'])).toBeNull()
    expect(match(key('Escape'), ['Autocomplete', 'Chat'])).toBe('autocomplete:dismiss')
  })
})

describe('provider', () => {
  it('sends an action to the newest surface that handles it', () => {
    const calls: string[] = []
    function Surface({ name }: { name: string }) {
      useKeybindingHandlers('Global', { 'app:toggleTranscript': () => { calls.push(name) } })
      return null
    }
    function Dialog() {
      useKeybindingHandlers('Confirmation', { 'confirm:no': () => { calls.push('dialog') } })
      return null
    }
    render(<KeybindingsProvider environment="browser"><Surface name="chat" /><Dialog /></KeybindingsProvider>)
    act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'o', ctrlKey: true })) })
    act(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })) })
    expect(calls).toEqual(['chat', 'dialog'])
  })
})

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createBrowserApiClient } from '../../../api/client'
import { WorkspaceNavigation } from '../../projects/WorkspaceNavigation'
import { PinnedSessionTabs } from '../PinnedSessionTabs'
import {
  PINNED_STORAGE_KEY, SESSION_DRAG_TYPE, flushDeleteOnUnload, getSessionStore, pinSession, resetSessionStore, syncSessions, unpinSession,
} from '../sessionStore'

type Call = { method: string; url: string; body: unknown }

const conversations = [
  { conversation_id: 'c1', title: 'Helpdesk sem assunto', state: 'idle', mode: 'code', permission_mode: 'ask_permissions', updated_at: null },
  { conversation_id: 'c2', title: 'Bot de cobrança', state: 'running', mode: 'chat', permission_mode: 'ask_permissions', updated_at: null },
  { conversation_id: 'friend1', title: 'Orin Friend', state: 'idle', mode: 'friend', permission_mode: 'ask_permissions', updated_at: null },
]

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}

function mockApi(options: { deleteStatus?: number } = {}) {
  const calls: Call[] = []
  let items = [...conversations]
  globalThis.fetch = vi.fn<typeof fetch>().mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : String(input)
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, url, body })
    if (url.endsWith('/v1/projects/sidebar')) return json({ items: [] })
    if (url.includes('/v1/conversations') && method === 'GET' && !url.match(/conversations\/[^/?]+/)) return json({ items })
    const rename = url.match(/\/v1\/conversations\/([^/]+)\/title$/)
    if (rename && method === 'PATCH') {
      items = items.map((item) => (item.conversation_id === rename[1] ? { ...item, title: (body as { title: string }).title } : item))
      return json({ conversation_id: rename[1], title: (body as { title: string }).title })
    }
    const del = url.match(/\/v1\/conversations\/([^/]+)$/)
    if (del && method === 'DELETE') {
      const status = options.deleteStatus ?? 204
      if (status !== 204) return json({ error: { code: 'conflict', message: 'running' } }, status)
      items = items.filter((item) => item.conversation_id !== del[1])
      return new Response(null, { status: 204 })
    }
    return json({})
  })
  return calls
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderApp(path = '/') {
  const client = createBrowserApiClient()
  const page = (
    <>
      <header data-testid="header"><PinnedSessionTabs client={client} /></header>
      <WorkspaceNavigation client={client} initialView="chats" />
      <LocationProbe />
    </>
  )
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/" element={page} />
        <Route path="/chats/:id" element={page} />
      </Routes>
    </MemoryRouter>,
  )
}

function row(id: string): HTMLElement {
  const element = document.querySelector<HTMLElement>(`[data-session-row="${id}"]`)
  if (!element) throw new Error(`row ${id} not rendered`)
  return element
}

function fakeDataTransfer() {
  const data: Record<string, string> = {}
  return {
    data,
    get types() { return Object.keys(data) },
    setData: (type: string, value: string) => { data[type] = value },
    getData: (type: string) => data[type] ?? '',
    dropEffect: 'move',
    effectAllowed: 'all',
  }
}

describe('session store', () => {
  beforeEach(() => {
    localStorage.clear()
    resetSessionStore()
  })

  it('pins, reorders, unpins and persists to localStorage', () => {
    pinSession({ id: 'a', title: 'A' })
    pinSession({ id: 'b', title: 'B', mode: 'code', projectId: 'p1' })
    pinSession({ id: 'b', title: 'B', mode: 'code', projectId: 'p1' }, 0)
    expect(getSessionStore().pinned.map((item) => item.id)).toEqual(['b', 'a'])
    expect(JSON.parse(localStorage.getItem(PINNED_STORAGE_KEY) ?? '[]')).toEqual([
      { id: 'b', title: 'B', mode: 'code', projectId: 'p1' },
      { id: 'a', title: 'A', mode: 'chat', projectId: null },
    ])
    unpinSession('b')
    expect(getSessionStore().pinned.map((item) => item.id)).toEqual(['a'])
  })

  it('reads only valid entries back after a reload', () => {
    localStorage.setItem(PINNED_STORAGE_KEY, JSON.stringify([{ id: 'x', title: 'X' }, { nope: 1 }, { id: 'x', title: 'dup' }, 'junk']))
    resetSessionStore()
    expect(getSessionStore().pinned).toEqual([{ id: 'x', title: 'X', mode: 'chat', projectId: null }])
  })

  it('syncs titles and states and drops tabs of sessions deleted elsewhere', () => {
    pinSession({ id: 'a', title: 'Old' })
    pinSession({ id: 'gone', title: 'Gone' })
    syncSessions([{ id: 'a', title: 'New', state: 'running' }])
    expect(getSessionStore().pinned.map((item) => item.title)).toEqual(['New', 'Gone'])
    expect(getSessionStore().states.a).toBe('running')
    syncSessions([{ id: 'a', title: 'New' }], { complete: true })
    expect(getSessionStore().pinned.map((item) => item.id)).toEqual(['a'])
  })
})

describe('session context menu and header tabs', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    localStorage.clear()
    resetSessionStore()
    document.head.innerHTML = '<meta name="agentos-auth-mode" content="loopback">'
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    resetSessionStore()
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('right-click opens the menu and pins the session as a header tab', async () => {
    mockApi()
    renderApp('/chats/c1')
    await screen.findByText('Bot de cobrança')
    fireEvent.contextMenu(row('c2'))
    const menu = await screen.findByTestId('session-context-menu')
    expect(within(menu).getAllByRole('menuitem').map((item) => item.getAttribute('data-action'))).toEqual(['open', 'rename', 'pin', 'delete'])
    fireEvent.click(within(menu).getByText('Fixar no cabeçalho'))
    expect(screen.queryByTestId('session-context-menu')).toBeNull()
    const tab = await screen.findByTestId('session-tab-c2')
    expect(tab).toHaveTextContent('Bot de cobrança')
    expect(screen.getByTestId('conversation-pinned-c2')).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem(PINNED_STORAGE_KEY) ?? '[]')[0].id).toBe('c2')

    // The menu now offers to unpin.
    fireEvent.contextMenu(row('c2'))
    expect(await screen.findByText('Desafixar do cabeçalho')).toBeInTheDocument()
  })

  it('tabs switch sessions on click and unpin with the close button or middle click', async () => {
    mockApi()
    pinSession({ id: 'c1', title: 'Helpdesk sem assunto', mode: 'code' })
    pinSession({ id: 'c2', title: 'Bot de cobrança' })
    renderApp('/')
    await screen.findByText('Bot de cobrança', { selector: '.project-navigation__chat-title' })
    fireEvent.click(screen.getByTestId('session-tab-c2'))
    expect(screen.getByTestId('location')).toHaveTextContent('/chats/c2')
    expect(screen.getByTestId('session-tab-c2')).toHaveAttribute('aria-selected', 'true')

    fireEvent.click(screen.getByTestId('session-tab-close-c1'))
    expect(screen.queryByTestId('session-tab-c1')).toBeNull()
    expect(screen.getByTestId('location')).toHaveTextContent('/chats/c2')

    fireEvent(screen.getByTestId('session-tab-c2'), new MouseEvent('auxclick', { bubbles: true, button: 1 }))
    expect(screen.queryByTestId('session-tab-c2')).toBeNull()
    // The sessions stay in the sidebar.
    expect(row('c1')).toBeInTheDocument()
    expect(row('c2')).toBeInTheDocument()
  })

  it('renames inline: Enter saves through the API, Escape cancels', async () => {
    const calls = mockApi()
    pinSession({ id: 'c1', title: 'Helpdesk sem assunto', mode: 'code' })
    renderApp('/chats/c1')
    await screen.findByText('Bot de cobrança')

    fireEvent.keyDown(row('c1'), { key: 'F2' })
    const cancelled = await screen.findByTestId('session-rename-input-c1')
    fireEvent.change(cancelled, { target: { value: 'Não deve salvar' } })
    fireEvent.keyDown(cancelled, { key: 'Escape' })
    expect(screen.queryByTestId('session-rename-input-c1')).toBeNull()
    expect(calls.some((call) => call.method === 'PATCH')).toBe(false)

    fireEvent.contextMenu(row('c1'))
    fireEvent.click(await screen.findByText('Renomear'))
    const input = await screen.findByTestId('session-rename-input-c1')
    expect(input).toHaveValue('Helpdesk sem assunto')
    fireEvent.change(input, { target: { value: '  Helpdesk:   título vazio  ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(calls.find((call) => call.method === 'PATCH')?.body).toEqual({ title: 'Helpdesk: título vazio' }))
    expect(calls.find((call) => call.method === 'PATCH')?.url).toContain('/v1/conversations/c1/title')
    await waitFor(() => expect(row('c1')).toHaveTextContent('Helpdesk: título vazio'))
    expect(screen.getByTestId('session-tab-c1')).toHaveTextContent('Helpdesk: título vazio')
  })

  it('double-click on a tab renames it in place', async () => {
    const calls = mockApi()
    pinSession({ id: 'c2', title: 'Bot de cobrança' })
    renderApp('/')
    fireEvent.doubleClick(await screen.findByTestId('session-tab-c2'))
    const input = await within(screen.getByTestId('session-tab-c2')).findByRole('textbox')
    // Only the tab edits; the sidebar row keeps its title.
    expect(within(row('c2')).queryByRole('textbox')).toBeNull()
    fireEvent.change(input, { target: { value: 'Cobrança diária' } })
    fireEvent.blur(input)
    await waitFor(() => expect(calls.find((call) => call.method === 'PATCH')?.body).toEqual({ title: 'Cobrança diária' }))
  })

  it('delete asks for confirmation, offers undo, and only then calls the API', async () => {
    const calls = mockApi()
    pinSession({ id: 'c1', title: 'Helpdesk sem assunto', mode: 'code' })
    renderApp('/chats/c1')
    await screen.findByText('Bot de cobrança')

    fireEvent.contextMenu(row('c1'))
    fireEvent.click(await screen.findByText('Apagar'))
    const dialog = await screen.findByTestId('session-delete-dialog')
    expect(dialog).toHaveTextContent('Helpdesk sem assunto')
    fireEvent.click(within(dialog).getByText('Cancelar'))
    expect(screen.queryByTestId('session-delete-dialog')).toBeNull()
    expect(row('c1')).toBeInTheDocument()

    fireEvent.keyDown(row('c1'), { key: 'Delete' })
    fireEvent.click(await screen.findByTestId('session-delete-confirm'))
    expect(document.querySelector('[data-session-row="c1"]')).toBeNull()
    expect(screen.queryByTestId('session-tab-c1')).toBeNull()
    // The open session is left right away.
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/$/)
    expect(screen.getByTestId('session-toast')).toHaveTextContent('Sessão apagada')

    fireEvent.click(screen.getByTestId('session-toast-undo'))
    expect(row('c1')).toBeInTheDocument()
    expect(screen.getByTestId('session-tab-c1')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/chats/c1')
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)

    fireEvent.contextMenu(row('c1'))
    fireEvent.click(await screen.findByText('Apagar'))
    fireEvent.click(await screen.findByTestId('session-delete-confirm'))
    await act(async () => { flushDeleteOnUnload() })
    await waitFor(() => expect(calls.find((call) => call.method === 'DELETE')?.url).toContain('/v1/conversations/c1'))
    await waitFor(() => expect(document.querySelector('[data-session-row="c1"]')).toBeNull())
    expect(getSessionStore().pinned).toEqual([])
  })

  it('a refused delete (running session) brings the session back with a message', async () => {
    mockApi({ deleteStatus: 409 })
    renderApp('/')
    await screen.findByText('Bot de cobrança')
    fireEvent.contextMenu(row('c2'))
    fireEvent.click(await screen.findByText('Apagar'))
    fireEvent.click(await screen.findByTestId('session-delete-confirm'))
    await act(async () => { flushDeleteOnUnload() })
    await waitFor(() => expect(screen.getByTestId('session-toast')).toHaveTextContent('pare a sessão antes de apagá-la.'))
    expect(row('c2')).toBeInTheDocument()
  })

  it('the Orin Friend cannot be deleted', async () => {
    mockApi()
    renderApp('/')
    await screen.findByText('Bot de cobrança')
    fireEvent.contextMenu(row('friend1'))
    const menu = await screen.findByTestId('session-context-menu')
    expect(within(menu).getByText('Apagar').closest('button')).toBeDisabled()
  })

  it('dragging a sidebar row onto the header pins it; dragging tabs reorders them', async () => {
    mockApi()
    pinSession({ id: 'c1', title: 'Helpdesk sem assunto', mode: 'code' })
    renderApp('/')
    await screen.findByText('Bot de cobrança')

    const transfer = fakeDataTransfer()
    fireEvent.dragStart(row('c2'), { dataTransfer: transfer })
    expect(transfer.types).toContain(SESSION_DRAG_TYPE)
    const strip = screen.getByRole('tablist', { name: 'Sessões fixadas' })
    fireEvent.dragOver(strip, { dataTransfer: transfer })
    fireEvent.drop(strip, { dataTransfer: transfer })
    expect(getSessionStore().pinned.map((item) => item.id)).toEqual(['c1', 'c2'])

    // Drop c2 on the left half of c1 → c2 goes first.
    const moving = fakeDataTransfer()
    fireEvent.dragStart(screen.getByTestId('session-tab-c2'), { dataTransfer: moving })
    const target = screen.getByTestId('session-tab-c1')
    target.getBoundingClientRect = () => ({ left: 0, width: 100, top: 0, height: 30, right: 100, bottom: 30, x: 0, y: 0, toJSON: () => ({}) })
    // jsdom has no DragEvent, so clientX must be set on a plain event.
    const dragEvent = (type: string) => Object.assign(new Event(type, { bubbles: true, cancelable: true }), { clientX: 10, dataTransfer: moving })
    fireEvent(target, dragEvent('dragover'))
    fireEvent(target, dragEvent('drop'))
    expect(getSessionStore().pinned.map((item) => item.id)).toEqual(['c2', 'c1'])
  })

  it('offers to pin the open session when it is not pinned', () => {
    mockApi()
    const client = createBrowserApiClient()
    const current = { id: 'c9', title: 'Sessão aberta' }
    const view = render(
      <MemoryRouter initialEntries={['/chats/c9']}>
        <PinnedSessionTabs client={client} current={current} fallback={<h1>Sessão aberta</h1>} />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { level: 1, name: 'Sessão aberta' })).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('session-pin-current'))
    expect(screen.getByTestId('session-tab-c9')).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByTestId('session-pin-current')).toBeNull()

    act(() => { pinSession({ id: 'other', title: 'Outra' }); unpinSession('c9') })
    view.rerender(
      <MemoryRouter initialEntries={['/chats/c9']}>
        <PinnedSessionTabs client={client} current={current} fallback={<h1>Sessão aberta</h1>} />
      </MemoryRouter>,
    )
    expect(screen.getByTestId('session-pin-current')).toHaveTextContent('Fixar esta')
  })
})

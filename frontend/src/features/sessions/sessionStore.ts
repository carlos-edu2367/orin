// Shared state for the session context menu (rename / pin / delete) and the
// pinned session tabs in the header. Every page mounts its own header and
// sidebar, so this lives outside React and is read with useSyncExternalStore.
import { useSyncExternalStore } from 'react'
import type { ApiClient } from '../../api/client'
import type { ConversationMode } from '../../api/conversations'
import { renameConversation } from '../../api/sessionTools'

export type SessionRef = {
  id: string
  title: string
  mode?: ConversationMode
  projectId?: string | null
}

export type SessionLiveInfo = SessionRef & { state?: string }

export type SessionToast = { key: number; text: string; tone: 'info' | 'error'; undo: boolean }

/** Where an inline rename happens: the sidebar row or the header tab. */
export type RenameSurface = 'nav' | 'tab'

export type SessionStoreSnapshot = {
  pinned: SessionRef[]
  states: Readonly<Record<string, string>>
  titles: Readonly<Record<string, string>>
  hidden: readonly string[]
  renaming: { id: string; where: RenameSurface } | null
  confirmDelete: SessionRef | null
  toast: SessionToast | null
}

export const PINNED_STORAGE_KEY = 'orin.pinned-sessions'
export const DELETE_UNDO_MS = 6000
export const SESSION_DRAG_TYPE = 'application/x-orin-session'

function readPinned(): SessionRef[] {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return []
    const parsed: unknown = JSON.parse(window.localStorage.getItem(PINNED_STORAGE_KEY) || '[]')
    if (!Array.isArray(parsed)) return []
    const seen = new Set<string>()
    const out: SessionRef[] = []
    for (const item of parsed) {
      if (!item || typeof item !== 'object') continue
      const raw = item as Record<string, unknown>
      if (typeof raw.id !== 'string' || !raw.id || seen.has(raw.id)) continue
      seen.add(raw.id)
      out.push({
        id: raw.id,
        title: typeof raw.title === 'string' && raw.title ? raw.title : 'Conversa',
        mode: raw.mode === 'code' || raw.mode === 'friend' ? raw.mode : 'chat',
        projectId: typeof raw.projectId === 'string' && raw.projectId ? raw.projectId : null,
      })
    }
    return out
  } catch {
    return []
  }
}

function writePinned(pinned: SessionRef[]): void {
  try {
    window.localStorage.setItem(PINNED_STORAGE_KEY, JSON.stringify(pinned))
  } catch {
    // Storage may be unavailable or full; the tabs still work for this window.
  }
}

let state: SessionStoreSnapshot = {
  pinned: readPinned(), states: {}, titles: {}, hidden: [], renaming: null, confirmDelete: null, toast: null,
}
const listeners = new Set<() => void>()
let toastTimer: ReturnType<typeof setTimeout> | null = null
let toastKey = 0
let pendingDelete: { ref: SessionRef; timer: ReturnType<typeof setTimeout>; pinnedAt: number; run: () => Promise<void>; onUndo?: () => void } | null = null

function set(patch: Partial<SessionStoreSnapshot>): void {
  state = { ...state, ...patch }
  if (patch.pinned) writePinned(patch.pinned)
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function getSessionStore(): SessionStoreSnapshot {
  return state
}

export function useSessionStore(): SessionStoreSnapshot {
  return useSyncExternalStore(subscribe, getSessionStore, getSessionStore)
}

/** Test helper: back to a clean store (re-reading localStorage). */
export function resetSessionStore(): void {
  if (toastTimer) clearTimeout(toastTimer)
  if (pendingDelete) clearTimeout(pendingDelete.timer)
  toastTimer = null
  pendingDelete = null
  state = { pinned: readPinned(), states: {}, titles: {}, hidden: [], renaming: null, confirmDelete: null, toast: null }
  for (const listener of listeners) listener()
}

export function sessionPath(ref: Pick<SessionRef, 'id' | 'projectId'>): string {
  const id = encodeURIComponent(ref.id)
  return ref.projectId ? `/projects/${encodeURIComponent(ref.projectId)}/chats/${id}` : `/chats/${id}`
}

export function displayTitle(ref: SessionRef, snapshot: SessionStoreSnapshot = state): string {
  return snapshot.titles[ref.id] ?? ref.title
}

export function notifyConversationsUpdated(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new Event('orin:conversations-updated'))
}

// ---------- Toast ----------

export function showSessionToast(text: string, options: { tone?: 'info' | 'error'; undo?: boolean; durationMs?: number } = {}): void {
  if (toastTimer) clearTimeout(toastTimer)
  toastKey += 1
  const key = toastKey
  set({ toast: { key, text, tone: options.tone ?? 'info', undo: Boolean(options.undo) } })
  toastTimer = setTimeout(() => {
    if (state.toast?.key === key) set({ toast: null })
  }, options.durationMs ?? (options.undo ? DELETE_UNDO_MS : 2600))
}

export function dismissSessionToast(): void {
  if (toastTimer) clearTimeout(toastTimer)
  set({ toast: null })
}

// ---------- Pins ----------

export function isPinned(id: string): boolean {
  return state.pinned.some((item) => item.id === id)
}

export function pinSession(ref: SessionRef, index?: number): void {
  const rest = state.pinned.filter((item) => item.id !== ref.id)
  const entry: SessionRef = { id: ref.id, title: ref.title, mode: ref.mode ?? 'chat', projectId: ref.projectId ?? null }
  const at = index === undefined ? rest.length : Math.max(0, Math.min(index, rest.length))
  set({ pinned: [...rest.slice(0, at), entry, ...rest.slice(at)] })
}

export function unpinSession(id: string): void {
  if (!isPinned(id)) return
  set({ pinned: state.pinned.filter((item) => item.id !== id) })
}

export function togglePin(ref: SessionRef): void {
  if (isPinned(ref.id)) unpinSession(ref.id)
  else pinSession(ref)
}

/**
 * Refresh the pinned tabs from the sidebar lists: titles, modes, projects and
 * live states. With `complete`, a pinned session missing from the lists was
 * deleted elsewhere and its tab goes away.
 */
export function syncSessions(sessions: readonly SessionLiveInfo[], { complete = false } = {}): void {
  const byId = new Map(sessions.map((item) => [item.id, item]))
  let changed = false
  const pinned: SessionRef[] = []
  for (const item of state.pinned) {
    const fresh = byId.get(item.id)
    if (!fresh) {
      if (complete && !state.hidden.includes(item.id)) {
        changed = true
        continue
      }
      pinned.push(item)
      continue
    }
    const next = { id: item.id, title: fresh.title || item.title, mode: fresh.mode ?? item.mode, projectId: fresh.projectId ?? null }
    if (next.title !== item.title || next.mode !== item.mode || next.projectId !== (item.projectId ?? null)) changed = true
    pinned.push(next)
  }
  const states: Record<string, string> = { ...state.states }
  let statesChanged = false
  for (const item of sessions) {
    if (item.state && states[item.id] !== item.state) {
      states[item.id] = item.state
      statesChanged = true
    }
  }
  // A title override is dropped once the server already reports the new title.
  const titles: Record<string, string> = {}
  let titlesChanged = false
  for (const [id, title] of Object.entries(state.titles)) {
    if (byId.get(id)?.title === title) titlesChanged = true
    else titles[id] = title
  }
  if (!changed && !statesChanged && !titlesChanged) return
  set({ ...(changed ? { pinned } : {}), ...(statesChanged ? { states } : {}), ...(titlesChanged ? { titles } : {}) })
}

// ---------- Rename ----------

export function startRename(id: string, where: RenameSurface = 'nav'): void {
  set({ renaming: { id, where } })
}

export function isRenaming(snapshot: SessionStoreSnapshot, id: string, where: RenameSurface): boolean {
  return snapshot.renaming?.id === id && snapshot.renaming.where === where
}

export function cancelRename(): void {
  if (state.renaming !== null) set({ renaming: null })
}

export async function commitRename(client: ApiClient, ref: SessionRef, value: string): Promise<void> {
  const title = value.replace(/\s+/g, ' ').trim()
  const previous = displayTitle(ref)
  if (state.renaming?.id === ref.id) set({ renaming: null })
  if (!title || title === previous) return
  set({ titles: { ...state.titles, [ref.id]: title } })
  try {
    const result = await renameConversation(client, ref.id, title)
    const saved = result.title || title
    set({
      titles: { ...state.titles, [ref.id]: saved },
      pinned: state.pinned.map((item) => (item.id === ref.id ? { ...item, title: saved } : item)),
    })
    notifyConversationsUpdated()
    window.dispatchEvent(new CustomEvent('orin:conversation-renamed', { detail: { conversationId: ref.id, title: saved } }))
  } catch {
    const titles = { ...state.titles }
    delete titles[ref.id]
    set({ titles })
    showSessionToast('Não foi possível renomear a sessão.', { tone: 'error' })
  }
}

// ---------- Delete ----------

export function requestDelete(ref: SessionRef): void {
  if (ref.mode === 'friend') {
    showSessionToast('O Orin Friend não pode ser apagado.', { tone: 'error' })
    return
  }
  set({ confirmDelete: ref })
}

export function cancelDelete(): void {
  if (state.confirmDelete) set({ confirmDelete: null })
}

function errorMessage(error: unknown): string {
  const { status, code } = (error ?? {}) as { status?: number; code?: string }
  if (code === 'conversation_protected') return 'O Orin Friend não pode ser apagado.'
  if (status === 409) return 'Esta sessão ainda está em andamento: pare a sessão antes de apagá-la.'
  if (status === 404) return 'Essa sessão já não existe.'
  return 'Não foi possível apagar a sessão.'
}

/** Runs the pending deletion now (e.g. a second deletion starts while one is waiting). */
async function flushPendingDelete(): Promise<void> {
  const current = pendingDelete
  if (!current) return
  clearTimeout(current.timer)
  pendingDelete = null
  await current.run()
}

/**
 * Hides the session, offers "Desfazer" for a few seconds and only then asks the
 * backend to delete it. `onLeave` runs right away when the session is open, and
 * `onUndo` brings the person back if they undo.
 */
export function confirmDelete(
  remove: (id: string) => Promise<unknown>,
  hooks: { onLeave?: (ref: SessionRef) => void; onUndo?: (ref: SessionRef) => void } = {},
): void {
  const ref = state.confirmDelete
  if (!ref) return
  // A deletion still waiting out its undo window goes through now; only one waits at a time.
  void flushPendingDelete()
  const pinnedAt = state.pinned.findIndex((item) => item.id === ref.id)
  set({
    confirmDelete: null,
    hidden: [...state.hidden.filter((id) => id !== ref.id), ref.id],
    pinned: state.pinned.filter((item) => item.id !== ref.id),
  })
  hooks.onLeave?.(ref)
  const restore = () => {
    const pinned = [...state.pinned.filter((item) => item.id !== ref.id)]
    if (pinnedAt >= 0) pinned.splice(Math.min(pinnedAt, pinned.length), 0, ref)
    set({ hidden: state.hidden.filter((id) => id !== ref.id), pinned })
  }
  const run = async () => {
    try {
      await remove(ref.id)
      set({ hidden: state.hidden.filter((id) => id !== ref.id) })
      notifyConversationsUpdated()
    } catch (error) {
      restore()
      showSessionToast(errorMessage(error), { tone: 'error', durationMs: 4500 })
    }
  }
  const timer = setTimeout(() => {
    if (pendingDelete?.ref.id === ref.id) {
      pendingDelete = null
      void run()
    }
  }, DELETE_UNDO_MS)
  pendingDelete = { ref, timer, pinnedAt, run, onUndo: hooks.onUndo ? () => hooks.onUndo?.(ref) : undefined }
  showSessionToast('Sessão apagada', { undo: true })
}

export function undoDelete(): void {
  const current = pendingDelete
  if (!current) return
  clearTimeout(current.timer)
  pendingDelete = null
  const pinned = [...state.pinned.filter((item) => item.id !== current.ref.id)]
  if (current.pinnedAt >= 0) pinned.splice(Math.min(current.pinnedAt, pinned.length), 0, current.ref)
  set({ hidden: state.hidden.filter((id) => id !== current.ref.id), pinned })
  dismissSessionToast()
  current.onUndo?.()
}

/** If the window closes during the undo window, the deletion still happens. */
export function flushDeleteOnUnload(): void {
  void flushPendingDelete()
}

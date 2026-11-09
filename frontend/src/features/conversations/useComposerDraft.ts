import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'

/**
 * Keeps what the person is typing in a composer across navigation and app restarts.
 *
 * The text lives in the caller's own state; this hook only mirrors it to localStorage
 * under a context key (a new session, or one conversation), restores it when the key
 * mounts or changes, and forgets it when the text is emptied (a successful send) or
 * when `clearDraft()` is called. Attachments are not persisted.
 */

export const DRAFT_KEY_PREFIX = 'orin.draft.'
export const MAX_DRAFT_CHARACTERS = 20_000
export const DRAFT_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000
export const MAX_STORED_DRAFTS = 100
const DEFAULT_DEBOUNCE_MS = 300

type StoredDraft = { text: string; updatedAt: number }

/** Key of the composer on a "new session" page (Home), scoped by project. */
export function newSessionDraftKey(projectId?: string | null): string {
  return `${DRAFT_KEY_PREFIX}new:${projectId || 'none'}`
}

/** Key of the composer inside an existing conversation. */
export function conversationDraftKey(conversationId: string): string {
  return `${DRAFT_KEY_PREFIX}conv:${conversationId}`
}

function storage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

function parseStored(raw: string | null): StoredDraft | null {
  if (raw === null) return null
  try {
    const parsed = JSON.parse(raw) as Partial<StoredDraft> | null
    if (parsed && typeof parsed.text === 'string') {
      return { text: parsed.text, updatedAt: typeof parsed.updatedAt === 'number' ? parsed.updatedAt : 0 }
    }
  } catch {
    // Not JSON: an older or foreign value; treat it as plain text.
  }
  return { text: raw, updatedAt: 0 }
}

export function readDraft(key: string): string | null {
  try {
    const stored = parseStored(storage()?.getItem(key) ?? null)
    return stored && stored.text.length > 0 ? stored.text : null
  } catch {
    return null
  }
}

export function removeDraft(key: string): void {
  try {
    storage()?.removeItem(key)
  } catch {
    // Storage unavailable: nothing to forget.
  }
}

/** Stores the text (capped), or forgets the key when the text is blank. */
export function writeDraft(key: string, text: string, now: number = Date.now()): void {
  if (text.trim().length === 0) {
    removeDraft(key)
    return
  }
  try {
    const value: StoredDraft = { text: text.slice(0, MAX_DRAFT_CHARACTERS), updatedAt: now }
    storage()?.setItem(key, JSON.stringify(value))
  } catch {
    // Quota exceeded or private mode: a draft is a convenience, never an error.
  }
}

/** Drops drafts older than `maxAgeMs` and keeps at most `maxCount` of the newest. */
export function pruneDrafts(now: number = Date.now(), maxAgeMs = DRAFT_MAX_AGE_MS, maxCount = MAX_STORED_DRAFTS): void {
  try {
    const store = storage()
    if (!store) return
    const entries: Array<{ key: string; updatedAt: number }> = []
    for (let index = 0; index < store.length; index += 1) {
      const key = store.key(index)
      if (!key || !key.startsWith(DRAFT_KEY_PREFIX)) continue
      const stored = parseStored(store.getItem(key))
      entries.push({ key, updatedAt: stored?.updatedAt ?? 0 })
    }
    const expired = entries.filter((entry) => now - entry.updatedAt > maxAgeMs)
    const fresh = entries
      .filter((entry) => now - entry.updatedAt <= maxAgeMs)
      .sort((left, right) => right.updatedAt - left.updatedAt)
    for (const entry of [...expired, ...fresh.slice(maxCount)]) store.removeItem(entry.key)
  } catch {
    // Best effort only.
  }
}

let prunedThisSession = false

export type ComposerDraftOptions = {
  /** Keep the current value instead of restoring the stored draft (e.g. an explicit prefill). Read when the key mounts. */
  skipRestore?: boolean
  debounceMs?: number
}

/**
 * Mirrors `value` to localStorage under `key`.
 *
 * - on mount / key change: restores the stored draft through `setValue` (a key with no
 *   draft, after a change of key, clears the value so one context never leaks into another);
 * - while typing: writes debounced; on unmount or key change: writes immediately;
 * - blank text removes the key, so a send that empties the composer clears the draft and a
 *   failed send that puts the text back keeps it.
 */
export function useComposerDraft(
  key: string | null,
  value: string,
  setValue: (next: string) => void,
  options: ComposerDraftOptions = {},
): { clearDraft: () => void } {
  const { skipRestore = false, debounceMs = DEFAULT_DEBOUNCE_MS } = options
  const valueRef = useRef(value)
  const setValueRef = useRef(setValue)
  const skipRestoreRef = useRef(skipRestore)
  const previousKeyRef = useRef<string | null>(null)
  // After clearDraft(), the text still on screen must not be written back until it changes.
  const suppressedRef = useRef<{ value: string } | null>(null)
  const timerRef = useRef<number | null>(null)

  useLayoutEffect(() => {
    valueRef.current = value
    setValueRef.current = setValue
    skipRestoreRef.current = skipRestore
  })

  useEffect(() => {
    if (prunedThisSession) return
    prunedThisSession = true
    pruneDrafts()
  }, [])

  useLayoutEffect(() => {
    if (!key) return
    const previousKey = previousKeyRef.current
    previousKeyRef.current = key
    suppressedRef.current = null
    if (!skipRestoreRef.current) {
      const stored = readDraft(key)
      if (stored !== null) {
        if (stored !== valueRef.current) setValueRef.current(stored)
      } else if (previousKey !== null && previousKey !== key && valueRef.current !== '') {
        setValueRef.current('')
      }
    }
    return () => {
      const suppressed = suppressedRef.current
      if (suppressed && suppressed.value === valueRef.current) return
      writeDraft(key, valueRef.current)
    }
  }, [key])

  useEffect(() => {
    if (!key) return
    const suppressed = suppressedRef.current
    if (suppressed) {
      if (suppressed.value === value) return
      suppressedRef.current = null
    }
    const timer = window.setTimeout(() => {
      timerRef.current = null
      writeDraft(key, value)
    }, debounceMs)
    timerRef.current = timer
    return () => {
      window.clearTimeout(timer)
      if (timerRef.current === timer) timerRef.current = null
    }
  }, [key, value, debounceMs])

  const clearDraft = useCallback(() => {
    if (!key) return
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
    suppressedRef.current = { value: valueRef.current }
    removeDraft(key)
  }, [key])

  return { clearDraft }
}

/** Test helper: lets a test run the once-per-session prune again. */
export function resetDraftPruneForTests(): void {
  prunedThisSession = false
}

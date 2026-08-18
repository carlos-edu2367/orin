import { useEffect, useRef, useState } from 'react'
import { ApiClient, type MutationIntent } from '../../api/client'
import type { BrowserSessionBootstrap } from '../../api/browserSession'
import { ApiError } from '../../api/errors'
import {
  addProviderKey, listProviderKeys, removeProviderKey, renameProviderKey, reorderProviderKeys,
  type ProviderApiKeyState, type ProviderName,
} from '../../api/providers'
import { toApiError } from './useProviderState'

export type ProviderKeysLoadState = { status: 'loading' } | { status: 'loaded' } | { status: 'unavailable'; error: ApiError }
export type ProviderKeysAction = 'add' | 'rename' | 'remove' | 'reorder' | null
export type ProviderKeysActionState = { pending: boolean; error: ApiError | null; kind: ProviderKeysAction }

export type ProviderKeysState = {
  load: ProviderKeysLoadState
  keys: ProviderApiKeyState[]
  action: ProviderKeysActionState
  add: (apiKey: string, label?: string) => Promise<void>
  rename: (keyId: number, label: string | null) => Promise<void>
  remove: (keyId: number) => Promise<void>
  moveUp: (keyId: number) => Promise<void>
  moveDown: (keyId: number) => Promise<void>
}

export function useProviderKeysState(client: ApiClient, provider: ProviderName, bootstrap: BrowserSessionBootstrap): ProviderKeysState {
  const [load, setLoad] = useState<ProviderKeysLoadState>({ status: 'loading' })
  const [keys, setKeys] = useState<ProviderApiKeyState[]>([])
  const [action, setAction] = useState<ProviderKeysActionState>({ pending: false, error: null, kind: null })
  const intents = useRef(new Map<string, MutationIntent>())

  useEffect(() => {
    const controller = new AbortController()
    listProviderKeys(client, provider, controller.signal).then((loaded) => {
      if (controller.signal.aborted) return
      setKeys(loaded)
      setLoad({ status: 'loaded' })
    }).catch((error: unknown) => {
      if (controller.signal.aborted) return
      setLoad({ status: 'unavailable', error: toApiError(error) })
    })
    return () => controller.abort()
  }, [client, provider])

  const intentFor = (key: string) => {
    const existing = intents.current.get(key)
    if (existing) return existing
    const intent = client.createMutationIntent()
    intents.current.set(key, intent)
    return intent
  }

  async function add(apiKey: string, label?: string) {
    if (action.pending || !apiKey || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'add' })
    try {
      const created = await addProviderKey(client, provider, { apiKey, label }, intentFor('add'))
      intents.current.delete('add')
      setKeys((current) => [...current, created])
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'add' })
    }
  }

  async function rename(keyId: number, label: string | null) {
    if (action.pending || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'rename' })
    try {
      const updated = await renameProviderKey(client, provider, keyId, label, intentFor(`rename:${keyId}`))
      intents.current.delete(`rename:${keyId}`)
      setKeys((current) => current.map((key) => key.id === updated.id ? updated : key))
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'rename' })
    }
  }

  async function remove(keyId: number) {
    if (action.pending || bootstrap.status === 'missing_csrf') return
    setAction({ pending: true, error: null, kind: 'remove' })
    try {
      await removeProviderKey(client, provider, keyId, intentFor(`remove:${keyId}`))
      intents.current.delete(`remove:${keyId}`)
      setKeys((current) => current.filter((key) => key.id !== keyId).map((key, index) => ({ ...key, position: index })))
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'remove' })
    }
  }

  async function move(keyId: number, direction: -1 | 1) {
    if (action.pending || bootstrap.status === 'missing_csrf') return
    const index = keys.findIndex((key) => key.id === keyId)
    const swapWith = index + direction
    if (index < 0 || swapWith < 0 || swapWith >= keys.length) return
    const reordered = [...keys]
    ;[reordered[index], reordered[swapWith]] = [reordered[swapWith], reordered[index]]
    setAction({ pending: true, error: null, kind: 'reorder' })
    try {
      const saved = await reorderProviderKeys(client, provider, reordered.map((key) => key.id), intentFor('reorder'))
      intents.current.delete('reorder')
      setKeys(saved)
      setAction({ pending: false, error: null, kind: null })
    } catch (error) {
      setAction({ pending: false, error: toApiError(error), kind: 'reorder' })
    }
  }

  return {
    load, keys, action,
    add, rename, remove,
    moveUp: (keyId: number) => move(keyId, -1),
    moveDown: (keyId: number) => move(keyId, 1),
  }
}

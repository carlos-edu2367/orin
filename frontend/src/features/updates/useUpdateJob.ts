import { useCallback, useEffect, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { ACTIVE_UPDATE_STATES, getUpdateStatus, prepareUpdate, type UpdateJobStatus } from '../../api/updateJob'

const POLL_MS = 1000

/**
 * The background update, as the page sees it: the current status, a way to
 * start the download, and polling for as long as the backend is working.
 */
export function useUpdateJob(client: ApiClient, enabled = true) {
  const [status, setStatus] = useState<UpdateJobStatus | null>(null)
  const [requestFailed, setRequestFailed] = useState(false)
  const active = status !== null && ACTIVE_UPDATE_STATES.includes(status.state)

  useEffect(() => {
    if (!enabled) return undefined
    let live = true
    getUpdateStatus(client).then((value) => { if (live) setStatus(value) }).catch(() => undefined)
    return () => { live = false }
  }, [client, enabled])

  useEffect(() => {
    if (!active) return undefined
    const timer = window.setInterval(() => {
      getUpdateStatus(client).then((value) => { setRequestFailed(false); setStatus(value) }).catch(() => setRequestFailed(true))
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [client, active])

  const start = useCallback(async () => {
    setRequestFailed(false)
    try { setStatus(await prepareUpdate(client)) } catch { setRequestFailed(true) }
  }, [client])

  return { status, start, requestFailed }
}

export function formatMegabytes(bytes: number | null): string {
  return bytes === null ? '' : `${(bytes / 1_048_576).toFixed(1).replace('.', ',')} MB`
}

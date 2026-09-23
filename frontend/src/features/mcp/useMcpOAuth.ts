import { useCallback, useEffect, useRef, useState } from 'react'
import type { ApiClient } from '../../api/client'
import { cancelMcpOAuth, getMcpServer, startMcpOAuth } from '../../api/mcp'

export const MCP_OAUTH_POLL_MS = 2000
export const MCP_OAUTH_TIMEOUT_MS = 10 * 60 * 1000

export type McpOAuthPhase = 'idle' | 'starting' | 'waiting' | 'failed'

export type McpOAuthControl = {
  phase: McpOAuthPhase
  reason: string | null
  /** Set when the browser refused to open a new tab; the panel offers it as a link. */
  pendingUrl: string | null
  start: () => Promise<void>
  cancel: () => Promise<void>
}

type Options = { openWindow?: (url: string) => Window | null }

function openInNewTab(url: string): Window | null {
  // `noopener` in the features string makes window.open return null even on
  // success, which would hide a blocked popup; drop the opener by hand instead.
  const opened = window.open(url, '_blank')
  if (opened) opened.opener = null
  return opened
}

/**
 * Sign-in for an OAuth MCP server: the API hands back the authorization URL,
 * the system browser does the consent, and the API's own callback activates
 * the server. This hook only watches the server until that happens.
 */
export function useMcpOAuth(client: ApiClient, serverId: string, onConnected: () => void, options: Options = {}): McpOAuthControl {
  const [phase, setPhase] = useState<McpOAuthPhase>('idle')
  const [reason, setReason] = useState<string | null>(null)
  const [pendingUrl, setPendingUrl] = useState<string | null>(null)
  const timers = useRef<{ poll?: ReturnType<typeof setInterval>; timeout?: ReturnType<typeof setTimeout> }>({})
  const openWindow = options.openWindow ?? openInNewTab
  const onConnectedRef = useRef(onConnected)
  useEffect(() => { onConnectedRef.current = onConnected }, [onConnected])

  const stopTimers = useCallback(() => {
    clearInterval(timers.current.poll)
    clearTimeout(timers.current.timeout)
    timers.current = {}
  }, [])

  useEffect(() => stopTimers, [stopTimers])

  const fail = useCallback((message: string) => {
    stopTimers()
    setPendingUrl(null)
    setReason(message)
    setPhase('failed')
  }, [stopTimers])

  const poll = useCallback(async () => {
    try {
      const server = await getMcpServer(client, serverId)
      if (server.state === 'active') {
        stopTimers()
        setPendingUrl(null)
        setPhase('idle')
        onConnectedRef.current()
        return
      }
      if (server.state_reason) fail(server.state_reason)
    } catch {
      // A transient read failure: the next tick tries again.
    }
  }, [client, serverId, stopTimers, fail])

  const pollRef = useRef(poll)
  useEffect(() => { pollRef.current = poll }, [poll])

  const start = useCallback(async () => {
    stopTimers()
    setReason(null)
    setPendingUrl(null)
    setPhase('starting')
    let url: string
    try {
      url = (await startMcpOAuth(client, serverId)).authorization_url
    } catch {
      let message = 'Não foi possível iniciar o login.'
      try {
        const server = await getMcpServer(client, serverId)
        if (server.state_reason) message = server.state_reason
      } catch {
        // Keep the generic message.
      }
      fail(message)
      return
    }
    if (!openWindow(url)) setPendingUrl(url)
    setPhase('waiting')
    timers.current.poll = setInterval(() => { void pollRef.current() }, MCP_OAUTH_POLL_MS)
    timers.current.timeout = setTimeout(() => {
      void cancelMcpOAuth(client, serverId).catch(() => undefined)
      fail('O tempo para autorizar acabou. Tente de novo.')
    }, MCP_OAUTH_TIMEOUT_MS)
  }, [client, serverId, openWindow, stopTimers, fail])

  const cancel = useCallback(async () => {
    stopTimers()
    setPendingUrl(null)
    setReason(null)
    setPhase('idle')
    try {
      await cancelMcpOAuth(client, serverId)
    } catch {
      // The pending sign-in expires on its own.
    }
  }, [client, serverId, stopTimers])

  return { phase, reason, pendingUrl, start, cancel }
}

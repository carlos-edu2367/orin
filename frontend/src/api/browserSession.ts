export type BrowserSessionBootstrap =
  | { status: 'ready'; csrfToken: string }
  | { status: 'loopback' }
  | { status: 'missing_csrf' }

export function readBrowserSessionBootstrap(documentRef?: Document): BrowserSessionBootstrap {
  const authMode = documentRef
    ?.querySelector<HTMLMetaElement>('meta[name="agentos-auth-mode"]')
    ?.content
    .trim()
  if (authMode === 'loopback') return { status: 'loopback' }

  const csrfToken = documentRef
    ?.querySelector<HTMLMetaElement>('meta[name="csrf-token"]')
    ?.content
    .trim()

  if (!csrfToken || csrfToken.length > 255) return { status: 'missing_csrf' }
  return { status: 'ready', csrfToken }
}

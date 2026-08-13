import { ApiError, invalidResponseError, parseApiErrorResponse } from './errors'
import { readBrowserSessionBootstrap } from './browserSession'

export type MutationIntent = Readonly<{ idempotencyKey: string }>

type ApiClientOptions = {
  baseUrl?: string
  bearerToken?: string
  csrfToken?: string
  fetchImpl?: typeof fetch
  maxAttempts?: number
  retryDelayMs?: number
  createIdempotencyKey?: () => string
}

type BrowserApiClientOptions = Pick<ApiClientOptions, 'baseUrl' | 'fetchImpl'>

type RequestOptions<T> = {
  path: string
  query?: Record<string, string | number | boolean | null | undefined>
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  expectedStatus?: number
  body?: unknown
  intent?: MutationIntent
  signal?: AbortSignal
  parse: (value: unknown) => T
}

export type StreamRequestOptions = {
  path: string
  signal?: AbortSignal
}

export class ApiClient {
  private readonly baseUrl: string
  private readonly bearerToken?: string
  private readonly csrfToken?: string
  private readonly fetchImpl: typeof fetch
  private readonly maxAttempts: number
  private readonly retryDelayMs: number
  private readonly createKey: () => string

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl?.replace(/\/$/, '') ?? ''
    this.bearerToken = options.bearerToken
    this.csrfToken = options.csrfToken
    this.fetchImpl = options.fetchImpl ?? fetch
    this.maxAttempts = Math.max(1, Math.min(options.maxAttempts ?? 2, 3))
    this.retryDelayMs = Math.max(0, options.retryDelayMs ?? 250)
    this.createKey = options.createIdempotencyKey ?? defaultIdempotencyKey
  }

  createMutationIntent(): MutationIntent {
    return Object.freeze({ idempotencyKey: this.createKey() })
  }

  async request<T>(options: RequestOptions<T>): Promise<T> {
    const method = options.method ?? 'GET'
    const url = this.safeUrl(options.path, options.query)
    const headers = new Headers({ Accept: 'application/json' })
    if (options.body !== undefined) headers.set('Content-Type', 'application/json')
    if (this.bearerToken) headers.set('Authorization', `Bearer ${this.bearerToken}`)
    if (this.csrfToken && method !== 'GET') headers.set('X-CSRF-Token', this.csrfToken)
    if (options.intent) headers.set('Idempotency-Key', options.intent.idempotencyKey)

    for (let attempt = 1; attempt <= this.maxAttempts; attempt += 1) {
      if (options.signal?.aborted) throw abortError()
      try {
        const response = await this.fetchImpl.call(globalThis, url, {
          method,
          headers,
          credentials: 'same-origin',
          body: options.body === undefined ? undefined : JSON.stringify(options.body),
          signal: options.signal,
        })
        if (!response.ok || (options.expectedStatus !== undefined && response.status !== options.expectedStatus)) {
          const error = await parseApiErrorResponse(response)
          if (error.retryable && attempt < this.maxAttempts) {
            await delay(this.retryDelayMs, options.signal)
            continue
          }
          throw error
        }

        let body: unknown
        try {
          // A 204 has no body by definition; parsing one would only fail.
          body = response.status === 204 ? undefined : await response.json()
          return options.parse(body)
        } catch (error) {
          if (error instanceof ApiError) throw error
          throw invalidResponseError()
        }
      } catch (error) {
        if (error instanceof ApiError) throw error
        if (options.signal?.aborted) throw abortError()
        if (attempt < this.maxAttempts) {
          await delay(this.retryDelayMs, options.signal)
          continue
        }
        throw new ApiError({
          status: 0,
          code: 'network_error',
          category: 'INDETERMINATE',
          messageKey: 'network_error',
          retryable: true,
        })
      }
    }
    throw invalidResponseError()
  }

  async upload<T>(options: { path: string; body: FormData; expectedStatus?: number; parse: (value: unknown) => T }): Promise<T> {
    // No Content-Type header is set here: the browser must generate the
    // multipart boundary itself from the FormData body.
    const url = this.safeUrl(options.path)
    const headers = new Headers({ Accept: 'application/json' })
    if (this.bearerToken) headers.set('Authorization', `Bearer ${this.bearerToken}`)
    if (this.csrfToken) headers.set('X-CSRF-Token', this.csrfToken)
    const response = await this.fetchImpl.call(globalThis, url, {
      method: 'POST',
      headers,
      credentials: 'same-origin',
      body: options.body,
    })
    if (!response.ok || (options.expectedStatus !== undefined && response.status !== options.expectedStatus)) {
      throw await parseApiErrorResponse(response)
    }
    try {
      return options.parse(await response.json())
    } catch (error) {
      if (error instanceof ApiError) throw error
      throw invalidResponseError()
    }
  }

  async stream(options: StreamRequestOptions): Promise<Response> {
    const url = this.safeStreamUrl(options.path)
    const headers = new Headers({ Accept: 'text/event-stream' })
    if (this.bearerToken) headers.set('Authorization', `Bearer ${this.bearerToken}`)
    const response = await this.fetchImpl.call(globalThis, url, {
      method: 'GET',
      headers,
      credentials: 'same-origin',
      signal: options.signal,
    })
    return response
  }

  private safeUrl(path: string, query?: Record<string, string | number | boolean | null | undefined>): string {
    if (!path.startsWith('/') || path.includes('?') || path.includes('#')) {
      throw new TypeError('API paths must be absolute and must not contain query data')
    }
    const parameters = new URLSearchParams()
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value !== undefined && value !== null) parameters.set(key, String(value))
    }
    const encoded = parameters.toString()
    return `${this.baseUrl}${path}${encoded ? `?${encoded}` : ''}`
  }

  private safeStreamUrl(path: string): string {
    if (!path.startsWith('/') || path.includes('#')) throw new TypeError('API stream paths must be absolute and must not contain fragments')
    const [pathname, query = ''] = path.split('?', 2)
    if (!pathname || pathname.includes('?') || query.includes('#')) throw new TypeError('API stream path is invalid')
    return `${this.baseUrl}${pathname}${query ? `?${query}` : ''}`
  }
}

export function createBrowserApiClient(options: BrowserApiClientOptions = {}): ApiClient {
  const bootstrap = typeof document === 'undefined'
    ? { status: 'missing_csrf' as const }
    : readBrowserSessionBootstrap(document)
  const csrfToken = bootstrap.status === 'ready' ? bootstrap.csrfToken : undefined
  return new ApiClient({ ...options, csrfToken, maxAttempts: 1 })
}

function defaultIdempotencyKey(): string {
  if (typeof globalThis.crypto?.randomUUID !== 'function') {
    throw new Error('Secure idempotency key generation is unavailable')
  }
  return globalThis.crypto.randomUUID()
}

function delay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  if (milliseconds === 0) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const timeout = globalThis.setTimeout(resolve, milliseconds)
    signal?.addEventListener('abort', () => {
      globalThis.clearTimeout(timeout)
      reject(abortError())
    }, { once: true })
  })
}

function abortError(): DOMException {
  return new DOMException('The request was aborted', 'AbortError')
}

export type ApiErrorDetails = {
  status: number
  code: string
  category: string
  messageKey: string
  correlationId: string | null
  retryable: boolean
  retryAfter: number | null
}

type ApiErrorInput = Partial<Omit<ApiErrorDetails, 'status'>> & Pick<ApiErrorDetails, 'status'>

const RESYNC_CODES = new Set([
  'cursor_invalid',
  'invalid_cursor',
  'cursor_expired',
  'retention_expired',
  'retention_insufficient',
  'stream_revoked',
  'sequence_gap',
  'version_gap',
  'version_conflict',
  'indeterminate',
])

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly category: string
  readonly messageKey: string
  readonly correlationId: string | null
  readonly retryable: boolean
  readonly retryAfter: number | null

  constructor(input: ApiErrorInput) {
    const code = safeString(input.code) ?? 'request_failed'
    const messageKey = safeString(input.messageKey) ?? code
    super(`Orin request failed: ${messageKey}`)
    this.name = 'ApiError'
    this.status = Number.isInteger(input.status) ? input.status : 0
    this.code = code
    this.category = safeString(input.category) ?? 'INTERNAL'
    this.messageKey = messageKey
    this.correlationId = safeString(input.correlationId) ?? null
    this.retryable = input.retryable === true
    this.retryAfter = safeRetryAfter(input.retryAfter)
  }

  toPublicDetails(): ApiErrorDetails {
    return {
      status: this.status,
      code: this.code,
      category: this.category,
      messageKey: this.messageKey,
      correlationId: this.correlationId,
      retryable: this.retryable,
      retryAfter: this.retryAfter,
    }
  }
}

export function parseApiError(status: number, value: unknown): ApiError {
  const envelope = isRecord(value) && isRecord(value.error) ? value.error : null
  if (!envelope) return fallbackError(status)

  return new ApiError({
    status,
    code: safeString(envelope.code) ?? 'request_failed',
    category: safeString(envelope.category) ?? categoryForStatus(status),
    messageKey: safeString(envelope.message_key) ?? safeString(envelope.code) ?? 'request_failed',
    correlationId: safeString(envelope.correlation_id),
    retryable: envelope.retryable === true,
    retryAfter: safeRetryAfter(envelope.retry_after),
  })
}

export async function parseApiErrorResponse(response: Response): Promise<ApiError> {
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // A malformed body is intentionally discarded rather than retained on the error.
  }
  return parseApiError(response.status, body)
}

export function shouldResync(error: ApiError): boolean {
  const normalizedCode = error.code.trim().toLowerCase().replaceAll('-', '_')
  const category = error.category.trim().toUpperCase()
  return error.status === 401
    || error.status === 403
    || category === 'AUTHENTICATION'
    || category === 'AUTHORIZATION'
    || category === 'CONFLICT'
    || category === 'INDETERMINATE'
    || RESYNC_CODES.has(normalizedCode)
}

export function isAuthenticationError(error: unknown): error is ApiError {
  if (!(error instanceof ApiError)) return false
  const category = normalizedCategory(error.category)
  const code = normalizedCode(error.code)
  return error.status === 401
    || category === 'AUTHENTICATION'
    || code === 'authentication_required'
}

export function isCsrfAuthorizationError(error: unknown): error is ApiError {
  if (!(error instanceof ApiError)) return false
  return error.status === 403
    && normalizedCategory(error.category) === 'AUTHORIZATION'
    && normalizedCode(error.code) === 'access_denied'
}

export function invalidResponseError(): ApiError {
  return new ApiError({
    status: 0,
    code: 'invalid_response',
    category: 'INTERNAL',
    messageKey: 'invalid_response',
    retryable: false,
  })
}

function normalizedCode(code: string): string {
  return code.trim().toLowerCase().replaceAll('-', '_')
}

function normalizedCategory(category: string): string {
  return category.trim().toUpperCase()
}

function fallbackError(status: number): ApiError {
  return new ApiError({
    status,
    code: 'request_failed',
    category: categoryForStatus(status),
    messageKey: 'request_failed',
    retryable: status >= 500,
  })
}

function categoryForStatus(status: number): string {
  if (status === 401) return 'AUTHENTICATION'
  if (status === 403) return 'AUTHORIZATION'
  if (status === 409) return 'CONFLICT'
  if (status === 429) return 'RATE_LIMITED'
  if (status >= 500) return 'INTERNAL'
  return 'VALIDATION'
}

function safeString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 && value.length <= 255 ? value : null
}

function safeRetryAfter(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

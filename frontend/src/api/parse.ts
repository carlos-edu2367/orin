/** Small readers for untrusted JSON (W7 API modules): every value is checked, nothing is cast blindly. */

export function rec(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {}
}

export function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

export function optStr(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

export function num(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

export function bool(value: unknown): boolean {
  return value === true
}

export function list<T>(value: unknown, read: (item: unknown) => T | null, max = 1000): T[] {
  if (!Array.isArray(value)) return []
  const items: T[] = []
  for (const item of value.slice(0, max)) {
    const parsed = read(item)
    if (parsed !== null) items.push(parsed)
  }
  return items
}

export function strings(value: unknown, max = 1000): string[] {
  return list(value, (item) => (typeof item === 'string' ? item : null), max)
}

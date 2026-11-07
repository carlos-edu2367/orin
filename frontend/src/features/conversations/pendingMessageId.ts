let nextPendingMessageSeq = 0

export function pendingMessageId(): string {
  nextPendingMessageSeq = nextPendingMessageSeq >= Number.MAX_SAFE_INTEGER ? 1 : nextPendingMessageSeq + 1
  const rawNow = Date.now()
  const timestamp = Number.isFinite(rawNow) && rawNow >= 0 ? Math.trunc(rawNow) : 0
  return `pending-${nextPendingMessageSeq}-${timestamp}`
}

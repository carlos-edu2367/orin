let nextPendingMessageSeq = 0

export function pendingMessageId(): string {
  nextPendingMessageSeq += 1
  return `pending-${nextPendingMessageSeq}-${Date.now()}`
}

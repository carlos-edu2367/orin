import { groupLabel, toolBatchOverview } from './activitySummary'
import type { ActivityGroup, ActivityState, ConversationActivityEvent, ToolKind } from './activityTypes'

export type MessageContentSegment =
  | { kind: 'markdown'; content: string }
  | { kind: 'activity'; group: ActivityGroup }

const FAST_RESIDUAL_MARKER_RE =
  /Tool\s+(?:Use|Result)|orin_tool_call|(?:<|&lt;)\s*\/?\s*tool_call\b|(?:<|&lt;)\s*\/?\s*tool_result\b|(?:<|&lt;)\s*\/?\s*speak\b/i

const SPEAK_TAG_RE = /(?:<|&lt;)\s*\/?\s*speak\b(?:[^>&]|&(?!gt;))*?(?:>|&gt;)/gi

const FENCE_OPEN_RE = /^[ \t]{0,3}(`{3,}|~{3,})[^\r\n]*(?:\r?\n|$)/gm

const TOOL_BLOCK_START_RE =
  /(?:(?:\[|&#91;|&#x5b;)\s*Tool\s+(?:Use|Result)\b|(?:<|&lt;)\s*(?:orin_tool_call|tool_call|orin_tool_result|tool_result)\b)/gi

const GLUED_ROLE_MARKER_RE =
  /^[ \t\r\n]*(?:(?:\[|&#91;|&#x5b;)\s*Assistant\s*(?:\]|&#93;|&#x5d;)[ \t]*:?[ \t\r\n]*|Assistant[ \t]*:?[ \t\r\n]*)/i

type ParsedRawBlock = {
  blockKind: 'use' | 'result'
  id?: string
  name?: string
  status?: string
  body: string
  closed: boolean
  endIndex: number
}

type MergedToolInvocation = {
  id?: string
  name: string
  status?: string
  inputBody: string
  resultBody: string
  closed: boolean
}

/**
 * Fast-path check so normal assistant messages render directly through
 * `<ReactMarkdown>` with zero parsing overhead.
 */
export function hasResidualToolMarkers(content: string): boolean {
  if (!content) return false
  return FAST_RESIDUAL_MARKER_RE.test(content)
}

function stripSpeakTags(text: string): string {
  if (!text || !/(?:<|&lt;)\s*\/?\s*speak\b/i.test(text)) return text
  return text.replace(SPEAK_TAG_RE, '')
}

function decodeHtmlEntities(text: string): string {
  if (!text || !text.includes('&')) return text
  return text
    .replace(/&#91;|&#x5b;/gi, '[')
    .replace(/&#93;|&#x5d;/gi, ']')
    .replace(/&lt;|&#60;|&#x3c;/gi, '<')
    .replace(/&gt;|&#62;|&#x3e;/gi, '>')
    .replace(/&quot;|&#34;|&#x22;/gi, '"')
    .replace(/&apos;|&#39;|&#x27;/gi, "'")
    .replace(/&amp;|&#38;|&#x26;/gi, '&')
}

function parseHeaderAttributes(attrText: string): Record<string, string> {
  const attrs: Record<string, string> = {}
  if (!attrText) return attrs
  const decoded = decodeHtmlEntities(attrText)
  const attrRe = /([a-zA-Z_][\w-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s\]>]+))/g
  let match: RegExpExecArray | null
  while ((match = attrRe.exec(decoded)) !== null) {
    const key = match[1].toLowerCase()
    const value = match[2] ?? match[3] ?? match[4] ?? ''
    attrs[key] = value
  }
  return attrs
}

function consumeGluedRoleMarker(content: string, index: number): number {
  const slice = content.slice(index)
  const match = GLUED_ROLE_MARKER_RE.exec(slice)
  if (!match) return index
  return index + match[0].length
}

function parseSingleToolBlockAt(content: string, startIndex: number): ParsedRawBlock | null {
  const tail = content.slice(startIndex)

  // 1. Bracketed [Tool Use ...] or [Tool Result ...]
  const bracketOpenRe = /^(?:\[|&#91;|&#x5b;)\s*Tool\s+(Use|Result)\b/i
  const bracketMatch = bracketOpenRe.exec(tail)
  if (bracketMatch) {
    const kindWord = bracketMatch[1].toLowerCase()
    const blockKind: 'use' | 'result' = kindWord === 'result' ? 'result' : 'use'
    const afterKeywordIndex = startIndex + bracketMatch[0].length
    const closeTagRe =
      blockKind === 'use'
        ? /(?:\[|&#91;|&#x5b;)\s*\/\s*Tool\s+Use\s*(?:\]|&#93;|&#x5d;)/gi
        : /(?:\[|&#91;|&#x5b;)\s*\/\s*Tool\s+Result\s*(?:\]|&#93;|&#x5d;)/gi
    closeTagRe.lastIndex = afterKeywordIndex
    const closeTagMatch = closeTagRe.exec(content)

    // Find closing bracket of the opening tag header (`]` or `&#93;` or `&#x5d;`)
    const headerCloseRe = /(?:\]|&#93;|&#x5d;)/gi
    headerCloseRe.lastIndex = afterKeywordIndex
    const headerCloseMatch = headerCloseRe.exec(content)

    const limitIndex = closeTagMatch ? closeTagMatch.index : content.length
    let attrs: Record<string, string>
    let bodyStartIndex: number

    if (headerCloseMatch && headerCloseMatch.index < limitIndex) {
      const rawAttrSlice = content.slice(afterKeywordIndex, headerCloseMatch.index)
      attrs = parseHeaderAttributes(rawAttrSlice)
      bodyStartIndex = headerCloseMatch.index + headerCloseMatch[0].length
    } else {
      // Streaming partial header that hasn't even closed `]` yet
      const rawAttrSlice = content.slice(afterKeywordIndex, limitIndex)
      attrs = parseHeaderAttributes(rawAttrSlice)
      bodyStartIndex = limitIndex
    }

    const rawBody = content.slice(bodyStartIndex, limitIndex).trim()
    const closed = Boolean(closeTagMatch)
    let endIndex = closeTagMatch ? closeTagMatch.index + closeTagMatch[0].length : content.length
    if (closed) {
      endIndex = consumeGluedRoleMarker(content, endIndex)
    }

    return {
      blockKind,
      id: attrs.id || attrs.call_id || attrs.tool_use_id,
      name: attrs.name || attrs.tool || attrs.tool_name,
      status: attrs.status || (attrs.is_error === 'true' ? 'failed' : undefined),
      body: decodeHtmlEntities(rawBody),
      closed,
      endIndex,
    }
  }

  // 2. XML-style <orin_tool_call>, <tool_call>, <orin_tool_result>, <tool_result>
  const xmlOpenRe = /^(?:<|&lt;)\s*(orin_tool_call|tool_call|orin_tool_result|tool_result)\b/i
  const xmlMatch = xmlOpenRe.exec(tail)
  if (xmlMatch) {
    const tagName = xmlMatch[1].toLowerCase()
    const blockKind: 'use' | 'result' = tagName.endsWith('result') ? 'result' : 'use'
    const afterTagIndex = startIndex + xmlMatch[0].length
    const closeTagRe = new RegExp(
      String.raw`(?:<|&lt;)\s*\/\s*${tagName}\s*(?:>|&gt;)`,
      'gi',
    )
    closeTagRe.lastIndex = afterTagIndex
    const closeTagMatch = closeTagRe.exec(content)

    const headerCloseRe = /(?:>|&gt;)/gi
    headerCloseRe.lastIndex = afterTagIndex
    const headerCloseMatch = headerCloseRe.exec(content)

    const limitIndex = closeTagMatch ? closeTagMatch.index : content.length
    let attrs: Record<string, string>
    let bodyStartIndex: number

    if (headerCloseMatch && headerCloseMatch.index < limitIndex) {
      const rawAttrSlice = content.slice(afterTagIndex, headerCloseMatch.index)
      attrs = parseHeaderAttributes(rawAttrSlice)
      bodyStartIndex = headerCloseMatch.index + headerCloseMatch[0].length
    } else {
      const rawAttrSlice = content.slice(afterTagIndex, limitIndex)
      attrs = parseHeaderAttributes(rawAttrSlice)
      bodyStartIndex = limitIndex
    }

    const rawBody = content.slice(bodyStartIndex, limitIndex).trim()
    const closed = Boolean(closeTagMatch)
    let endIndex = closeTagMatch ? closeTagMatch.index + closeTagMatch[0].length : content.length
    if (closed) {
      endIndex = consumeGluedRoleMarker(content, endIndex)
    }

    return {
      blockKind,
      id: attrs.id || attrs.call_id || attrs.tool_use_id,
      name: attrs.name || attrs.tool || attrs.tool_name,
      status: attrs.status || (attrs.is_error === 'true' ? 'failed' : undefined),
      body: decodeHtmlEntities(rawBody),
      closed,
      endIndex,
    }
  }

  return null
}

function tryParseJsonRecord(text: string): Record<string, unknown> | null {
  if (!text) return null
  const trimmed = text.trim()
  if (!trimmed.startsWith('{')) return null
  try {
    const parsed: unknown = JSON.parse(trimmed)
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>
    }
  } catch {
    // Partial or non-JSON body during live streaming
  }
  return null
}

function extractRegexField(text: string, fieldNames: string[]): string | undefined {
  if (!text) return undefined
  for (const field of fieldNames) {
    const re = new RegExp(`"${field}"\\s*:\\s*"([^"\\\\]*(?:\\\\.[^"\\\\]*)*)"`, 'i')
    const match = re.exec(text)
    if (match?.[1]) {
      return match[1].replace(/\\"/g, '"').replace(/\\\\/g, '\\')
    }
  }
  return undefined
}

function inferToolKind(toolName: string): ToolKind | undefined {
  const lower = toolName.toLowerCase()
  if (
    lower === 'read' ||
    lower === 'write' ||
    lower === 'edit' ||
    lower === 'glob' ||
    lower === 'grep' ||
    lower === 'listfiles' ||
    lower === 'searchcode' ||
    lower === 'projectmap' ||
    lower === 'viewfile' ||
    lower === 'transcribepdf' ||
    lower === 'notebookedit' ||
    lower.includes('file') ||
    lower.includes('read') ||
    lower.includes('write') ||
    lower.includes('edit') ||
    lower.includes('grep') ||
    lower.includes('glob')
  ) {
    return 'filesystem'
  }
  if (
    lower === 'bash' ||
    lower === 'powershell' ||
    lower === 'run_command' ||
    lower === 'verifyproject' ||
    lower === 'verify_project' ||
    lower === 'monitor' ||
    lower === 'taskoutput' ||
    lower === 'taskstop'
  ) {
    return 'terminal'
  }
  if (lower === 'webfetch' || lower.includes('web')) {
    return 'web'
  }
  if (lower.includes('playwright') || lower.includes('browser') || lower.includes('preview')) {
    return 'browser'
  }
  if (lower === 'remember' || lower === 'recall' || lower.includes('memory')) {
    return 'memory'
  }
  if (lower === 'agent' || lower === 'checksubagent' || lower.includes('subagent')) {
    return 'agent'
  }
  if (lower.includes('image') || lower.includes('audio') || lower.includes('media')) {
    return 'media'
  }
  return undefined
}

function normalizeCanonicalToolName(rawName: string): string {
  const trimmed = rawName.trim()
  if (!trimmed) return 'tool'
  const lower = trimmed.toLowerCase()
  if (lower === 'read' || lower === 'mcp_orin_read') return 'read_file'
  if (lower === 'write' || lower === 'mcp_orin_write') return 'write_file'
  if (lower === 'edit' || lower === 'mcp_orin_edit') return 'edit_file'
  if (lower === 'listfiles' || lower === 'mcp_orin_listfiles') return 'list_files'
  if (lower === 'grep' || lower === 'mcp_orin_grep' || lower === 'glob' || lower === 'mcp_orin_glob') {
    return 'search_files'
  }
  if (lower === 'searchcode' || lower === 'mcp_orin_searchcode') return 'search_code'
  if (lower === 'projectmap' || lower === 'mcp_orin_projectmap') return 'project_map'
  if (lower === 'bash' || lower === 'mcp_orin_bash') return 'bash'
  if (lower === 'powershell' || lower === 'mcp_orin_powershell') return 'powershell'
  return trimmed
}

function extractArgumentsRecord(parsedInput: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!parsedInput) return null
  for (const key of ['arguments', 'input', 'parameters', 'params', 'args']) {
    const candidate = parsedInput[key]
    if (candidate && typeof candidate === 'object' && !Array.isArray(candidate)) {
      return candidate as Record<string, unknown>
    }
    if (typeof candidate === 'string') {
      const nested = tryParseJsonRecord(candidate)
      if (nested) return nested
    }
  }
  return parsedInput
}

function buildHumanSummary(
  displayToolName: string,
  canonicalName: string,
  argsRecord: Record<string, unknown> | null,
  rawInput: string,
  closed: boolean,
): { summary: string; path?: string; command?: string } {
  const filePath =
    (typeof argsRecord?.file_path === 'string' && argsRecord.file_path) ||
    (typeof argsRecord?.path === 'string' && argsRecord.path) ||
    (typeof argsRecord?.notebook_path === 'string' && argsRecord.notebook_path) ||
    extractRegexField(rawInput, ['file_path', 'path', 'notebook_path'])

  const command =
    (typeof argsRecord?.command === 'string' && argsRecord.command) ||
    extractRegexField(rawInput, ['command'])

  const pattern =
    (typeof argsRecord?.pattern === 'string' && argsRecord.pattern) ||
    (typeof argsRecord?.query === 'string' && argsRecord.query) ||
    extractRegexField(rawInput, ['pattern', 'query'])

  if (canonicalName === 'read_file') {
    const target = filePath || displayToolName
    return {
      summary: `${closed ? 'Leu' : 'Lendo'} ${target}`,
      path: filePath,
    }
  }
  if (canonicalName === 'write_file') {
    const target = filePath || displayToolName
    return {
      summary: `${closed ? 'Escreveu' : 'Escrevendo'} ${target}`,
      path: filePath,
    }
  }
  if (canonicalName === 'edit_file') {
    const target = filePath || displayToolName
    return {
      summary: `${closed ? 'Editou' : 'Editando'} ${target}`,
      path: filePath,
    }
  }
  if (canonicalName === 'list_files') {
    const target = filePath || 'arquivos'
    return {
      summary: `${closed ? 'Listou' : 'Listando'} ${target}`,
      path: filePath,
    }
  }
  if (canonicalName === 'search_files' || canonicalName === 'search_code') {
    const target = pattern || filePath || displayToolName
    return {
      summary: `${closed ? 'Buscou' : 'Buscando'} ${target}`,
      path: filePath,
    }
  }
  if (canonicalName === 'bash' || canonicalName === 'powershell' || canonicalName === 'run_command') {
    if (command) {
      return {
        summary: `$ ${command}`,
        command,
      }
    }
  }

  return {
    summary: `${closed ? 'Usou' : 'Executando'} ${displayToolName}`,
    path: filePath,
    command,
  }
}

function resolveInvocationState(inv: MergedToolInvocation): ActivityState {
  if (!inv.closed) return 'working'
  const statusLower = (inv.status ?? '').trim().toLowerCase()
  if (statusLower) {
    if (
      statusLower === 'failed' ||
      statusLower === 'error' ||
      statusLower === 'cancelled' ||
      statusLower === 'canceled' ||
      statusLower === 'denied' ||
      statusLower === 'timed_out' ||
      statusLower === 'timeout'
    ) {
      return 'failed'
    }
    if (
      statusLower === 'succeeded' ||
      statusLower === 'success' ||
      statusLower === 'ok' ||
      statusLower === 'completed'
    ) {
      return 'completed'
    }
  }
  const parsedResult = tryParseJsonRecord(inv.resultBody)
  if (parsedResult) {
    if (parsedResult.is_error === true || parsedResult.ok === false || parsedResult.status === 'failed') {
      return 'failed'
    }
  }
  return 'completed'
}

function mergeRawBlocksIntoInvocations(blocks: ParsedRawBlock[]): MergedToolInvocation[] {
  const invocations: MergedToolInvocation[] = []

  for (const block of blocks) {
    const parsedBody = tryParseJsonRecord(block.body)
    const bodyName =
      (typeof parsedBody?.name === 'string' && parsedBody.name) ||
      (typeof parsedBody?.tool === 'string' && parsedBody.tool) ||
      (typeof parsedBody?.tool_name === 'string' && parsedBody.tool_name) ||
      extractRegexField(block.body, ['name', 'tool', 'tool_name'])

    const bodyId =
      (typeof parsedBody?.id === 'string' && parsedBody.id) ||
      (typeof parsedBody?.call_id === 'string' && parsedBody.call_id) ||
      extractRegexField(block.body, ['id', 'call_id'])

    const resolvedId = block.id || bodyId
    const resolvedName = block.name || bodyName || 'tool'

    if (block.blockKind === 'use') {
      invocations.push({
        id: resolvedId,
        name: resolvedName,
        status: block.status,
        inputBody: block.body,
        resultBody: '',
        closed: block.closed,
      })
      continue
    }

    // block.blockKind === 'result'
    const matchingUse =
      (resolvedId
        ? [...invocations].reverse().find((item) => item.id === resolvedId && !item.resultBody)
        : undefined) ??
      [...invocations].reverse().find((item) => !item.resultBody)

    if (matchingUse) {
      if (matchingUse.name === 'tool' && resolvedName !== 'tool') {
        matchingUse.name = resolvedName
      }
      if (!matchingUse.id && resolvedId) {
        matchingUse.id = resolvedId
      }
      if (block.status) {
        matchingUse.status = block.status
      }
      matchingUse.resultBody = block.body
      matchingUse.closed = matchingUse.closed && block.closed
    } else {
      invocations.push({
        id: resolvedId,
        name: resolvedName,
        status: block.status,
        inputBody: '',
        resultBody: block.body,
        closed: block.closed,
      })
    }
  }

  return invocations
}

function buildActivityGroupFromBlocks(blocks: ParsedRawBlock[], groupIndex: number): ActivityGroup {
  const invocations = mergeRawBlocksIntoInvocations(blocks)
  const groupRunning = invocations.some((inv) => !inv.closed)

  const events: ConversationActivityEvent[] = invocations.map((inv, idx) => {
    const state = resolveInvocationState(inv)
    const displayToolName = inv.name || 'tool'
    const canonicalName = normalizeCanonicalToolName(displayToolName)
    const parsedInput = tryParseJsonRecord(inv.inputBody)
    const argsRecord = extractArgumentsRecord(parsedInput)
    const { summary, path, command } = buildHumanSummary(
      displayToolName,
      canonicalName,
      argsRecord,
      inv.inputBody,
      inv.closed,
    )
    const toolKind = inferToolKind(displayToolName)
    const eventId = `residual-tool-${groupIndex}-${idx}-${inv.id ?? displayToolName}`

    // Keep outputTail unset while the group is running so ActivityCard does not
    // render a live terminal stream outside the collapsed card. When expanded,
    // `content` and `outputTail` are both visible inside ActivityDetailRow.
    const contentText = groupRunning
      ? [inv.inputBody, inv.resultBody].filter(Boolean).join('\n') || undefined
      : inv.inputBody || undefined
    const outputTailText = !groupRunning && inv.resultBody ? inv.resultBody : undefined

    return {
      eventId,
      cursor: eventId,
      type: state === 'working' ? 'tool.started' : 'tool.finished',
      kind: 'tool',
      state,
      agentId: 'assistant',
      tool: canonicalName,
      toolName: displayToolName,
      toolKind,
      invocationId: inv.id ?? eventId,
      status: inv.status ?? (state === 'failed' ? 'failed' : state === 'completed' ? 'succeeded' : 'running'),
      label: displayToolName,
      summary,
      content: contentText,
      outputTail: outputTailText,
      path,
      filePath: path,
      command,
      payload: argsRecord ? { arguments: argsRecord } : undefined,
    }
  })

  const hasWorking = events.some((event) => event.state === 'working' || event.state === 'waiting_tool')
  const hasFailed = events.some((event) => event.state === 'failed')
  const groupState: ActivityState = hasWorking ? 'working' : hasFailed ? 'failed' : 'completed'

  const draftGroup: ActivityGroup = {
    id: `residual-group-${groupIndex}-${events[0]?.eventId ?? '0'}`,
    kind: 'tool',
    state: groupState,
    label: '',
    count: events.length,
    events,
    agentId: 'assistant',
    failed: hasFailed,
  }

  draftGroup.label =
    groupState === 'working'
      ? groupLabel(draftGroup)
      : events.length > 1
        ? toolBatchOverview(draftGroup)
        : groupLabel(draftGroup)

  return draftGroup
}

function findFenceEnd(content: string, fenceStartEndIndex: number, fenceMarker: string): number {
  const fenceChar = fenceMarker[0]
  const minLen = fenceMarker.length
  const closeFenceRe = new RegExp(
    `^[ \\t]{0,3}${fenceChar === '~' ? '~' : '`'}{${minLen},}[ \\t]*(?:\\r?\\n|$)`,
    'gm',
  )
  closeFenceRe.lastIndex = fenceStartEndIndex
  const closeMatch = closeFenceRe.exec(content)
  if (!closeMatch) return content.length
  return closeMatch.index + closeMatch[0].length
}

/**
 * Splits assistant message `content` into ordered Markdown and ActivityGroup
 * segments, collapsing leaked raw `[Tool Use ...]` / `[Tool Result ...]`,
 * `&#91;Tool Use ...]`, `<orin_tool_call>`, and `<tool_call>` blocks into
 * interactive `ActivityGroup` batches while preserving fenced code blocks.
 */
export function parseResidualToolBlocks(content: string): MessageContentSegment[] {
  if (!content) return []
  if (!hasResidualToolMarkers(content)) {
    return [{ kind: 'markdown', content }]
  }

  const segments: MessageContentSegment[] = []
  let markdownBuffer = ''
  let pos = 0
  let groupCounter = 0

  const flushMarkdown = () => {
    if (!markdownBuffer) return
    if (markdownBuffer.trim().length > 0) {
      segments.push({ kind: 'markdown', content: markdownBuffer })
    }
    markdownBuffer = ''
  }

  while (pos < content.length) {
    FENCE_OPEN_RE.lastIndex = pos
    const fenceMatch = FENCE_OPEN_RE.exec(content)

    TOOL_BLOCK_START_RE.lastIndex = pos
    const toolMatch = TOOL_BLOCK_START_RE.exec(content)

    if (fenceMatch && (!toolMatch || fenceMatch.index < toolMatch.index)) {
      markdownBuffer += stripSpeakTags(content.slice(pos, fenceMatch.index))
      const fenceOpenEnd = fenceMatch.index + fenceMatch[0].length
      const fenceEnd = findFenceEnd(content, fenceOpenEnd, fenceMatch[1])
      markdownBuffer += content.slice(fenceMatch.index, fenceEnd)
      pos = fenceEnd
      continue
    }

    if (!toolMatch) {
      markdownBuffer += stripSpeakTags(content.slice(pos))
      break
    }

    // A tool block starts at toolMatch.index
    if (toolMatch.index > pos) {
      markdownBuffer += stripSpeakTags(content.slice(pos, toolMatch.index))
    }

    const batchBlocks: ParsedRawBlock[] = []
    let cursor = toolMatch.index

    while (cursor < content.length) {
      const parsedBlock = parseSingleToolBlockAt(content, cursor)
      if (!parsedBlock) break
      batchBlocks.push(parsedBlock)
      cursor = parsedBlock.endIndex
      if (!parsedBlock.closed) break

      // Check if another tool block immediately follows (ignoring whitespace)
      const wsMatch = /^[ \t\r\n]*/.exec(content.slice(cursor))
      const nextNonWs = cursor + (wsMatch ? wsMatch[0].length : 0)
      const nextBlock = parseSingleToolBlockAt(content, nextNonWs)
      if (!nextBlock) break
      cursor = nextNonWs
    }

    if (batchBlocks.length > 0) {
      flushMarkdown()
      groupCounter += 1
      const group = buildActivityGroupFromBlocks(batchBlocks, groupCounter)
      segments.push({ kind: 'activity', group })
      pos = cursor
    } else {
      // Fallback safety: advance one character so we never loop infinitely
      markdownBuffer += content[toolMatch.index]
      pos = toolMatch.index + 1
    }
  }

  flushMarkdown()
  return segments
}

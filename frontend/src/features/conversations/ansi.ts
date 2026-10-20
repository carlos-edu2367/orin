/**
 * The status line's ANSI colours (W7). A status line command prints text with SGR escapes, as in a terminal. This
 * reads the escapes into plain text segments with a few styles; it never produces HTML, and every escape it does not
 * know (cursor moves, titles, links) is dropped, so the command's output can only ever be text.
 */

export type AnsiSegment = {
  text: string
  /** 0–15: the 16 terminal colours (themed in CSS); an `rgb()` string for 256-colour and true-colour codes. */
  color?: number | string
  bold?: boolean
  dim?: boolean
  italic?: boolean
  underline?: boolean
}

type Style = Omit<AnsiSegment, 'text'>

// eslint-disable-next-line no-control-regex
const ESCAPES = /\x1b\[([0-9;:]*)([A-Za-z])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]/g

const CUBE = [0, 95, 135, 175, 215, 255]

function color256(index: number): number | string | undefined {
  if (!Number.isInteger(index) || index < 0 || index > 255) return undefined
  if (index < 16) return index
  if (index >= 232) {
    const level = 8 + (index - 232) * 10
    return `rgb(${level}, ${level}, ${level})`
  }
  const cube = index - 16
  return `rgb(${CUBE[Math.floor(cube / 36)]}, ${CUBE[Math.floor(cube / 6) % 6]}, ${CUBE[cube % 6]})`
}

function applySgr(style: Style, params: string): Style {
  const codes = params === '' ? [0] : params.split(/[;:]/).map((part) => (part === '' ? 0 : Number(part)))
  let next: Style = { ...style }
  for (let index = 0; index < codes.length; index += 1) {
    const code = codes[index]
    if (code === 0) next = {}
    else if (code === 1) next.bold = true
    else if (code === 2) next.dim = true
    else if (code === 3) next.italic = true
    else if (code === 4) next.underline = true
    else if (code === 22) { delete next.bold; delete next.dim }
    else if (code === 23) delete next.italic
    else if (code === 24) delete next.underline
    else if (code >= 30 && code <= 37) next.color = code - 30
    else if (code >= 90 && code <= 97) next.color = code - 90 + 8
    else if (code === 39) delete next.color
    else if (code === 38 && codes[index + 1] === 5) {
      const value = color256(codes[index + 2])
      if (value === undefined) delete next.color
      else next.color = value
      index += 2
    } else if (code === 38 && codes[index + 1] === 2) {
      const [red, green, blue] = codes.slice(index + 2, index + 5)
      const valid = [red, green, blue].every((part) => Number.isInteger(part) && part >= 0 && part <= 255)
      if (valid) next.color = `rgb(${red}, ${green}, ${blue})`
      index += 4
    } else if (code === 48) {
      // Backgrounds are ignored (they would fight the theme); skip their arguments.
      index += codes[index + 1] === 5 ? 2 : codes[index + 1] === 2 ? 4 : 0
    }
  }
  return next
}

/** The segments of one line of a status line's output. Control characters other than the escapes are removed. */
export function parseAnsi(line: string): AnsiSegment[] {
  const segments: AnsiSegment[] = []
  let style: Style = {}
  let last = 0
  const push = (text: string) => {
    // eslint-disable-next-line no-control-regex
    const clean = text.replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '')
    if (!clean) return
    const previous = segments[segments.length - 1]
    const same = previous && previous.color === style.color && previous.bold === style.bold && previous.dim === style.dim
      && previous.italic === style.italic && previous.underline === style.underline
    if (same) previous.text += clean
    else segments.push({ text: clean, ...style })
  }
  for (const match of line.matchAll(ESCAPES)) {
    push(line.slice(last, match.index))
    last = (match.index ?? 0) + match[0].length
    if (match[2] === 'm') style = applySgr(style, match[1] ?? '')
  }
  push(line.slice(last))
  return segments
}

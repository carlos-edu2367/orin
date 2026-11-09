import { SCENE_HEIGHT } from './sceneLayout'

export interface SceneLabelBox {
  id: string
  /** Desired left coordinate in logical pixels */
  x: number
  /** Desired top coordinate in logical pixels */
  y: number
  /** Width in logical pixels */
  width: number
  /** Height in logical pixels */
  height: number
  /** Whether this box belongs to the selected agent (always shown) */
  priority?: boolean
}

export interface PlacedSceneLabelBox {
  id: string
  x: number
  y: number
  width: number
  height: number
  priority: boolean
  visible: boolean
}

export interface LayoutLabelsOptions {
  /** Minimum top coordinate in logical pixels (default 1) */
  minY?: number
  /** Maximum bottom (y + height) coordinate in logical pixels (default SCENE_HEIGHT - 1) */
  maxY?: number
  /** Vertical step in logical pixels per attempt (defaults to box.height + 1) */
  stepY?: number
  /** Maximum vertical shift attempts in each direction before overflowing (default 4) */
  maxAttempts?: number
  /** Primary shift direction when resolving a collision (default 'down') */
  direction?: 'down' | 'up'
  /** Hide non-priority boxes when no collision-free slot fits (default true) */
  hideOnOverflow?: boolean
}

const sortIndicesScratch: number[] = []
const placedIndicesScratch: number[] = []

export function boxesIntersect(
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number }
): boolean {
  return (
    a.x < b.x + b.width &&
    a.x + a.width > b.x &&
    a.y < b.y + b.height &&
    a.y + a.height > b.y
  )
}

function clampY(y: number, height: number, minY: number, maxY: number): number {
  const maxTop = Math.max(minY, maxY - height)
  return Math.max(minY, Math.min(maxTop, y))
}

/**
 * Pure anti-collision pass for character name tags and speech/thought bubbles.
 * Sorts boxes by `y` ascending, shifts colliding boxes vertically (trying primary
 * direction first, then opposite), and hides non-selected labels if no slot fits.
 * Selected agent labels (`priority: true`) are always kept visible.
 *
 * Accepts an optional `out` array to reuse allocations across animation frames.
 */
export function layoutLabels(
  boxes: readonly SceneLabelBox[],
  options?: LayoutLabelsOptions,
  out?: PlacedSceneLabelBox[]
): PlacedSceneLabelBox[] {
  const minY = options?.minY ?? 1
  const maxY = options?.maxY ?? SCENE_HEIGHT - 1
  const maxAttempts = options?.maxAttempts ?? 4
  const direction = options?.direction ?? 'down'
  const hideOnOverflow = options?.hideOnOverflow ?? true

  const target = out ?? []
  while (target.length < boxes.length) {
    target.push({
      id: '',
      x: 0,
      y: 0,
      width: 0,
      height: 0,
      priority: false,
      visible: true,
    })
  }
  target.length = boxes.length

  const count = boxes.length
  if (count === 0) {
    return target
  }

  sortIndicesScratch.length = count
  for (let i = 0; i < count; i += 1) {
    sortIndicesScratch[i] = i
  }

  // Sort by y ascending; break ties with priority (selected first), then input order
  sortIndicesScratch.sort((ia, ib) => {
    const a = boxes[ia]!
    const b = boxes[ib]!
    if (a.y !== b.y) return a.y - b.y
    const ap = a.priority ? 1 : 0
    const bp = b.priority ? 1 : 0
    if (ap !== bp) return bp - ap
    return ia - ib
  })

  placedIndicesScratch.length = 0

  for (let orderIdx = 0; orderIdx < count; orderIdx += 1) {
    const src = boxes[sortIndicesScratch[orderIdx]!]!
    const dest = target[orderIdx]!
    const priority = Boolean(src.priority)
    const step = options?.stepY ?? src.height + 1
    const baseY = clampY(Math.round(src.y), src.height, minY, maxY)

    dest.id = src.id
    dest.x = Math.round(src.x)
    dest.y = baseY
    dest.width = src.width
    dest.height = src.height
    dest.priority = priority
    dest.visible = true

    const collidesAt = (candY: number): boolean => {
      for (let p = 0; p < placedIndicesScratch.length; p += 1) {
        const other = target[placedIndicesScratch[p]!]!
        if (!other.visible) continue
        if (
          dest.x < other.x + other.width &&
          dest.x + dest.width > other.x &&
          candY < other.y + other.height &&
          candY + dest.height > other.y
        ) {
          return true
        }
      }
      return false
    }

    const inBounds = (candY: number): boolean =>
      candY >= minY && candY + dest.height <= maxY

    if (!collidesAt(baseY)) {
      placedIndicesScratch.push(orderIdx)
      continue
    }

    let resolvedY: number | null = null
    const primarySign = direction === 'up' ? -1 : 1
    const secondarySign = -primarySign

    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      const delta = attempt * step
      const candPrimary = baseY + primarySign * delta
      if (inBounds(candPrimary) && !collidesAt(candPrimary)) {
        resolvedY = candPrimary
        break
      }

      const candSecondary = baseY + secondarySign * delta
      if (inBounds(candSecondary) && !collidesAt(candSecondary)) {
        resolvedY = candSecondary
        break
      }
    }

    if (resolvedY !== null) {
      dest.y = resolvedY
      dest.visible = true
      placedIndicesScratch.push(orderIdx)
    } else if (priority) {
      // Selected agent label is always shown; hide any non-priority label it displaces
      dest.y = baseY
      dest.visible = true
      if (hideOnOverflow) {
        for (let p = 0; p < placedIndicesScratch.length; p += 1) {
          const other = target[placedIndicesScratch[p]!]!
          if (!other.visible || other.priority) continue
          if (boxesIntersect(dest, other)) {
            other.visible = false
          }
        }
      }
      placedIndicesScratch.push(orderIdx)
    } else if (hideOnOverflow) {
      dest.y = baseY
      dest.visible = false
    } else {
      // For bubbles (hideOnOverflow = false), stagger vertically within bounds so they don't stack identically
      const fallbackShift = ((orderIdx % 3) + 1) * Math.max(4, Math.floor(step / 2))
      const fallbackY = clampY(
        baseY + primarySign * fallbackShift,
        dest.height,
        minY,
        maxY
      )
      dest.y = fallbackY !== baseY
        ? fallbackY
        : clampY(baseY + secondarySign * fallbackShift, dest.height, minY, maxY)
      dest.visible = true
      placedIndicesScratch.push(orderIdx)
    }
  }

  return target
}

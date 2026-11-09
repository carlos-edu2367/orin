import { describe, expect, it } from 'vitest'
import { boxesIntersect, layoutLabels, type PlacedSceneLabelBox } from '../sceneLabels'

describe('sceneLabels', () => {
  it('keeps non-overlapping labels at their original coordinates', () => {
    const placed = layoutLabels([
      { id: 'a', x: 20, y: 60, width: 28, height: 8 },
      { id: 'b', x: 120, y: 60, width: 28, height: 8 },
    ])

    expect(placed).toHaveLength(2)
    expect(placed[0]).toMatchObject({ id: 'a', x: 20, y: 60, visible: true })
    expect(placed[1]).toMatchObject({ id: 'b', x: 120, y: 60, visible: true })
  })

  it('sorts by y and shifts colliding labels vertically so they do not intersect', () => {
    const placed = layoutLabels([
      { id: 'lower', x: 50, y: 102, width: 32, height: 8 },
      { id: 'upper', x: 52, y: 100, width: 32, height: 8 },
    ])

    expect(placed.map((p) => p.id)).toEqual(['upper', 'lower'])
    const upper = placed[0]!
    const lower = placed[1]!
    expect(upper.visible).toBe(true)
    expect(lower.visible).toBe(true)
    expect(upper.y).toBe(100)
    expect(lower.y).not.toBe(102)
    expect(boxesIntersect(upper, lower)).toBe(false)
  })

  it('shifts upward when a downward shift would exceed maxY', () => {
    const placed = layoutLabels(
      [
        { id: 'first', x: 80, y: 182, width: 30, height: 8 },
        { id: 'second', x: 82, y: 182, width: 30, height: 8 },
      ],
      { minY: 1, maxY: 191 }
    )

    expect(placed[0]!.visible).toBe(true)
    expect(placed[1]!.visible).toBe(true)
    expect(placed[0]!.y).toBe(182)
    expect(placed[1]!.y).toBeLessThan(182)
    expect(boxesIntersect(placed[0]!, placed[1]!)).toBe(false)
  })

  it('hides unselected labels when maxAttempts is exhausted, but always keeps selected label visible', () => {
    const placed = layoutLabels(
      [
        { id: 'unselected-1', x: 60, y: 100, width: 36, height: 8, priority: false },
        { id: 'unselected-2', x: 60, y: 101, width: 36, height: 8, priority: false },
        { id: 'selected', x: 60, y: 102, width: 36, height: 8, priority: true },
      ],
      { maxAttempts: 0 }
    )

    const sel = placed.find((p) => p.id === 'selected')!
    const u1 = placed.find((p) => p.id === 'unselected-1')!
    const u2 = placed.find((p) => p.id === 'unselected-2')!

    expect(sel.visible).toBe(true)
    expect(u1.visible).toBe(false)
    expect(u2.visible).toBe(false)
  })

  it('displaces bubbles vertically without hiding when hideOnOverflow is false', () => {
    const placed = layoutLabels(
      [
        { id: 'b1', x: 90, y: 40, width: 70, height: 13 },
        { id: 'b2', x: 94, y: 40, width: 70, height: 13 },
      ],
      { direction: 'up', stepY: 15, hideOnOverflow: false }
    )

    expect(placed[0]!.visible).toBe(true)
    expect(placed[1]!.visible).toBe(true)
    expect(placed[0]!.y).toBe(40)
    expect(placed[1]!.y).toBe(25)
    expect(boxesIntersect(placed[0]!, placed[1]!)).toBe(false)
  })

  it('reuses the provided output array in place without losing correctness', () => {
    const reusable: PlacedSceneLabelBox[] = []
    const firstRun = layoutLabels(
      [
        { id: '1', x: 40, y: 80, width: 24, height: 8 },
        { id: '2', x: 40, y: 80, width: 24, height: 8 },
      ],
      undefined,
      reusable
    )
    expect(firstRun).toBe(reusable)
    expect(reusable).toHaveLength(2)
    const ref0 = reusable[0]

    const secondRun = layoutLabels(
      [{ id: 'only', x: 10, y: 50, width: 20, height: 8 }],
      undefined,
      reusable
    )
    expect(secondRun).toBe(reusable)
    expect(reusable).toHaveLength(1)
    expect(reusable[0]).toBe(ref0)
    expect(reusable[0]!.id).toBe('only')
  })
})

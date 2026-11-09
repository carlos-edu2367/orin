import { describe, expect, it } from 'vitest'
import {
  DOOR,
  SCENE_HEIGHT,
  SCENE_TILE,
  SCENE_WIDTH,
  STATION_ANCHORS,
  STATION_PROPS,
  stationSlot,
} from '../sceneLayout'
import { STATION_SHORT_LABELS } from '../sceneStations'
import type { SceneStationId } from '../sceneTypes'

const ALL_STATIONS: readonly SceneStationId[] = [
  'terminal',
  'files',
  'writing',
  'browser',
  'planning',
  'meeting',
  'extensions',
  'generic',
  'lounge',
]

describe('sceneLayout', () => {
  it('defines a 320x192 logical office on a 16px tile grid', () => {
    expect(SCENE_TILE).toBe(16)
    expect(SCENE_WIDTH).toBe(320)
    expect(SCENE_HEIGHT).toBe(192)
    expect(DOOR.x).toBeGreaterThan(0)
    expect(DOOR.x).toBeLessThan(SCENE_WIDTH)
    expect(DOOR.y).toBeGreaterThan(0)
    expect(DOOR.y).toBeLessThan(SCENE_HEIGHT)
  })

  it('keeps all station anchors and slots 0..11 inside 320x192 without overlapping at the same station', () => {
    for (const station of ALL_STATIONS) {
      const anchor = STATION_ANCHORS[station]
      expect(anchor.x).toBeGreaterThanOrEqual(0)
      expect(anchor.x).toBeLessThanOrEqual(SCENE_WIDTH)
      expect(anchor.y).toBeGreaterThanOrEqual(0)
      expect(anchor.y).toBeLessThanOrEqual(SCENE_HEIGHT)

      const seenCoords = new Set<string>()
      for (let i = 0; i < 12; i += 1) {
        const slot = stationSlot(station, i)
        expect(slot.x).toBeGreaterThan(0)
        expect(slot.x).toBeLessThan(SCENE_WIDTH)
        expect(slot.y).toBeGreaterThan(0)
        expect(slot.y).toBeLessThan(SCENE_HEIGHT)

        const key = `${slot.x},${slot.y}`
        expect(seenCoords.has(key)).toBe(false)
        seenCoords.add(key)
      }
    }
  })

  it('places all station furniture props within the logical canvas bounds', () => {
    expect(STATION_PROPS.length).toBeGreaterThanOrEqual(ALL_STATIONS.length)
    for (const prop of STATION_PROPS) {
      expect(prop.x).toBeGreaterThanOrEqual(0)
      expect(prop.y).toBeGreaterThanOrEqual(0)
      expect(prop.x + prop.width).toBeLessThanOrEqual(SCENE_WIDTH)
      expect(prop.y + prop.height).toBeLessThanOrEqual(SCENE_HEIGHT)
      if (prop.station) {
        expect(prop.label).toBe(STATION_SHORT_LABELS[prop.station])
      } else if (prop.propId === 'door') {
        expect(prop.label).toBe('Porta')
      } else {
        expect(prop.label).toBeUndefined()
      }
    }
  })
})

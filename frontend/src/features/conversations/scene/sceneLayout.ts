import { STATION_LABELS, STATION_SHORT_LABELS } from './sceneStations'
import type { SceneStationId } from './sceneTypes'

export const SCENE_TILE = 16
export const SCENE_COLS = 20
export const SCENE_ROWS = 12
export const SCENE_WIDTH = SCENE_COLS * SCENE_TILE // 320
export const SCENE_HEIGHT = SCENE_ROWS * SCENE_TILE // 192

export interface ScenePoint {
  readonly x: number
  readonly y: number
}

export interface StationAnchor extends ScenePoint {
  readonly id: SceneStationId
  readonly label: string
}

export interface StationPropPlacement {
  readonly id: string
  readonly propId: string
  readonly station?: SceneStationId
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
  readonly label?: string
}

/**
 * Size of the office background art (`assets/office.webp` source). Seat and screen
 * coordinates below are measured on that image and converted to logical pixels.
 */
export const OFFICE_SOURCE_W = 1619
export const OFFICE_SOURCE_H = 971

const toSceneX = (sourceX: number): number => (sourceX * SCENE_WIDTH) / OFFICE_SOURCE_W
const toSceneY = (sourceY: number): number => (sourceY * SCENE_HEIGHT) / OFFICE_SOURCE_H

/** Door position (feet anchor, on the door mat) where subagents enter and exit the office. */
export const DOOR: ScenePoint = {
  x: 34,
  y: 184,
}

/**
 * Standing feet anchor for each station in logical pixels (320x192), in front of the
 * matching furniture of the office art. Desk stations also have a chair (`SCENE_SEATS`):
 * their first slot is the chair and this anchor is where extra agents stand.
 */
export const STATION_ANCHORS: Readonly<Record<SceneStationId, StationAnchor>> = {
  planning: { id: 'planning', x: 77, y: 76, label: STATION_LABELS.planning },
  files: { id: 'files', x: 138, y: 78, label: STATION_LABELS.files },
  browser: { id: 'browser', x: 186, y: 76, label: STATION_LABELS.browser },
  extensions: { id: 'extensions', x: 292, y: 79, label: STATION_LABELS.extensions },
  terminal: { id: 'terminal', x: 102, y: 132, label: STATION_LABELS.terminal },
  meeting: { id: 'meeting', x: 168, y: 137, label: STATION_LABELS.meeting },
  writing: { id: 'writing', x: 244, y: 132, label: STATION_LABELS.writing },
  generic: { id: 'generic', x: 170, y: 184, label: STATION_LABELS.generic },
  lounge: { id: 'lounge', x: 232, y: 188, label: STATION_LABELS.lounge },
}

export interface SceneSeat {
  readonly station: SceneStationId
  /** Feet anchor of the seated agent: chair centre, bottom of the backrest. */
  readonly x: number
  readonly y: number
  /** Top of the backrest: the seated agent's shoulders rest just below it. */
  readonly backTop: number
  /** Backrest outline, drawn again over the seated agent so the chair hides the torso. */
  readonly backrest: readonly ScenePoint[]
}

function seat(
  station: SceneStationId,
  centerX: number,
  top: number,
  bottom: number,
  backrest: ReadonlyArray<readonly [number, number]>
): SceneSeat {
  return {
    station,
    x: toSceneX(centerX),
    y: toSceneY(bottom),
    backTop: toSceneY(top),
    backrest: backrest.map(([x, y]) => ({ x: toSceneX(x), y: toSceneY(y) })),
  }
}

/** Desk chairs of the office art (measured on the source image). */
export const SCENE_SEATS: Readonly<Partial<Record<SceneStationId, SceneSeat>>> = {
  terminal: seat('terminal', 322, 540, 615, [
    [282, 548], [290, 540], [354, 540], [361, 548], [362, 606], [355, 615], [290, 615], [282, 606],
  ]),
  browser: seat('browser', 1066, 258, 332, [
    [1031, 266], [1038, 258], [1096, 258], [1103, 266], [1104, 322], [1097, 332], [1037, 332], [1030, 322],
  ]),
  writing: seat('writing', 1380, 538, 616, [
    [1341, 547], [1348, 538], [1412, 538], [1419, 547], [1420, 606], [1412, 616], [1348, 616], [1340, 606],
  ]),
  generic: seat('generic', 606, 825, 902, [
    [569, 834], [576, 825], [636, 825], [643, 834], [644, 892], [636, 902], [576, 902], [568, 892],
  ]),
}

/** Sofa cushion where the first resting agent (normally the Orin) lies down. */
export const SOFA_SPOT: ScenePoint = { x: toSceneX(1085), y: toSceneY(828) }

/** Where the other resting agents put their lounge chairs: beside the sofa first, then in front. */
const LOUNGE_SPOTS: readonly ScenePoint[] = [
  { x: 180, y: 186 },
  { x: 270, y: 186 },
  { x: 246, y: 190 },
  { x: 298, y: 188 },
  { x: 152, y: 188 },
  { x: 124, y: 190 },
]

/** Screens of the office art that glow while the team works (logical px). */
export const SCENE_SCREENS = {
  terminal: {
    x: toSceneX(257),
    y: toSceneY(394),
    width: toSceneX(140),
    height: toSceneY(84),
    cursor: { x: toSceneX(291), y: toSceneY(458), width: toSceneX(8), height: toSceneY(10) },
  },
  browser: { x: toSceneX(995), y: toSceneY(132), width: toSceneX(143), height: toSceneY(80) },
} as const

/** Returns the chair of `station` when (x, y) is its seat. */
export function seatAt(station: SceneStationId, x: number, y: number): SceneSeat | null {
  const candidate = SCENE_SEATS[station]
  if (!candidate) return null
  return Math.abs(candidate.x - x) < 0.75 && Math.abs(candidate.y - y) < 0.75 ? candidate : null
}

/** Height of a standing character in logical px (used for hit tests and effects). */
export const FIGURE_HEIGHT = 44
/** Height of the Orin mascot in logical px. */
export const ORIN_FIGURE_HEIGHT = 22
/** Half of the clickable width of a character in logical px. */
export const FIGURE_HALF_WIDTH = 11

export function figureHeight(isMain: boolean): number {
  return isMain ? ORIN_FIGURE_HEIGHT : FIGURE_HEIGHT
}

const LEFT_SIDE_COL_OFFSETS = [0, 20, -20, 40, -40, 60] as const
// Right-side stations sit near the wall: extra agents spread towards the centre.
const RIGHT_SIDE_COL_OFFSETS = [0, -20, -40, 12, -60, -80] as const
const CENTER_COL_OFFSETS = [0, -31, 31, -15, 15, 46] as const

/**
 * Computes a non-overlapping slot (character feet in logical px) for the `index`-th
 * agent at `station`. At a desk station the first agent sits in the chair; resting
 * starts on the sofa. Supports >= 12 simultaneous agents per station by forming
 * rows that always stay within the 320x192 logical canvas.
 */
export function stationSlot(station: SceneStationId, index: number): { x: number; y: number } {
  const safeIndex = Math.max(0, Math.floor(index))
  const chair = SCENE_SEATS[station]
  if (chair && safeIndex === 0) return { x: chair.x, y: chair.y }
  if (station === 'lounge') {
    if (safeIndex === 0) return { x: SOFA_SPOT.x, y: SOFA_SPOT.y }
    const spot = LOUNGE_SPOTS[safeIndex - 1]
    if (spot) return { x: spot.x, y: spot.y }
    return standingSlot(station, safeIndex - 1)
  }
  return standingSlot(station, chair ? safeIndex - 1 : safeIndex)
}

function standingSlot(station: SceneStationId, safeIndex: number): { x: number; y: number } {
  const anchor = STATION_ANCHORS[station]
  const colsPerRow = 6
  const col = safeIndex % colsPerRow
  const row = Math.floor(safeIndex / colsPerRow)

  const colOffsets =
    anchor.x < 120
      ? LEFT_SIDE_COL_OFFSETS
      : anchor.x > 200
        ? RIGHT_SIDE_COL_OFFSETS
        : CENTER_COL_OFFSETS

  const dx = colOffsets[col] ?? 0
  // Shift extra rows slightly stagger-wise so row 2+ never collides with row 0..1
  const staggerX = row >= 2 ? (row % 2 === 0 ? 6 : -6) : 0
  const rowDirection = anchor.y > 145 ? -1 : 1
  const dy = row * 11 * rowDirection

  const rawX = anchor.x + dx + staggerX
  const rawY = anchor.y + dy

  const x = Math.max(12, Math.min(SCENE_WIDTH - 12, rawX))
  const y = Math.max(28, Math.min(SCENE_HEIGHT - 6, rawY))

  return { x, y }
}

/**
 * Furniture of the hand-drawn fallback office, used only while the office art has not
 * loaded (or cannot load). Coordinates `(x, y)` represent the top-left corner of the prop sprite.
 */
export const STATION_PROPS: readonly StationPropPlacement[] = [
  {
    id: 'prop-door',
    propId: 'door',
    x: 8,
    y: 132,
    width: 16,
    height: 32,
    label: 'Porta',
  },
  {
    id: 'prop-planning',
    propId: 'planning',
    station: 'planning',
    x: 36,
    y: 20,
    width: 32,
    height: 16,
    label: STATION_SHORT_LABELS.planning,
  },
  {
    id: 'prop-files',
    propId: 'files',
    station: 'files',
    x: 108,
    y: 18,
    width: 32,
    height: 24,
    label: STATION_SHORT_LABELS.files,
  },
  {
    id: 'prop-browser',
    propId: 'browser',
    station: 'browser',
    x: 180,
    y: 22,
    width: 32,
    height: 20,
    label: STATION_SHORT_LABELS.browser,
  },
  {
    id: 'prop-extensions',
    propId: 'extensions',
    station: 'extensions',
    x: 252,
    y: 18,
    width: 32,
    height: 24,
    label: STATION_SHORT_LABELS.extensions,
  },
  {
    id: 'prop-terminal',
    propId: 'terminal',
    station: 'terminal',
    x: 40,
    y: 82,
    width: 32,
    height: 20,
    label: STATION_SHORT_LABELS.terminal,
  },
  {
    id: 'prop-meeting',
    propId: 'meeting',
    station: 'meeting',
    x: 136,
    y: 82,
    width: 48,
    height: 20,
    label: STATION_SHORT_LABELS.meeting,
  },
  {
    id: 'prop-writing',
    propId: 'writing',
    station: 'writing',
    x: 248,
    y: 82,
    width: 32,
    height: 20,
    label: STATION_SHORT_LABELS.writing,
  },
  {
    id: 'prop-generic',
    propId: 'generic',
    station: 'generic',
    x: 88,
    y: 136,
    width: 32,
    height: 18,
    label: STATION_SHORT_LABELS.generic,
  },
  {
    id: 'prop-lounge',
    propId: 'lounge',
    station: 'lounge',
    x: 204,
    y: 134,
    width: 48,
    height: 20,
    label: STATION_SHORT_LABELS.lounge,
  },
  {
    id: 'prop-plant-left',
    propId: 'plant',
    x: 12,
    y: 26,
    width: 16,
    height: 16,
  },
  {
    id: 'prop-plant-right',
    propId: 'plant',
    x: 294,
    y: 136,
    width: 16,
    height: 16,
  },
]

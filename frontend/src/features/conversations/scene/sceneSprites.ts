import type { ScenePose } from './sceneTypes'

export type PixelMatrix = readonly string[]

export const CHARACTER_W = 16
export const CHARACTER_H = 24

/** People drawn in the office art sprite sheet (`assets/characters.webp`, rows p1..p4). */
export const ART_PERSON_LOOKS = 4

/**
 * Canonical pixel-art palette derived from Orin's design tokens.
 * Includes semantic names and single-character matrix keys for direct lookup.
 */
export const SCENE_PALETTE = {
  // Orin purples
  orinBase: '#B4A4EF',
  orinShadow: '#8F7AE0',
  orinLight: '#DDD5FA',
  outline: '#282333',

  // Dark surfaces (--orin-ink & raised surfaces)
  ink: '#101014',
  surface1: '#141419',
  surface2: '#1A1A21',
  surface3: '#1E1E26',
  wallBase: '#22222C',
  wallTrim: '#2E2E3C',

  // Wood & metal neutrals
  woodLight: '#B07D52',
  woodBase: '#8B5E3C',
  woodDark: '#5E3E26',
  metalLight: '#A5AEBE',
  metalBase: '#7A8299',
  metalDark: '#4B5263',

  // Semantic & prop accents
  success: '#4ADE80',
  successDark: '#16A34A',
  danger: '#F87171',
  warn: '#FB923C',
  gold: '#FACC15',
  info: '#60A5FA',
  paper: '#F4F3F8',
  paperShadow: '#CBD5E1',

  // Single-character default matrix keys
  O: '#282333', // Outline / dark features
  P: '#B4A4EF', // Orin main purple
  L: '#DDD5FA', // Orin highlight
  D: '#8F7AE0', // Orin shadow
  S: '#F5CBA7', // Skin tone
  E: '#E0AC82', // Skin shadow
  H: '#FACC15', // Hair color
  C: '#60A5FA', // Primary outfit color
  B: '#3B82F6', // Outfit shadow / secondary
  A: '#F472B6', // Accessory accent
  W: '#F4F3F8', // White / paper
  K: '#CBD5E1', // Silver / light gray
  G: '#4ADE80', // Green (terminal / check)
  J: '#16A34A', // Dark green
  R: '#F87171', // Red (error / alert)
  Y: '#FB923C', // Orange / spark
  M: '#8B5E3C', // Wood mid
  N: '#B07D52', // Wood light
  U: '#5E3E26', // Wood dark
  T: '#7A8299', // Metal mid
  V: '#4B5263', // Metal dark
  Q: '#38BDF8', // Screen cyan
} as const

export type AvatarAccessory = 'none' | 'glasses' | 'headphones' | 'cap' | 'tie' | 'visor'

export interface SceneAvatar {
  readonly seed: string
  readonly signature: string
  readonly skinColor: string
  readonly skinShadow: string
  readonly hairColor: string
  readonly outfitColor: string
  readonly outfitShade: string
  readonly accentColor: string
  readonly hairStyle: number
  readonly bodyStyle: number
  readonly accessory: AvatarAccessory
}

const SKIN_TONES: ReadonlyArray<{ base: string; shadow: string }> = [
  { base: '#FDE2C8', shadow: '#E6B996' },
  { base: '#F5CBA7', shadow: '#D9A173' },
  { base: '#E0AC69', shadow: '#BA8343' },
  { base: '#C68642', shadow: '#996228' },
  { base: '#8D5524', shadow: '#663B16' },
  { base: '#EAD2DC', shadow: '#C4A3B3' },
]

const HAIR_COLORS: readonly string[] = [
  '#FDE047', // Sunny blonde
  '#FB923C', // Copper ginger
  '#F472B6', // Rose pink
  '#38BDF8', // Electric blue
  '#A78BFA', // Soft violet
  '#34D399', // Mint green
  '#E2E8F0', // Silver white
  '#D97706', // Warm chestnut
]

// High-contrast outfits against #101014 / #141419 dark background (no near-black colors)
const OUTFIT_THEMES: ReadonlyArray<{ base: string; shade: string }> = [
  { base: '#60A5FA', shade: '#2563EB' }, // Sky blue
  { base: '#34D399', shade: '#059669' }, // Emerald
  { base: '#FBBF24', shade: '#D97706' }, // Amber
  { base: '#FB7185', shade: '#E11D48' }, // Coral rose
  { base: '#38BDF8', shade: '#0284C7' }, // Cyan
  { base: '#A3E635', shade: '#65A30D' }, // Lime
  { base: '#F472B6', shade: '#DB2777' }, // Magenta pink
  { base: '#FDBA74', shade: '#EA580C' }, // Peach orange
  { base: '#C084FC', shade: '#7C3AED' }, // Orchid
  { base: '#2DD4BF', shade: '#0D9488' }, // Teal
]

const ACCENT_COLORS: readonly string[] = [
  '#F4F3F8',
  '#FACC15',
  '#38BDF8',
  '#4ADE80',
  '#FB7185',
  '#DDD5FA',
]

const ACCESSORIES: readonly AvatarAccessory[] = [
  'none',
  'glasses',
  'headphones',
  'cap',
  'tie',
  'visor',
]

/** Deterministic 32-bit FNV-1a hash. */
export function fnv1a32(input: string): number {
  let hash = 0x811c9dc5
  for (let i = 0; i < input.length; i += 1) {
    hash ^= input.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193)
  }
  return hash >>> 0
}

/**
 * Deterministically builds a subagent avatar from `agentId` and an optional `salt`.
 */
export function avatarFromSeed(agentId: string, salt = 0): SceneAvatar {
  const key = salt === 0 ? agentId : `${agentId}#${salt}`
  const h1 = fnv1a32(key)
  const h2 = fnv1a32(`${key}:parts`)
  const h3 = fnv1a32(`${key}:colors`)

  const skin = SKIN_TONES[h1 % SKIN_TONES.length]!
  const hairColor = HAIR_COLORS[(h1 >>> 4) % HAIR_COLORS.length]!
  const outfit = OUTFIT_THEMES[(h2 + salt * 3) % OUTFIT_THEMES.length]!
  const accentColor = ACCENT_COLORS[(h3 >>> 3) % ACCENT_COLORS.length]!
  const hairStyle = (h2 >>> 5) % 8
  const bodyStyle = (h3 >>> 7) % 4
  const accessory = ACCESSORIES[(h1 >>> 9) % ACCESSORIES.length]!

  const signature = [
    skin.base,
    hairColor,
    outfit.base,
    hairStyle,
    bodyStyle,
    accessory,
  ].join('|')

  return {
    seed: agentId,
    signature,
    skinColor: skin.base,
    skinShadow: skin.shadow,
    hairColor,
    outfitColor: outfit.base,
    outfitShade: outfit.shade,
    accentColor,
    hairStyle,
    bodyStyle,
    accessory,
  }
}

/**
 * Assigns deterministic avatars to a list of simultaneous `agentIds`, re-rolling
 * with `salt + 1` on any visual collision so no two agents look identical.
 */
export function assignAvatars(agentIds: readonly string[]): Map<string, SceneAvatar> {
  const result = new Map<string, SceneAvatar>()
  const usedSignatures = new Set<string>()

  for (const agentId of agentIds) {
    if (result.has(agentId)) continue
    let salt = 0
    let candidate = avatarFromSeed(agentId, salt)
    while (usedSignatures.has(candidate.signature) && salt < 256) {
      salt += 1
      candidate = avatarFromSeed(agentId, salt)
    }
    usedSignatures.add(candidate.signature)
    result.set(agentId, candidate)
  }

  return result
}

/** Number of animation frames for a given `ScenePose`. */
export function poseFrameCount(pose: ScenePose): number {
  switch (pose) {
    case 'walk':
      return 4
    case 'idle':
    case 'work':
    case 'think':
    case 'raise_hand':
    case 'scared':
    case 'wait':
    case 'deliver':
    case 'sit':
      return 2
  }
}

function normalizeGrid(rows: readonly string[]): PixelMatrix {
  const normalized: string[] = []
  for (let r = 0; r < CHARACTER_H; r += 1) {
    const raw = rows[r] ?? '................'
    if (raw.length === CHARACTER_W) {
      normalized.push(raw)
    } else if (raw.length > CHARACTER_W) {
      normalized.push(raw.slice(0, CHARACTER_W))
    } else {
      normalized.push(raw.padEnd(CHARACTER_W, '.'))
    }
  }
  return normalized
}

// Orin base frames (16x24): purple blob with two rounded top ears, oval eyes & small smile
function buildOrinMatrix(pose: ScenePose, frameIndex: number): PixelMatrix {
  const f = frameIndex % poseFrameCount(pose)

  if (pose === 'sit') {
    return normalizeGrid([
      '................',
      '................',
      '................',
      '....OO....OO....',
      '...OLLO..OLLO...',
      '...OLPP66PPLO...'
        .replace(/6/g, 'O'),
      '..OOLLPPPPPPDO..',
      '..OLLPPPPPPPPDO.',
      '.OOLPPPPPPPPPPDO',
      '.OLPPOOOOOOOPPDO',
      '.OLPPO.PPP.OPPDO',
      '.OLPPO.PPP.OPPDO',
      '.OLPPPPPPPPPPPDO',
      '.OLPPPPOMOOPPPDO',
      '.OOLPPPPOOPPPDO.',
      '..ODPPPPPPPPDDO.',
      '..OODDDDDDDDDOO.',
      '...OOOPPPPPOOO..',
      '....OOPPPPPOO...',
      '....OODDDDDOO...',
      '....OOO..OOO....',
      '................',
      '................',
      '................',
    ].map((line) => line.replace(/M/g, 'O')))
  }

  if (pose === 'raise_hand') {
    const signShift = f === 0 ? 0 : 1
    const topRows =
      signShift === 0
        ? [
            '..........OOOOOO',
            '..........OWWWWO',
            '....OO....OWRRWO',
            '...OLLO...OW.RWO',
            '...OLPPO..OWWWWO',
            '..OOLLPPO.OOOOOO',
          ]
        : [
            '................',
            '..........OOOOOO',
            '....OO....OWWWWO',
            '...OLLO...OWRRWO',
            '...OLPPO..OW.RWO',
            '..OOLLPPO.OWWWWO',
          ]
    return normalizeGrid([
      ...topRows,
      '..OLLPPPPPPPDOO.',
      '.OOLPPPPPPPPPPO.',
      '.OLPPPPPPPPPPPDO',
      '.OLPPO.PPP.OPPDO',
      '.OLPPO.PPP.OPPDO',
      '.OLPPO.PPP.OPPDO',
      '.OLPPPPPPPPPPPDO',
      '.OLPPPPPOOPPPPDO',
      '.OOLPPPPPPPPPDO.',
      '..ODPPPPPPPPDDO.',
      '..OODDDDDDDDDOO.',
      '...OOODDDDDDOO..',
      '....OOOPPPPOOO..',
      '....OO.OOOO.OO..',
      '....OO......OO..',
      '....OO......OO..',
      '................',
      '................',
    ])
  }

  if (pose === 'scared') {
    const shift = f === 0 ? '' : '.'
    const trim = (s: string) => (shift ? (shift + s).slice(0, 16) : s)
    return normalizeGrid([
      '................',
      '....O......O....',
      '...OO..OO..OO...',
      '...OLLO..OLLO...',
      '..OOLPPOOOPLOO..',
      '..OLLPPPPPPPPO..',
      '.OOLPPPPPPPPPDO.',
      '.OLPPOOOOOOOPPDO',
      '.OLPOWWO.OWWOPDO',
      '.OLPOWOO.OWOOPDO',
      '.OLPPOOOOOOOPPDO',
      '.OLPPPPPPPPPPPDO',
      '.OLPPPPOOOOPPPDO',
      '.OOLPPPOOOOPPDO.',
      '..ODPPPPOOOODDO.',
      '..OODDDDDDDDDOO.',
      '...OOODDDDDDOO..',
      '....OOOPPPPOOO..',
      '....OO.OOOO.OO..',
      '....OO......OO..',
      '....OO......OO..',
      '................',
      '................',
      '................',
    ].map(trim))
  }

  if (pose === 'think') {
    const eyeRow = f === 0 ? '.OLPP.OPPP.OPPDO' : '.OLPPO.PPP.OPPDO'
    return normalizeGrid([
      '................',
      '...........OO...',
      '....OO....OLLO..',
      '...OLLO..OOLPO..',
      '...OLPPOOOPLPO..',
      '..OOLLPPPPPPDO..',
      '..OLLPPPPPPPPDO.',
      '.OOLPPPPPPPPPPDO',
      eyeRow,
      '.OLPPO.PPP.OPPDO',
      '.OLPPPPPPPPPPPDO',
      '.OLPPPPPPPPPPPDO',
      '.OLPPPPPOOOOPPDO',
      '.OOLPPPPPPPOPDO.',
      '..ODPPPPPPOPDDO.',
      '..OODDDDDDOODOO.',
      '...OOODDDDDDOO..',
      '....OOOPPPPOOO..',
      '....OO.OOOO.OO..',
      '....OO......OO..',
      '....OO......OO..',
      '................',
      '................',
      '................',
    ])
  }

  if (pose === 'work') {
    const hands =
      f === 0
        ? '..ODPPOOOOOPDDO.'
        : '..ODPOPOOOPOPDO.'
    return normalizeGrid([
      '................',
      '................',
      '....OO....OO....',
      '...OLLO..OLLO...',
      '...OLPPOOOPLPO..',
      '..OOLLPPPPPPDO..',
      '..OLLPPPPPPPPDO.',
      '.OOLPPPPPPPPPPDO',
      '.OLPPPPPPPPPPPDO',
      '.OLPPO.PPP.OPPDO',
      '.OLPPO.PPP.OPPDO',
      '.OLPPPPPPPPPPPDO',
      '.OLPPPPPOOPPPPDO',
      '.OOLPPPPPPPPPDO.',
      hands,
      '..OODDDDDDDDDOO.',
      '...OOODDDDDDOO..',
      '....OOOPPPPOOO..',
      '....OO.OOOO.OO..',
      '....OO......OO..',
      '....OO......OO..',
      '................',
      '................',
      '................',
    ])
  }

  if (pose === 'deliver') {
    const paperRow = f === 0 ? '..ODPPPOOOOWWWWO' : '..ODPPPOOOOWWWWO'
    return normalizeGrid([
      '................',
      '................',
      '....OO....OO....',
      '...OLLO..OLLO...',
      '...OLPPOOOPLPO..',
      '..OOLLPPPPPPDO..',
      '..OLLPPPPPPPPDO.',
      '.OOLPPPPPPPPPPDO',
      '.OLPPPPPPPPPPPDO',
      '.OLPPO.PPP.OPPDO',
      '.OLPPO.PPP.OPPDO',
      '.OLPPPPPPPPPPPDO',
      '.OLPPPPOOOPPPPDO',
      '.OOLPPPPPPPPPDOO',
      paperRow,
      '..OODDDDDDDDDOOO',
      '...OOODDDDDDOO..',
      '....OOOPPPPOOO..',
      '....OO.OOOO.OO..',
      '....OO......OO..',
      '....OO......OO..',
      '................',
      '................',
      '................',
    ])
  }

  if (pose === 'wait') {
    return normalizeGrid([
      '................',
      '................',
      '....OO....OO....',
      '...OLLO..OLLO...',
      '...OLPPOOOPLPO..',
      '..OOLLPPPPPPDO..',
      '..OLLPPPPPPPPDO.',
      '.OOLPPPPPPPPPPDO',
      '.OLPPPPPPPPPPPDO',
      '.OLPPO.PPP.OPPDO',
      '.OLPPO.PPP.OPPDO',
      '.OLPPPPPPPPPPPDO',
      '.OLPPPPOOOPPPPDO',
      '.OOLOOOOOOOOODO.',
      '..ODPPPPPPPPDDO.',
      '..OODDDDDDDDDOO.',
      '...OOODDDDDDOO..',
      '....OOOPPPPOOO..',
      '....OO.OOOO.OO..',
      f === 0 ? '....OO......OO..' : '....OO.....OO...',
      '....OO......OO..',
      '................',
      '................',
      '................',
    ])
  }

  if (pose === 'walk') {
    const footRows =
      f === 0
        ? ['....OO.OOOO.OO..', '...OO.......OO..', '...OO...........']
        : f === 1
          ? ['....OO.OOOO.OO..', '....OO......OO..', '....OO......OO..']
          : f === 2
            ? ['....OO.OOOO.OO..', '....OO.......OO.', '.............OO.']
            : ['....OO.OOOO.OO..', '....OO......OO..', '....OO......OO..']
    return normalizeGrid([
      '................',
      '................',
      '....OO....OO....',
      '...OLLO..OLLO...',
      '...OLPPOOOPLPO..',
      '..OOLLPPPPPPDO..',
      '..OLLPPPPPPPPDO.',
      '.OOLPPPPPPPPPPDO',
      '.OLPPPPPPPPPPPDO',
      '.OLPPO.PPP.OPPDO',
      '.OLPPO.PPP.OPPDO',
      '.OLPPPPPPPPPPPDO',
      '.OLPPPPOOOPPPPDO',
      '.OOLPPPPPPPPPDO.',
      '..ODPPPPPPPPDDO.',
      '..OODDDDDDDDDOO.',
      '...OOODDDDDDOO..',
      '....OOOPPPPOOO..',
      ...footRows,
      '................',
      '................',
      '................',
    ])
  }

  // Default: idle (breathing 2-frame loop)
  const bobDown = f === 1
  const baseRows = [
    '................',
    '................',
    '....OO....OO....',
    '...OLLO..OLLO...',
    '...OLPPOOOPLPO..',
    '..OOLLPPPPPPDO..',
    '..OLLPPPPPPPPDO.',
    '.OOLPPPPPPPPPPDO',
    '.OLPPPPPPPPPPPDO',
    '.OLPPO.PPP.OPPDO',
    '.OLPPO.PPP.OPPDO',
    '.OLPPPPPPPPPPPDO',
    '.OLPPPPOOOPPPPDO',
    '.OOLPPPPPPPPPDO.',
    '..ODPPPPPPPPDDO.',
    '..OODDDDDDDDDOO.',
    '...OOODDDDDDOO..',
    '....OOOPPPPOOO..',
    '....OO.OOOO.OO..',
    '....OO......OO..',
    '....OO......OO..',
    '................',
    '................',
    '................',
  ]
  if (bobDown) {
    return normalizeGrid(['................', ...baseRows.slice(0, 23)])
  }
  return normalizeGrid(baseRows)
}

function buildAvatarMatrix(avatar: SceneAvatar, pose: ScenePose, frameIndex: number): PixelMatrix {
  const f = frameIndex % poseFrameCount(pose)
  const grid: string[][] = Array.from({ length: CHARACTER_H }, () =>
    Array.from({ length: CHARACTER_W }, () => '.')
  )

  const setPx = (x: number, y: number, ch: string) => {
    if (x >= 0 && x < CHARACTER_W && y >= 0 && y < CHARACTER_H) {
      grid[y]![x] = ch
    }
  }

  const drawRow = (y: number, xStart: number, pattern: string) => {
    for (let i = 0; i < pattern.length; i += 1) {
      const ch = pattern[i]!
      if (ch !== '.') setPx(xStart + i, y, ch)
    }
  }

  const bob = (pose === 'idle' && f === 1) || (pose === 'work' && f === 1) ? 1 : 0
  const sitOffset = pose === 'sit' ? 2 : 0
  const yOff = bob + sitOffset

  // 1. Hair / Head silhouette (rows 2..11 + yOff)
  drawRow(2 + yOff, 5, 'OOOOOO')
  drawRow(3 + yOff, 4, 'OHHHHHHO')
  drawRow(4 + yOff, 3, 'OHHHHHHHHO')
  drawRow(5 + yOff, 3, 'OHHSSSSHHHO')
  drawRow(6 + yOff, 3, 'OHSSSSSSSHO')
  drawRow(7 + yOff, 3, 'OSSSSSSSSSO')
  drawRow(8 + yOff, 3, 'OSSOSSSOSSO')
  drawRow(9 + yOff, 3, 'OSSSSSSSSSO')
  drawRow(10 + yOff, 4, 'OSSSSSSSO')
  drawRow(11 + yOff, 5, 'OOOOOOO')

  // Hair style variations
  switch (avatar.hairStyle % 8) {
    case 0: // Spiky top
      drawRow(1 + yOff, 5, 'O.OO.O')
      drawRow(2 + yOff, 4, 'OHHHHHHO')
      break
    case 1: // Long bob sides
      drawRow(7 + yOff, 2, 'OH')
      drawRow(8 + yOff, 2, 'OH')
      drawRow(9 + yOff, 2, 'OH')
      drawRow(7 + yOff, 12, 'HO')
      drawRow(8 + yOff, 12, 'HO')
      drawRow(9 + yOff, 12, 'HO')
      break
    case 2: // Top bun
      drawRow(0 + yOff, 6, 'OOOO')
      drawRow(1 + yOff, 5, 'OHHHHO')
      break
    case 3: // Curly afro volume
      drawRow(2 + yOff, 3, 'OHHHHHHHHO')
      drawRow(3 + yOff, 2, 'OHHHHHHHHHHO')
      drawRow(4 + yOff, 2, 'OHHHHHHHHHHO')
      break
    case 4: // Side swept fringe
      drawRow(5 + yOff, 4, 'HHHHHSSS')
      drawRow(6 + yOff, 4, 'HHHSSSSS')
      break
    case 5: // Center Mohawk crest
      drawRow(0 + yOff, 7, 'OO')
      drawRow(1 + yOff, 6, 'OHHO')
      drawRow(2 + yOff, 6, 'OHHO')
      break
    case 6: // Twin tufts
      drawRow(2 + yOff, 2, 'OHO')
      drawRow(2 + yOff, 11, 'OHO')
      drawRow(3 + yOff, 2, 'OHHO')
      drawRow(3 + yOff, 10, 'OHHO')
      break
    default: // Classic parted
      drawRow(5 + yOff, 4, 'HHSSSSHH')
      break
  }

  // Facial expressions by pose
  if (pose === 'scared') {
    setPx(6, 7 + yOff, 'W')
    setPx(6, 8 + yOff, 'O')
    setPx(10, 7 + yOff, 'W')
    setPx(10, 8 + yOff, 'O')
    drawRow(10 + yOff, 7, 'OO')
  } else if (pose === 'think') {
    setPx(6, 7 + yOff, 'O')
    setPx(10, 7 + yOff, 'O')
    setPx(8, 10 + yOff, 'E')
  } else {
    setPx(6, 8 + yOff, 'O')
    setPx(10, 8 + yOff, 'O')
    drawRow(10 + yOff, 7, 'EE')
  }

  // Accessories
  if (avatar.accessory === 'glasses') {
    drawRow(8 + yOff, 5, 'AOA.AOA')
  } else if (avatar.accessory === 'visor') {
    drawRow(8 + yOff, 5, 'QQQQQQQ')
  } else if (avatar.accessory === 'headphones') {
    drawRow(6 + yOff, 2, 'AA')
    drawRow(7 + yOff, 2, 'AA')
    drawRow(6 + yOff, 12, 'AA')
    drawRow(7 + yOff, 12, 'AA')
  } else if (avatar.accessory === 'cap') {
    drawRow(3 + yOff, 3, 'OAAAAAAAAO')
    drawRow(4 + yOff, 3, 'OAAAAAAAAAAO')
  }

  // 2. Torso / Outfit (rows 12..17 + yOff)
  drawRow(12 + yOff, 4, 'OCCCCCCCO')
  drawRow(13 + yOff, 3, 'OCCCCCCCCCO')
  drawRow(14 + yOff, 3, 'OCCCCCCCCBO')
  drawRow(15 + yOff, 3, 'OCCCCCCCCBO')
  drawRow(16 + yOff, 4, 'OBBBBBBBBO')
  drawRow(17 + yOff, 4, 'OOOOOOOOOO')

  // Body style detail
  if (avatar.bodyStyle === 1) {
    // Jacket open center stripe
    for (let r = 12; r <= 16; r += 1) setPx(8, r + yOff, 'W')
  } else if (avatar.bodyStyle === 2) {
    // Hoodie pocket
    drawRow(15 + yOff, 6, 'BBBB')
  } else if (avatar.bodyStyle === 3) {
    // Collar V-neck
    drawRow(12 + yOff, 7, 'WW')
  }

  if (avatar.accessory === 'tie') {
    setPx(8, 12 + yOff, 'A')
    setPx(8, 13 + yOff, 'A')
    setPx(8, 14 + yOff, 'A')
  }

  // Pose-specific arms
  if (pose === 'raise_hand') {
    // Right arm raised holding a "?" sign
    drawRow(2, 11, 'OOOOO')
    drawRow(3, 11, 'OWWWO')
    drawRow(4, 11, 'OWRWO')
    drawRow(5, 11, 'OWWWO')
    drawRow(6, 11, 'OOOOO')
    setPx(12, 7 + yOff, 'S')
    setPx(12, 8 + yOff, 'C')
    setPx(12, 9 + yOff, 'C')
    setPx(12, 10 + yOff, 'C')
    setPx(12, 11 + yOff, 'C')
  } else if (pose === 'deliver') {
    drawRow(13 + yOff, 11, 'CCSS')
    drawRow(12 + yOff, 12, 'WWWW')
    drawRow(13 + yOff, 13, 'WWK')
  } else if (pose === 'work') {
    const handY = f === 0 ? 14 + yOff : 13 + yOff
    drawRow(handY, 2, 'OS')
    drawRow(handY, 12, 'SO')
  } else if (pose === 'wait') {
    drawRow(14 + yOff, 4, 'OSSSSSSSO')
  } else if (pose === 'think') {
    setPx(11, 11 + yOff, 'S')
    setPx(10, 10 + yOff, 'S')
  }

  // 3. Legs / Feet (rows 18..21)
  if (pose === 'sit') {
    drawRow(19, 4, 'OOBB..BBOO')
    drawRow(20, 4, 'OOOO..OOOO')
  } else if (pose === 'walk') {
    if (f === 0) {
      drawRow(18, 4, 'OOBO..OBOO')
      drawRow(19, 3, 'OOBO....OBOO')
      drawRow(20, 3, 'OOO......OOO')
    } else if (f === 2) {
      drawRow(18, 4, 'OOBO..OBOO')
      drawRow(19, 5, 'OOBO..OBOO')
      drawRow(20, 5, 'OOO....OOO')
    } else {
      drawRow(18, 5, 'OOBO.OBOO')
      drawRow(19, 5, 'OOBO.OBOO')
      drawRow(20, 5, 'OOO...OOO')
    }
  } else {
    drawRow(18 + bob, 5, 'OOBO.OBOO')
    drawRow(19 + bob, 5, 'OOBO.OBOO')
    drawRow(20 + bob, 5, 'OOO...OOO')
  }

  return grid.map((r) => r.join(''))
}

/**
 * Pure character matrix builder for Orin or any generated `SceneAvatar`.
 * Always returns a 16x24 matrix and its key->hex color table.
 */
export function characterMatrix(
  who: 'orin' | SceneAvatar,
  pose: ScenePose,
  frame: number
): { matrix: PixelMatrix; colors: Record<string, string> } {
  const count = poseFrameCount(pose)
  const safeFrame = ((Math.floor(frame) % count) + count) % count

  if (who === 'orin') {
    const colors: Record<string, string> = {
      O: SCENE_PALETTE.outline,
      P: SCENE_PALETTE.orinBase,
      L: SCENE_PALETTE.orinLight,
      D: SCENE_PALETTE.orinShadow,
      W: SCENE_PALETTE.paper,
      K: SCENE_PALETTE.paperShadow,
      R: SCENE_PALETTE.danger,
      G: SCENE_PALETTE.success,
    }
    return {
      matrix: buildOrinMatrix(pose, safeFrame),
      colors,
    }
  }

  const colors: Record<string, string> = {
    O: SCENE_PALETTE.outline,
    P: SCENE_PALETTE.orinBase,
    L: SCENE_PALETTE.orinLight,
    D: SCENE_PALETTE.orinShadow,
    S: who.skinColor,
    E: who.skinShadow,
    H: who.hairColor,
    C: who.outfitColor,
    B: who.outfitShade,
    A: who.accentColor,
    W: SCENE_PALETTE.paper,
    K: SCENE_PALETTE.paperShadow,
    G: SCENE_PALETTE.success,
    R: SCENE_PALETTE.danger,
    Q: SCENE_PALETTE.Q,
  }

  return {
    matrix: buildAvatarMatrix(who, pose, safeFrame),
    colors,
  }
}

export interface PropDefinition {
  readonly width: number
  readonly height: number
  readonly matrix: PixelMatrix
  readonly colors: Record<string, string>
}

const PROP_COLORS: Record<string, string> = {
  O: SCENE_PALETTE.O,
  P: SCENE_PALETTE.P,
  L: SCENE_PALETTE.L,
  D: SCENE_PALETTE.D,
  W: SCENE_PALETTE.W,
  K: SCENE_PALETTE.K,
  G: SCENE_PALETTE.G,
  J: SCENE_PALETTE.J,
  R: SCENE_PALETTE.R,
  Y: SCENE_PALETTE.Y,
  M: SCENE_PALETTE.M,
  N: SCENE_PALETTE.N,
  U: SCENE_PALETTE.U,
  T: SCENE_PALETTE.T,
  V: SCENE_PALETTE.V,
  Q: SCENE_PALETTE.Q,
  H: SCENE_PALETTE.H,
  C: SCENE_PALETTE.C,
  B: SCENE_PALETTE.B,
  A: SCENE_PALETTE.A,
}

function makeRectProp(
  width: number,
  height: number,
  draw: (setPx: (x: number, y: number, ch: string) => void) => void
): PropDefinition {
  const rows: string[][] = Array.from({ length: height }, () =>
    Array.from({ length: width }, () => '.')
  )
  const setPx = (x: number, y: number, ch: string) => {
    if (x >= 0 && x < width && y >= 0 && y < height) {
      rows[y]![x] = ch
    }
  }
  draw(setPx)
  return {
    width,
    height,
    matrix: rows.map((r) => r.join('')),
    colors: PROP_COLORS,
  }
}

function fillBox(
  setPx: (x: number, y: number, ch: string) => void,
  x: number,
  y: number,
  w: number,
  h: number,
  fill: string,
  border?: string
) {
  for (let py = y; py < y + h; py += 1) {
    for (let px = x; px < x + w; px += 1) {
      const isEdge = px === x || px === x + w - 1 || py === y || py === y + h - 1
      setPx(px, py, isEdge && border ? border : fill)
    }
  }
}

const PROP_DEFINITIONS: Readonly<Record<string, PropDefinition>> = {
  terminal: makeRectProp(32, 20, (setPx) => {
    // Desk surface
    fillBox(setPx, 0, 10, 32, 6, 'M', 'O')
    fillBox(setPx, 2, 16, 3, 4, 'U', 'O')
    fillBox(setPx, 27, 16, 3, 4, 'U', 'O')
    // Monitor with glowing green terminal lines
    fillBox(setPx, 8, 1, 16, 10, 'V', 'O')
    fillBox(setPx, 10, 3, 12, 6, 'O')
    setPx(11, 4, 'G')
    setPx(12, 4, 'G')
    setPx(13, 4, 'G')
    setPx(15, 4, 'G')
    setPx(11, 6, 'G')
    setPx(12, 6, 'G')
    setPx(14, 6, 'G')
    setPx(15, 6, 'G')
    setPx(16, 6, 'G')
  }),
  files: makeRectProp(32, 24, (setPx) => {
    // Bookshelf frame
    fillBox(setPx, 1, 0, 30, 24, 'U', 'O')
    fillBox(setPx, 3, 2, 26, 6, 'M')
    fillBox(setPx, 3, 9, 26, 6, 'M')
    fillBox(setPx, 3, 16, 26, 6, 'M')
    // Colorful binders/books
    const bookColors = ['P', 'Q', 'G', 'Y', 'W', 'A', 'C']
    for (let x = 4; x < 27; x += 2) {
      const c1 = bookColors[x % bookColors.length]!
      const c2 = bookColors[(x + 2) % bookColors.length]!
      fillBox(setPx, x, 3, 2, 5, c1)
      fillBox(setPx, x, 10, 2, 5, c2)
      fillBox(setPx, x, 17, 2, 5, c1)
    }
  }),
  writing: makeRectProp(32, 20, (setPx) => {
    fillBox(setPx, 0, 8, 32, 7, 'N', 'O')
    fillBox(setPx, 2, 15, 3, 5, 'M', 'O')
    fillBox(setPx, 27, 15, 3, 5, 'M', 'O')
    // Paper sheet and pen on desk
    fillBox(setPx, 10, 9, 10, 5, 'W', 'K')
    setPx(12, 11, 'O')
    setPx(13, 11, 'O')
    setPx(15, 11, 'O')
    setPx(22, 10, 'P')
    setPx(23, 11, 'P')
  }),
  browser: makeRectProp(32, 20, (setPx) => {
    fillBox(setPx, 0, 11, 32, 5, 'T', 'O')
    fillBox(setPx, 3, 16, 3, 4, 'V', 'O')
    fillBox(setPx, 26, 16, 3, 4, 'V', 'O')
    // Wide browser window screen
    fillBox(setPx, 5, 1, 22, 11, 'V', 'O')
    fillBox(setPx, 7, 3, 18, 7, 'Q')
    fillBox(setPx, 7, 3, 18, 2, 'W')
    setPx(8, 3, 'R')
    setPx(10, 3, 'H')
    setPx(12, 3, 'G')
  }),
  planning: makeRectProp(32, 16, (setPx) => {
    // Wall whiteboard / kanban board
    fillBox(setPx, 0, 0, 32, 16, 'W', 'O')
    fillBox(setPx, 1, 1, 30, 14, 'K')
    // Sticky notes
    fillBox(setPx, 3, 3, 6, 5, 'H', 'O')
    fillBox(setPx, 11, 3, 6, 5, 'P', 'O')
    fillBox(setPx, 19, 3, 6, 5, 'G', 'O')
    fillBox(setPx, 7, 9, 6, 5, 'Q', 'O')
    fillBox(setPx, 16, 9, 6, 5, 'A', 'O')
  }),
  meeting: makeRectProp(48, 20, (setPx) => {
    // Large central table
    fillBox(setPx, 0, 4, 48, 11, 'N', 'O')
    fillBox(setPx, 2, 6, 44, 7, 'M')
    fillBox(setPx, 4, 15, 4, 5, 'U', 'O')
    fillBox(setPx, 40, 15, 4, 5, 'U', 'O')
    // Documents and Orin badge in center
    fillBox(setPx, 14, 7, 6, 4, 'W')
    fillBox(setPx, 22, 7, 5, 4, 'P')
    fillBox(setPx, 30, 7, 6, 4, 'W')
  }),
  extensions: makeRectProp(32, 24, (setPx) => {
    // Server/plugin rack with LEDs
    fillBox(setPx, 4, 0, 24, 24, 'V', 'O')
    fillBox(setPx, 6, 2, 20, 5, 'T', 'O')
    fillBox(setPx, 6, 9, 20, 5, 'T', 'O')
    fillBox(setPx, 6, 16, 20, 5, 'T', 'O')
    setPx(8, 4, 'G')
    setPx(11, 4, 'P')
    setPx(8, 11, 'Q')
    setPx(11, 11, 'G')
    setPx(8, 18, 'H')
    setPx(11, 18, 'P')
  }),
  generic: makeRectProp(32, 18, (setPx) => {
    fillBox(setPx, 0, 6, 32, 7, 'M', 'O')
    fillBox(setPx, 3, 13, 3, 5, 'U', 'O')
    fillBox(setPx, 26, 13, 3, 5, 'U', 'O')
    fillBox(setPx, 12, 3, 8, 5, 'T', 'O')
  }),
  lounge: makeRectProp(48, 20, (setPx) => {
    // Cozy purple/plum sofa
    fillBox(setPx, 2, 2, 44, 9, 'D', 'O')
    fillBox(setPx, 0, 7, 6, 11, 'P', 'O')
    fillBox(setPx, 42, 7, 6, 11, 'P', 'O')
    fillBox(setPx, 5, 9, 38, 8, 'P', 'O')
    fillBox(setPx, 6, 10, 18, 5, 'L')
    fillBox(setPx, 25, 10, 17, 5, 'L')
    fillBox(setPx, 4, 17, 4, 3, 'U', 'O')
    fillBox(setPx, 40, 17, 4, 3, 'U', 'O')
  }),
  door: makeRectProp(16, 32, (setPx) => {
    fillBox(setPx, 1, 0, 14, 32, 'U', 'O')
    fillBox(setPx, 3, 2, 10, 28, 'M', 'O')
    fillBox(setPx, 5, 4, 6, 10, 'N')
    fillBox(setPx, 5, 16, 6, 10, 'N')
    setPx(11, 16, 'H')
    setPx(11, 17, 'H')
  }),
  plant: makeRectProp(16, 16, (setPx) => {
    fillBox(setPx, 5, 10, 6, 6, 'M', 'O')
    fillBox(setPx, 6, 4, 4, 6, 'J')
    fillBox(setPx, 3, 5, 4, 3, 'G')
    fillBox(setPx, 9, 5, 4, 3, 'G')
    fillBox(setPx, 5, 2, 6, 3, 'G')
  }),
  floor: makeRectProp(16, 16, (setPx) => {
    fillBox(setPx, 0, 0, 16, 16, 'V')
    setPx(0, 0, 'O')
  }),
  wall: makeRectProp(16, 16, (setPx) => {
    fillBox(setPx, 0, 0, 16, 16, 'T', 'V')
  }),
  paper: makeRectProp(10, 12, (setPx) => {
    fillBox(setPx, 1, 1, 8, 10, 'W', 'O')
    setPx(3, 4, 'T')
    setPx(4, 4, 'T')
    setPx(5, 4, 'T')
    setPx(3, 6, 'P')
    setPx(4, 6, 'P')
    setPx(6, 6, 'T')
    setPx(3, 8, 'T')
    setPx(5, 8, 'T')
  }),
  envelope: makeRectProp(12, 9, (setPx) => {
    fillBox(setPx, 0, 0, 12, 9, 'W', 'O')
    setPx(1, 1, 'P')
    setPx(2, 2, 'P')
    setPx(3, 3, 'P')
    setPx(4, 4, 'P')
    setPx(5, 5, 'P')
    setPx(6, 5, 'P')
    setPx(7, 4, 'P')
    setPx(8, 3, 'P')
    setPx(9, 2, 'P')
    setPx(10, 1, 'P')
  }),
  crumpled_paper: makeRectProp(10, 10, (setPx) => {
    fillBox(setPx, 2, 1, 6, 8, 'K', 'O')
    fillBox(setPx, 1, 2, 8, 6, 'W', 'O')
    setPx(3, 4, 'R')
    setPx(5, 3, 'K')
    setPx(6, 5, 'R')
    setPx(4, 6, 'K')
  }),
  check: makeRectProp(12, 12, (setPx) => {
    fillBox(setPx, 1, 1, 10, 10, 'J', 'O')
    setPx(3, 6, 'W')
    setPx(4, 7, 'W')
    setPx(5, 8, 'W')
    setPx(6, 6, 'W')
    setPx(7, 5, 'W')
    setPx(8, 4, 'W')
  }),
  spark: makeRectProp(12, 12, (setPx) => {
    setPx(5, 1, 'Y')
    setPx(6, 2, 'R')
    setPx(2, 5, 'Y')
    setPx(9, 4, 'H')
    fillBox(setPx, 4, 4, 4, 4, 'R', 'Y')
    setPx(5, 5, 'H')
    setPx(6, 6, 'W')
    setPx(3, 9, 'R')
    setPx(8, 9, 'Y')
  }),
  poof: makeRectProp(12, 12, (setPx) => {
    fillBox(setPx, 3, 3, 6, 6, 'L')
    fillBox(setPx, 2, 4, 8, 4, 'W')
    setPx(4, 2, 'W')
    setPx(7, 2, 'L')
    setPx(3, 9, 'K')
    setPx(8, 9, 'K')
  }),
  question_sign: makeRectProp(10, 12, (setPx) => {
    fillBox(setPx, 1, 0, 8, 9, 'W', 'O')
    setPx(4, 2, 'R')
    setPx(5, 2, 'R')
    setPx(6, 3, 'R')
    setPx(5, 4, 'R')
    setPx(4, 5, 'R')
    setPx(4, 7, 'R')
    setPx(4, 9, 'U')
    setPx(5, 9, 'U')
    setPx(4, 10, 'U')
    setPx(5, 10, 'U')
  }),
  zzz: makeRectProp(12, 10, (setPx) => {
    // Small 'z' and larger 'Z'
    setPx(1, 5, 'L')
    setPx(2, 5, 'L')
    setPx(3, 5, 'L')
    setPx(2, 6, 'L')
    setPx(1, 7, 'L')
    setPx(2, 7, 'L')
    setPx(3, 7, 'L')

    setPx(6, 1, 'P')
    setPx(7, 1, 'P')
    setPx(8, 1, 'P')
    setPx(9, 1, 'P')
    setPx(8, 2, 'P')
    setPx(7, 3, 'P')
    setPx(6, 4, 'P')
    setPx(7, 4, 'P')
    setPx(8, 4, 'P')
    setPx(9, 4, 'P')
  }),
}

export function propMatrix(id: string): PropDefinition | null {
  return PROP_DEFINITIONS[id] ?? null
}

export function getPropDimensions(id: string): { width: number; height: number } {
  const def = PROP_DEFINITIONS[id]
  return def ? { width: def.width, height: def.height } : { width: 16, height: 16 }
}

export type CanvasImageSource = HTMLCanvasElement | OffscreenCanvas

const characterSpriteCache = new Map<string, CanvasImageSource>()
const propSpriteCache = new Map<string, CanvasImageSource>()

function createRasterCanvas(
  width: number,
  height: number
): { canvas: CanvasImageSource; ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D } | null {
  try {
    if (typeof OffscreenCanvas !== 'undefined') {
      const offscreen = new OffscreenCanvas(width, height)
      const ctx = offscreen.getContext('2d')
      if (ctx) {
        return { canvas: offscreen, ctx }
      }
    }
  } catch {
    // Fall back to HTMLCanvasElement
  }

  if (typeof document === 'undefined' || typeof document.createElement !== 'function') {
    return null
  }

  try {
    const el = document.createElement('canvas')
    el.width = width
    el.height = height
    if (typeof el.getContext !== 'function') return null

    // Avoid jsdom's unimplemented getContext console noise unless mocked in a test
    const isJsdom =
      typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent ?? '')
    const isMocked =
      'mock' in el.getContext ||
      (typeof HTMLCanvasElement !== 'undefined' &&
        'mock' in HTMLCanvasElement.prototype.getContext)
    if (isJsdom && !isMocked) return null

    const ctx = el.getContext('2d')
    if (!ctx) return null
    return { canvas: el, ctx }
  } catch {
    return null
  }
}

function rasterizeMatrix(
  matrix: PixelMatrix,
  colors: Record<string, string>,
  width: number,
  height: number
): CanvasImageSource | null {
  const target = createRasterCanvas(width, height)
  if (!target) return null
  const { canvas, ctx } = target

  for (let y = 0; y < matrix.length; y += 1) {
    const row = matrix[y]!
    for (let x = 0; x < row.length; x += 1) {
      const key = row[x]!
      if (key === '.') continue
      const color = colors[key]
      if (!color) continue
      ctx.fillStyle = color
      ctx.fillRect(x, y, 1, 1)
    }
  }

  return canvas
}

/**
 * Rasterizes a character frame once and caches the resulting `CanvasImageSource`.
 * Returns `null` when a 2D canvas context is unavailable (e.g. jsdom).
 */
export function getCharacterSprite(
  who: 'orin' | SceneAvatar,
  pose: ScenePose,
  frame: number
): CanvasImageSource | null {
  const count = poseFrameCount(pose)
  const safeFrame = ((Math.floor(frame) % count) + count) % count
  const idKey = who === 'orin' ? 'orin' : who.signature
  const cacheKey = `${idKey}:${pose}:${safeFrame}`

  const cached = characterSpriteCache.get(cacheKey)
  if (cached) return cached

  const { matrix, colors } = characterMatrix(who, pose, safeFrame)
  const rasterized = rasterizeMatrix(matrix, colors, CHARACTER_W, CHARACTER_H)
  if (rasterized) {
    characterSpriteCache.set(cacheKey, rasterized)
  }
  return rasterized
}

/**
 * Rasterizes a furniture/item prop once and caches the `CanvasImageSource`.
 * Returns `null` if unknown or when 2D canvas context is unavailable.
 */
export function getPropSprite(id: string): CanvasImageSource | null {
  const def = PROP_DEFINITIONS[id]
  if (!def) return null

  const cached = propSpriteCache.get(id)
  if (cached) return cached

  const rasterized = rasterizeMatrix(def.matrix, def.colors, def.width, def.height)
  if (rasterized) {
    propSpriteCache.set(id, rasterized)
  }
  return rasterized
}

import { describe, expect, it } from 'vitest'
import {
  CHARACTER_H,
  CHARACTER_W,
  SCENE_PALETTE,
   assignAvatars,
  avatarFromSeed,
  characterMatrix,
  getCharacterSprite,
  getPropDimensions,
  getPropSprite,
  poseFrameCount,
} from '../sceneSprites'
import type { ScenePose } from '../sceneTypes'

const ALL_POSES: readonly ScenePose[] = [
  'idle',
  'walk',
  'work',
  'think',
  'raise_hand',
  'scared',
  'wait',
  'deliver',
  'sit',
]

describe('sceneSprites', () => {
  it('avatarFromSeed is deterministic for the same id and varies across ids', () => {
    const first = avatarFromSeed('agent:researcher-1')
    const second = avatarFromSeed('agent:researcher-1')
    const other = avatarFromSeed('agent:verifier-2')

    expect(first).toEqual(second)
    expect(first.signature).not.toBe(other.signature)
  })

  it('assignAvatars produces unique avatars across 30 simultaneous agent ids', () => {
    const ids = Array.from({ length: 30 }, (_, idx) => `agent:sub-${idx + 1}`)
    const assigned = assignAvatars(ids)

    expect(assigned.size).toBe(30)
    const signatures = new Set(Array.from(assigned.values()).map((a) => a.signature))
    expect(signatures.size).toBe(30)
  })

  it('poseFrameCount returns >= 1 for every ScenePose', () => {
    for (const pose of ALL_POSES) {
      expect(poseFrameCount(pose)).toBeGreaterThanOrEqual(1)
    }
  })

  it('builds 16x24 matrices using valid palette keys for Orin and generated avatars across all poses/frames', () => {
    const sampleAvatar = avatarFromSeed('agent:builder-99')
    const subjects = ['orin' as const, sampleAvatar]

    for (const who of subjects) {
      for (const pose of ALL_POSES) {
        const frames = poseFrameCount(pose)
        for (let f = 0; f < frames; f += 1) {
          const { matrix, colors } = characterMatrix(who, pose, f)
          expect(matrix).toHaveLength(CHARACTER_H)
          for (const row of matrix) {
            expect(row).toHaveLength(CHARACTER_W)
            for (const ch of row) {
              if (ch === '.') continue
              expect(colors[ch]).toBeDefined()
              expect(ch in SCENE_PALETTE).toBe(true)
            }
          }
        }
      }
    }
  })

  it('uses Orin canonical purple #B4A4EF on the Orin sprite', () => {
    const { matrix, colors } = characterMatrix('orin', 'idle', 0)
    expect(colors.P).toBe('#B4A4EF')
    const usesMainPurple = matrix.some((row) => row.includes('P'))
    expect(usesMainPurple).toBe(true)
  })

  it('degrades gracefully without throwing in jsdom when rasterizing characters and props', () => {
    const avatar = avatarFromSeed('agent:test-jsdom')
    expect(() => getCharacterSprite('orin', 'idle', 0)).not.toThrow()
    expect(() => getCharacterSprite(avatar, 'walk', 1)).not.toThrow()
    expect(() => getPropSprite('terminal')).not.toThrow()
    expect(() => getPropSprite('envelope')).not.toThrow()
    expect(getPropDimensions('terminal').width).toBeGreaterThan(0)
  })
})

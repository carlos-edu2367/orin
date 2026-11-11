import { describe, expect, it } from 'vitest'
import { SCENE_ART_FRAMES } from '../assets/sceneArtManifest'
import { createAnimator, syncAnimator } from '../sceneAnimator'
import { placeArtCharacter, type ArtCharacter } from '../sceneArt'
import { SCENE_SEATS, SOFA_SPOT, STATION_ANCHORS, stationSlot } from '../sceneLayout'
import { ART_PERSON_LOOKS } from '../sceneSprites'
import {
  MAIN_AGENT_ID,
  type SceneAgent,
  type ScenePose,
  type SceneState,
} from '../sceneTypes'

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

function character(overrides: Partial<ArtCharacter>): ArtCharacter {
  const anchor = STATION_ANCHORS.meeting
  return {
    isMain: false,
    look: 0,
    pose: 'idle',
    frame: 0,
    facing: 'right',
    station: 'meeting',
    x: anchor.x,
    y: anchor.y,
    ...overrides,
  }
}

function agent(id: string, name: string): SceneAgent {
  const isMain = id === MAIN_AGENT_ID
  return {
    id,
    isMain,
    name,
    parentId: isMain ? null : MAIN_AGENT_ID,
    depth: isMain ? 0 : 1,
    station: 'files',
    pose: 'work',
    status: 'working',
    statusLabel: 'trabalhando',
    task: 'Ler arquivos',
    currentTool: 'Read',
    lastThought: '',
    lastSpeech: '',
    bubble: null,
    activities: [],
    durationLabel: '1s',
  }
}

function scene(agents: SceneAgent[]): SceneState {
  return {
    turnId: 'turn-1',
    live: true,
    waitingUser: false,
    lightsOn: true,
    alarmEventId: null,
    agents,
    transfers: [],
    effects: [],
    pending: [],
  }
}

describe('sceneArt', () => {
  it('has an art frame for every pose of the Orin and of each person', () => {
    for (const pose of ALL_POSES) {
      for (const frame of [0, 1]) {
        expect(placeArtCharacter(character({ isMain: true, pose, frame }), 0, false)).not.toBeNull()
        for (let look = 0; look < ART_PERSON_LOOKS; look += 1) {
          expect(placeArtCharacter(character({ look, pose, frame }), 0, false)).not.toBeNull()
        }
      }
    }
  })

  it('keeps every frame inside the atlas with its foot anchor inside the frame', () => {
    for (const [name, f] of Object.entries(SCENE_ART_FRAMES)) {
      expect(f.w, name).toBeGreaterThan(0)
      expect(f.h, name).toBeGreaterThan(0)
      expect(f.ax, name).toBeGreaterThanOrEqual(0)
      expect(f.ax, name).toBeLessThanOrEqual(f.w)
      expect(f.ay, name).toBeGreaterThan(0)
      expect(f.ay, name).toBeLessThanOrEqual(f.h)
    }
  })

  it('seats a person working at a desk in its chair and keeps the others standing', () => {
    const chair = SCENE_SEATS.terminal!
    const seated = placeArtCharacter(
      character({ pose: 'work', station: 'terminal', x: chair.x, y: chair.y }),
      0,
      false
    )!
    expect(seated.seat).toBe(chair)
    expect(seated.frame).toBe(SCENE_ART_FRAMES['p1.seated'])
    // Shoulders rest just below the top of the backrest, which is drawn over them.
    expect(seated.y + seated.height).toBeCloseTo(chair.backTop + 2, 5)

    const beside = stationSlot('terminal', 1)
    const standing = placeArtCharacter(
      character({ pose: 'work', station: 'terminal', x: beside.x, y: beside.y }),
      0,
      false
    )!
    expect(standing.seat).toBeNull()
    expect(standing.y + standing.height).toBeGreaterThan(beside.y - 1)

    // Waiting for the user at the desk: stands up holding the sign instead of sitting.
    const asking = placeArtCharacter(
      character({ pose: 'raise_hand', station: 'terminal', x: chair.x, y: chair.y }),
      0,
      false
    )!
    expect(asking.seat).toBeNull()
  })

  it('mirrors walking characters facing left but never the sign or the bubbles', () => {
    expect(placeArtCharacter(character({ pose: 'walk', facing: 'left' }), 0, false)!.flip).toBe(true)
    expect(placeArtCharacter(character({ pose: 'walk', facing: 'right' }), 0, false)!.flip).toBe(false)
    expect(placeArtCharacter(character({ pose: 'raise_hand', facing: 'left' }), 0, false)!.flip).toBe(
      false
    )
    expect(placeArtCharacter(character({ pose: 'think', facing: 'left' }), 0, false)!.flip).toBe(false)
  })

  it('puts the first agent of a desk station in the chair and the first resting agent on the sofa', () => {
    for (const station of ['terminal', 'browser', 'writing', 'generic'] as const) {
      const chair = SCENE_SEATS[station]!
      expect(stationSlot(station, 0)).toEqual({ x: chair.x, y: chair.y })
      expect(stationSlot(station, 1)).not.toEqual({ x: chair.x, y: chair.y })
    }
    expect(stationSlot('lounge', 0)).toEqual({ x: SOFA_SPOT.x, y: SOFA_SPOT.y })
  })

  it('gives simultaneous subagents different people and keeps each look when others arrive', () => {
    const anim = createAnimator()
    const orin = agent(MAIN_AGENT_ID, 'Orin')
    const subs = Array.from({ length: ART_PERSON_LOOKS }, (_, idx) =>
      agent(`agent:sub-${idx + 1}`, `Agente ${idx + 1}`)
    )

    syncAnimator(anim, scene([orin, subs[0]!, subs[1]!]), 0, { reducedMotion: true })
    const firstLooks = [anim.agents.get('agent:sub-1')!.look, anim.agents.get('agent:sub-2')!.look]

    syncAnimator(anim, scene([orin, ...subs]), 100, { reducedMotion: true })
    const looks = subs.map((s) => anim.agents.get(s.id)!.look)

    expect(new Set(looks).size).toBe(ART_PERSON_LOOKS)
    expect(looks.slice(0, 2)).toEqual(firstLooks)
  })
})

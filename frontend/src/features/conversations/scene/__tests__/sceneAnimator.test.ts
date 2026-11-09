import { describe, expect, it } from 'vitest'
import {
  createAnimator,
  drainSoundCues,
  hitTest,
  stepAnimator,
  syncAnimator,
} from '../sceneAnimator'
import { DOOR, stationSlot } from '../sceneLayout'
import { MAIN_AGENT_ID, type SceneAgent, type SceneState } from '../sceneTypes'

function makeAgent(overrides: Partial<SceneAgent> & { id: string; name: string }): SceneAgent {
  const isMain = overrides.isMain ?? overrides.id === MAIN_AGENT_ID
  return {
    id: overrides.id,
    isMain,
    name: overrides.name,
    subagentType: overrides.subagentType,
    parentId: overrides.parentId ?? (isMain ? null : MAIN_AGENT_ID),
    depth: overrides.depth ?? (isMain ? 0 : 1),
    station: overrides.station ?? 'terminal',
    pose: overrides.pose ?? 'work',
    status: overrides.status ?? 'working',
    statusLabel: overrides.statusLabel ?? 'trabalhando',
    task: overrides.task ?? 'Implementar módulo',
    currentTool: overrides.currentTool ?? 'Bash',
    lastThought: overrides.lastThought ?? '',
    bubble: overrides.bubble ?? null,
    activities: overrides.activities ?? [],
    durationLabel: overrides.durationLabel ?? '2s',
  }
}

function makeScene(overrides: Partial<SceneState> = {}): SceneState {
  return {
    turnId: overrides.turnId ?? 'turn-1',
    live: overrides.live ?? true,
    waitingUser: overrides.waitingUser ?? false,
    lightsOn: overrides.lightsOn ?? true,
    alarmEventId: overrides.alarmEventId ?? null,
    agents: overrides.agents ?? [
      makeAgent({
        id: MAIN_AGENT_ID,
        isMain: true,
        name: 'Orin',
        station: 'meeting',
        pose: 'idle',
        status: 'working',
        statusLabel: 'coordenando',
      }),
    ],
    transfers: overrides.transfers ?? [],
    effects: overrides.effects ?? [],
    pending: overrides.pending ?? [],
  }
}

// An animator that already showed the scene once (only the Orin), so later syncs animate what is new.
function openedAnimator(nowMs = 0) {
  const anim = createAnimator()
  syncAnimator(anim, makeScene(), nowMs, { reducedMotion: false })
  drainSoundCues(anim)
  return anim
}

describe('sceneAnimator', () => {
  it('spawns a new subagent at the door and walks it to its station over time', () => {
    const anim = openedAnimator()
    const subagent = makeAgent({
      id: 'agent:sub-1',
      name: 'Explorador',
      station: 'files',
      pose: 'work',
    })
    const scene = makeScene({
      agents: [
        makeAgent({ id: MAIN_AGENT_ID, isMain: true, name: 'Orin', station: 'meeting' }),
        subagent,
      ],
    })

    syncAnimator(anim, scene, 1000, { reducedMotion: false })
    const subState = anim.agents.get('agent:sub-1')!
    expect(subState.x).toBe(DOOR.x)
    expect(subState.y).toBe(DOOR.y)
    expect(subState.pose).toBe('walk')

    // Step 1.25 seconds in 250ms increments (<= 1.3s)
    for (let i = 0; i < 5; i += 1) {
      stepAnimator(anim, 250, { reducedMotion: false })
    }

    const expectedTarget = stationSlot('files', 0)
    expect(subState.x).toBeCloseTo(expectedTarget.x, 1)
    expect(subState.y).toBeCloseTo(expectedTarget.y, 1)
    expect(subState.pose).toBe('work')
  })

  it('teleports agents directly to their target slot when reducedMotion is true', () => {
    const anim = createAnimator()
    const scene = makeScene({
      agents: [
        makeAgent({ id: MAIN_AGENT_ID, isMain: true, name: 'Orin', station: 'meeting' }),
        makeAgent({
          id: 'agent:sub-fast',
          name: 'Verificador',
          station: 'writing',
          pose: 'work',
        }),
      ],
    })

    syncAnimator(anim, scene, 0, { reducedMotion: true })
    const sub = anim.agents.get('agent:sub-fast')!
    const expected = stationSlot('writing', 0)
    expect(sub.x).toBe(expected.x)
    expect(sub.y).toBe(expected.y)
    expect(sub.pose).toBe('work')
  })

  it('plays each transfer id only once', () => {
    const anim = openedAnimator()
    const scene = makeScene({
      agents: [
        makeAgent({ id: MAIN_AGENT_ID, isMain: true, name: 'Orin', station: 'meeting' }),
        makeAgent({ id: 'agent:sub-1', name: 'Sub', station: 'terminal' }),
      ],
      transfers: [
        {
          id: 'tr-1',
          kind: 'delegation',
          fromId: MAIN_AGENT_ID,
          toId: 'agent:sub-1',
        },
      ],
    })

    syncAnimator(anim, scene, 100, { reducedMotion: false })
    expect(anim.activeTransfers).toHaveLength(1)

    // Step past transfer duration (900ms)
    stepAnimator(anim, 1000, { reducedMotion: false })
    expect(anim.activeTransfers).toHaveLength(0)

    // Re-syncing with the same transfer id must not replay it
    syncAnimator(anim, scene, 1200, { reducedMotion: false })
    expect(anim.activeTransfers).toHaveLength(0)
  })

  it('finds characters via hitTest and gives priority to the front-most character', () => {
    const anim = createAnimator()
    const scene = makeScene({
      agents: [
        makeAgent({ id: MAIN_AGENT_ID, isMain: true, name: 'Orin', station: 'terminal' }),
        makeAgent({ id: 'agent:sub-2', name: 'Sub 2', station: 'meeting' }),
      ],
    })

    syncAnimator(anim, scene, 0, { reducedMotion: true })
    const orinSlot = stationSlot('terminal', 0)
    expect(hitTest(anim, orinSlot.x, orinSlot.y - 10)).toBe(MAIN_AGENT_ID)
    expect(hitTest(anim, 2, 2)).toBeNull()
  })

  it('walks a completed subagent out through the door after ~4 seconds', () => {
    const anim = createAnimator()
    const initialScene = makeScene({
      agents: [
        makeAgent({ id: MAIN_AGENT_ID, isMain: true, name: 'Orin', station: 'meeting' }),
        makeAgent({
          id: 'agent:done',
          name: 'Executor',
          station: 'terminal',
          status: 'completed',
          statusLabel: 'concluído',
          pose: 'idle',
        }),
      ],
    })

    syncAnimator(anim, initialScene, 0, { reducedMotion: true })
    const doneAgent = anim.agents.get('agent:done')!
    expect(doneAgent.exited).toBe(false)

    // Before 4s it is still in the scene
    stepAnimator(anim, 2000, { reducedMotion: true })
    expect(doneAgent.exited).toBe(false)

    // After 4s total it has exited
    stepAnimator(anim, 2200, { reducedMotion: true })
    expect(doneAgent.exited).toBe(true)
    expect(hitTest(anim, doneAgent.x, doneAgent.y - 8)).toBeNull()
  })

  it('places all agents in the lounge sitting when scene.live is false', () => {
    const anim = createAnimator()
    const restingScene = makeScene({
      live: false,
      lightsOn: false,
      agents: [
        makeAgent({ id: MAIN_AGENT_ID, isMain: true, name: 'Orin', station: 'terminal' }),
        makeAgent({
          id: 'agent:sub-1',
          name: 'Auxiliar',
          station: 'files',
          status: 'completed',
        }),
      ],
    })

    syncAnimator(anim, restingScene, 500, { reducedMotion: true })
    const orin = anim.agents.get(MAIN_AGENT_ID)!
    const sub = anim.agents.get('agent:sub-1')!

    expect(orin.station).toBe('lounge')
    expect(orin.pose).toBe('sit')
    expect(sub.station).toBe('lounge')
    expect(sub.pose).toBe('sit')
    expect(sub.exited).toBe(false)
  })

  it('queues sound cues on walking, typing, transfers, and effects', () => {
    const anim = openedAnimator()
    const scene = makeScene({
      agents: [
        makeAgent({
          id: MAIN_AGENT_ID,
          isMain: true,
          name: 'Orin',
          station: 'terminal',
          pose: 'work',
        }),
        makeAgent({
          id: 'agent:walker',
          name: 'Andarilho',
          station: 'files',
          pose: 'work',
        }),
      ],
      transfers: [
        {
          id: 'tr-sound',
          kind: 'message',
          fromId: MAIN_AGENT_ID,
          toId: 'agent:walker',
        },
      ],
      effects: [
        {
          id: 'fx-check',
          kind: 'check',
          agentId: MAIN_AGENT_ID,
          station: 'terminal',
        },
      ],
    })

    syncAnimator(anim, scene, 10, { reducedMotion: false })
    const cues = drainSoundCues(anim)

    expect(cues).toContain('type')
    expect(cues).toContain('step')
    expect(cues).toContain('envelope')
    expect(cues).toContain('ding')
    expect(drainSoundCues(anim)).toEqual([])
  })

  it('completes the longest map traversal in <= 1.3s of simulated steps', () => {
    const anim = openedAnimator()
    const scene = makeScene({
      agents: [
        makeAgent({ id: MAIN_AGENT_ID, isMain: true, name: 'Orin', station: 'planning' }),
        makeAgent({ id: 'agent:far', name: 'Corredor', station: 'extensions', pose: 'work' }),
      ],
    })
    syncAnimator(anim, scene, 0, { reducedMotion: false })
    const farAgent = anim.agents.get('agent:far')!
    for (let i = 0; i < 13; i += 1) {
      stepAnimator(anim, 100, { reducedMotion: false })
    }
    const expected = stationSlot('extensions', 0)
    expect(farAgent.x).toBeCloseTo(expected.x, 1)
    expect(farAgent.y).toBeCloseTo(expected.y, 1)
    expect(farAgent.pose).toBe('work')
  })

  it('opening the scene does not replay transfers, effects, the alarm, spawns or sounds; later events do play', () => {
    const anim = createAnimator()
    const orin = makeAgent({ id: MAIN_AGENT_ID, isMain: true, name: 'Orin', station: 'meeting' })
    const sub = makeAgent({ id: 'agent:sub-1', name: 'Sub', station: 'files' })
    const opened = makeScene({
      agents: [orin, sub],
      alarmEventId: 'alarm-old',
      transfers: [{ id: 'tr-old', kind: 'delegation', fromId: MAIN_AGENT_ID, toId: 'agent:sub-1' }],
      effects: [{ id: 'fx-old', kind: 'spark', agentId: 'agent:sub-1', station: 'files' }],
    })

    syncAnimator(anim, opened, 0, { reducedMotion: false })
    expect(anim.activeTransfers).toEqual([])
    expect(anim.activeEffects).toEqual([])
    expect(anim.alarmBlinkRemainingMs).toBe(0)
    expect(drainSoundCues(anim)).toEqual([])
    const subState = anim.agents.get('agent:sub-1')!
    expect(subState.x).toBe(stationSlot('files', 0).x)
    expect(subState.y).toBe(stationSlot('files', 0).y)

    syncAnimator(
      anim,
      {
        ...opened,
        alarmEventId: 'alarm-new',
        transfers: [...opened.transfers, { id: 'tr-new', kind: 'return', fromId: 'agent:sub-1', toId: MAIN_AGENT_ID }],
        effects: [...opened.effects, { id: 'fx-new', kind: 'check', agentId: MAIN_AGENT_ID, station: 'meeting' }],
      },
      100,
      { reducedMotion: false },
    )
    expect(anim.activeTransfers.map((tr) => tr.id)).toEqual(['tr-new'])
    expect(anim.activeEffects.map((fx) => fx.id)).toEqual(['fx-new'])
    expect(anim.alarmBlinkRemainingMs).toBeGreaterThan(0)
    expect(drainSoundCues(anim)).toEqual(expect.arrayContaining(['envelope', 'ding']))
  })

  it('keeps whoever asked the user holding the sign after the turn ended', () => {
    const anim = createAnimator()
    syncAnimator(
      anim,
      makeScene({
        live: false,
        waitingUser: true,
        agents: [
          makeAgent({
            id: MAIN_AGENT_ID,
            isMain: true,
            name: 'Orin',
            station: 'files',
            pose: 'raise_hand',
            status: 'waiting_user',
          }),
        ],
      }),
      0,
      { reducedMotion: true },
    )
    const orin = anim.agents.get(MAIN_AGENT_ID)!
    expect(orin.station).toBe('files')
    expect(orin.pose).toBe('raise_hand')
  })
})

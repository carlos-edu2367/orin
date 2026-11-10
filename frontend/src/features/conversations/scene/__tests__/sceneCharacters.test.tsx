import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ApiClient } from '../../../../api/client'
import { parseConversationActivityEvent } from '../../../../api/conversations'
import {
  createSceneCharacter,
  deleteSceneCharacter,
  getSceneCharacters,
  resetSceneCharactersCache,
  seedSceneCharactersCache,
  updateSceneCharacter,
  updateSceneCharacterBindings,
  updateSceneCharacterNames,
  type SceneCharactersConfig,
} from '../../../../api/sceneCharacters'
import { CharactersSection } from '../../../settings/CharactersSection'
import { resolveAgentModalIdentity } from '../SceneAgentModal'
import {
  buildSceneAriaLabel,
  clearCustomSpriteCacheForTest,
  primeCustomSpriteCacheForTest,
  SceneCanvas,
} from '../SceneCanvas'
import {
  computeAgentNameTagMeta,
  createAnimator,
  hitTest,
  syncAnimator,
} from '../sceneAnimator'
import {
  computeFallbackIdleBobOffset,
  mapScenePoseToCustomPose,
  resolveCharacters,
  selectCustomCharacterPose,
} from '../sceneCharacters'
import { FIGURE_HEIGHT } from '../sceneLayout'
import { foldScene } from '../sceneReducer'
import { MAIN_AGENT_ID, type SceneAgent, type SceneState } from '../sceneTypes'
import { SceneView } from '../SceneView'

function makeAgent(overrides: Partial<SceneAgent> & { id: string; name: string }): SceneAgent {
  return {
    isMain: false,
    parentId: MAIN_AGENT_ID,
    depth: 1,
    station: 'terminal',
    pose: 'work',
    status: 'working',
    statusLabel: 'Trabalhando',
    task: 'Implementar recurso',
    currentTool: 'Bash',
    lastThought: '',
    lastSpeech: '',
    bubble: null,
    activities: [],
    durationLabel: '2s',
    ...overrides,
  }
}

const orinAgent: SceneAgent = makeAgent({
  id: MAIN_AGENT_ID,
  isMain: true,
  name: 'Orin',
  parentId: null,
  depth: 0,
})

describe('Personagens da Cena — resolução, etiquetas, poses, cliente API e tela de configurações', () => {
  beforeEach(() => {
    resetSceneCharactersCache()
    clearCustomSpriteCacheForTest()
  })

  afterEach(() => {
    cleanup()
    resetSceneCharactersCache()
    clearCustomSpriteCacheForTest()
    vi.restoreAllMocks()
  })

  describe('1. Resolução pura de personagens (resolveCharacters)', () => {
    const customBento = {
      id: 'char_bento123',
      name: 'Bento',
      poses: {
        idle: '/v1/scene/characters/char_bento123/idle.png',
        work: '/v1/scene/characters/char_bento123/work.png',
      },
    }
    const customKira = {
      id: 'char_kira456',
      name: 'Kira',
      poses: {
        idle: '/v1/scene/characters/char_kira456/idle.png',
      },
    }

    it('segue a prioridade (a) evento > (b) binding > (c) sorteio, preservando Orin intacto', () => {
      const agents: SceneAgent[] = [
        { ...orinAgent, characterName: 'Ignorado' },
        makeAgent({
          id: 'agent:1',
          name: 'claude-1',
          subagentType: 'claude',
          characterName: 'Zico', // (a) vindo do evento vence binding de claude
        }),
        makeAgent({
          id: 'agent:2',
          name: 'explore-1',
          subagentType: 'explore', // (b) binding por subagentType 'explore' -> Bento
        }),
        makeAgent({
          id: 'agent:3',
          name: 'general-1',
          subagentType: 'general', // (c) sorteado do pool
        }),
      ]

      const resolved = resolveCharacters(agents, {
        names: ['Luna'],
        characters: [customBento],
        bindings: {
          claude: customBento.id,
          explore: customBento.id,
        },
      })

      expect(resolved[0]?.name).toBe('Orin')
      expect(resolved[0]?.characterName).toBeUndefined()
      expect(resolved[0]?.customCharacter).toBeUndefined()

      // (a) Evento vence binding
      expect(resolved[1]?.characterName).toBe('Zico')
      expect(resolved[1]?.customCharacter).toBeUndefined()

      // (b) Binding por tipo em minúsculas
      expect(resolved[2]?.characterName).toBe('Bento')
      expect(resolved[2]?.customCharacter).toEqual(customBento)

      // (c) Sorteio usa o único nome livre restante no pool ('Luna', já que 'Bento' foi usado em agent:2)
      expect(resolved[3]?.characterName).toBe('Luna')
    })

    it('mantém estabilidade por hash do id e evita repetição enquanto houver nomes livres no pool', () => {
      const poolNames = ['Alfa', 'Beta', 'Gama', 'Delta']
      const agents: SceneAgent[] = [
        makeAgent({ id: 'agent:a', name: 'sub-1' }),
        makeAgent({ id: 'agent:b', name: 'sub-2' }),
        makeAgent({ id: 'agent:c', name: 'sub-3' }),
        makeAgent({ id: 'agent:d', name: 'sub-4' }),
      ]

      const firstRun = resolveCharacters(agents, { names: poolNames, characters: [], bindings: {} })
      const secondRun = resolveCharacters(agents, { names: poolNames, characters: [], bindings: {} })

      expect(firstRun.map((a) => a.characterName)).toEqual(
        secondRun.map((a) => a.characterName),
      )
      const chosenSet = new Set(firstRun.map((a) => a.characterName))
      expect(chosenSet.size).toBe(4)
    })

    it('associa imagens de personagem criado quando o nome resolvido (por evento ou sorteio) coincide ignorando caixa', () => {
      const agents: SceneAgent[] = [
        makeAgent({
          id: 'agent:ev',
          name: 'claude-1',
          characterName: 'bento', // minúsculas, coincide com 'Bento'
        }),
        makeAgent({
          id: 'agent:pool',
          name: 'plan-1',
        }),
      ]

      const resolved = resolveCharacters(agents, {
        names: [], // Pool terá apenas os nomes dos personagens criados: ['Bento', 'Kira']
        characters: [customBento, customKira],
        bindings: {},
      })

      expect(resolved[0]?.characterName).toBe('bento')
      expect(resolved[0]?.customCharacter).toEqual(customBento)

      // O segundo agente sorteia o único nome livre restante ('Kira') e recebe o customCharacter dela
      expect(resolved[1]?.characterName).toBe('Kira')
      expect(resolved[1]?.customCharacter).toEqual(customKira)
    })

    it('lê character_name no parseConversationActivityEvent e propaga pelo foldScene', () => {
      const parsed = parseConversationActivityEvent(
        {
          event_id: 'ev-del-1',
          cursor: '1',
          event_type: 'agent.message_sent',
          payload: {
            turn_id: 'turn-1',
            agent_id: 'main',
            agent_name: 'claude-1',
            to_agent_id: 'sub-claude-1',
            subagent_type: 'claude',
            character_name: 'Dante',
            content: 'Investigar módulo',
          },
        },
        'turn-1',
      )
      expect(parsed.characterName).toBe('Dante')

      const state = foldScene([parsed], { running: true })
      const sub = state.agents.find((a) => a.id === 'sub-claude-1')
      expect(sub?.name).toBe('claude-1')
      expect(sub?.characterName).toBe('Dante')
    })
  })

  describe('2. Etiqueta em duas linhas, hit-test, aria-label, HUD e Modal', () => {
    it('calcula caixa de 2 linhas (altura 14) quando há characterName distinto e 1 linha (altura 8) caso contrário', () => {
      const singleLineMeta = computeAgentNameTagMeta({
        isMain: false,
        name: 'claude-1',
        x: 100,
        y: 120,
      })
      expect(singleLineMeta.twoLine).toBe(false)
      expect(singleLineMeta.height).toBe(8)
      expect(singleLineMeta.primaryLabel).toBe('claude-1')
      expect(singleLineMeta.secondaryLabel).toBeNull()

      const twoLineMeta = computeAgentNameTagMeta({
        isMain: false,
        name: 'claude-1',
        characterName: 'Luffy',
        x: 100,
        y: 120,
      })
      expect(twoLineMeta.twoLine).toBe(true)
      expect(twoLineMeta.height).toBe(14)
      expect(twoLineMeta.primaryLabel).toBe('Luffy')
      expect(twoLineMeta.secondaryLabel).toBe('claude-1')
    })

    it('desenha etiqueta em duas linhas no SceneCanvas e faz hitTest na caixa da etiqueta de duas linhas', () => {
      const fillTextCalls: Array<{ text: string; x: number; y: number; font: string }> = []
      const fakeCtx: Record<string, unknown> = {
        font: '',
        fillStyle: '',
        strokeStyle: '',
        textAlign: '',
        textBaseline: '',
        imageSmoothingEnabled: false,
        save: vi.fn(),
        restore: vi.fn(),
        setTransform: vi.fn(),
        fillRect: vi.fn(),
        strokeRect: vi.fn(),
        drawImage: vi.fn(),
        beginPath: vi.fn(),
        ellipse: vi.fn(),
        fill: vi.fn(),
        stroke: vi.fn(),
        fillText: vi.fn((text: string, x: number, y: number) => {
          fillTextCalls.push({ text, x, y, font: String(fakeCtx.font) })
        }),
      }
      vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
        () => fakeCtx as unknown as ReturnType<HTMLCanvasElement['getContext']>,
      )

      const sceneWithChar: SceneState = {
        turnId: 'turn-1',
        live: true,
        waitingUser: false,
        lightsOn: true,
        alarmEventId: null,
        agents: [
          orinAgent,
          makeAgent({
            id: 'agent:sub1',
            name: 'claude-1',
            characterName: 'Luffy',
            station: 'files',
            pose: 'work',
          }),
        ],
        transfers: [],
        effects: [],
        pending: [],
      }

      render(<SceneCanvas scene={sceneWithChar} reducedMotion={true} />)

      expect(fillTextCalls.some((c) => c.text === 'Luffy' && c.font === '6px monospace')).toBe(
        true,
      )
      expect(
        fillTextCalls.some((c) => c.text === 'claude-1' && c.font === '4.8px monospace'),
      ).toBe(true)

      const aria = buildSceneAriaLabel(sceneWithChar)
      expect(aria).toContain('Luffy (claude-1)')

      // Hit-test na etiqueta de 2 linhas (abaixo dos pés do agente)
      const anim = createAnimator()
      syncAnimator(anim, sceneWithChar, 1000, { reducedMotion: true })
      const subAnim = anim.agents.get('agent:sub1')!
      const tagMeta = computeAgentNameTagMeta(subAnim)
      // Ponto na metade inferior da etiqueta de 2 linhas (y + 11, fora do hit-box de 1 linha que ia só até y + 8)
      const hitOnSecondLine = hitTest(anim, subAnim.x, tagMeta.y + 11)
      expect(hitOnSecondLine).toBe('agent:sub1')
    })

    it('resolveAgentModalIdentity usa characterName como displayName e name técnico como tag, e SceneHud mostra ambos + link de personalizar', () => {
      const identity = resolveAgentModalIdentity({
        id: 'agent:conv:123',
        isMain: false,
        name: 'claude-1',
        subagentType: 'claude',
        characterName: 'Luffy',
      })
      expect(identity).toEqual({
        displayName: 'Luffy',
        tag: 'claude-1',
      })

      seedSceneCharactersCache({
        names: ['Luffy'],
        defaultNames: ['Luffy'],
        characters: [],
        bindings: {},
      })

      render(
        <SceneView
          running={true}
          events={[
            {
              eventId: 'ev-1',
              cursor: '1',
              type: 'tool.started',
              kind: 'tool',
              state: 'working',
              agentId: 'sub-1',
              agentName: 'claude-1',
              characterName: 'Luffy',
              parentAgentId: 'main',
              toolName: 'read_file',
              summary: 'Lendo arquivo',
            },
          ]}
        />,
      )

      const customizeLink = screen.getByTestId('scene-customize-characters-link')
      expect(customizeLink.textContent).toBe('Personalizar personagens')
      expect(customizeLink.getAttribute('href')).toBe('/settings/characters')

      const agentBtn = screen.getByTestId('scene-agent-button-sub-1')
      expect(agentBtn.textContent).toContain('Luffy')
      expect(screen.getByTestId('scene-agent-tech-tag-sub-1').textContent).toBe('claude-1')
    })
  })

  describe('3. Mapeamento de pose, fallback para idle com balanço leve e desenho de sprite customizado', () => {
    it('mapeia poses corretamente e usa idle com bob leve (desligado em reducedMotion) quando a pose específica está ausente', () => {
      expect(mapScenePoseToCustomPose('think')).toBe('think')
      expect(mapScenePoseToCustomPose('work')).toBe('work')
      expect(mapScenePoseToCustomPose('raise_hand')).toBe('wait')
      expect(mapScenePoseToCustomPose('wait')).toBe('wait')
      expect(mapScenePoseToCustomPose('scared')).toBe('scared')
      expect(mapScenePoseToCustomPose('walk')).toBe('idle')
      expect(mapScenePoseToCustomPose('deliver')).toBe('idle')
      expect(mapScenePoseToCustomPose('sit')).toBe('idle')
      expect(mapScenePoseToCustomPose('idle')).toBe('idle')

      const poses = {
        idle: '/sprites/idle.png',
        work: '/sprites/work.png',
      }

      // Pose presente ('work')
      const present = selectCustomCharacterPose(poses, 'work')
      expect(present).toEqual({
        url: '/sprites/work.png',
        poseKey: 'work',
        isFallbackIdle: false,
      })

      // Pose ausente ('think') cai em 'idle' com isFallbackIdle = true
      const missing = selectCustomCharacterPose(poses, 'think')
      expect(missing).toEqual({
        url: '/sprites/idle.png',
        poseKey: 'idle',
        isFallbackIdle: true,
      })

      // Bob leve de 1px alternando no tempo quando !reducedMotion, e 0 em reducedMotion
      expect(computeFallbackIdleBobOffset('think', true, 0, false)).toBe(0)
      expect(computeFallbackIdleBobOffset('think', true, 350, false)).toBe(-1)
      expect(computeFallbackIdleBobOffset('think', true, 350, true)).toBe(0)
      expect(computeFallbackIdleBobOffset('idle', false, 350, false)).toBe(0)
    })

    it('desenha sprite customizado com altura FIGURE_HEIGHT quando carregado e usa arte padrão enquanto não carrega', () => {
      const drawImageCalls: unknown[][] = []
      const fakeCtx: Record<string, unknown> = {
        font: '',
        fillStyle: '',
        strokeStyle: '',
        textAlign: '',
        textBaseline: '',
        imageSmoothingEnabled: false,
        save: vi.fn(),
        restore: vi.fn(),
        setTransform: vi.fn(),
        translate: vi.fn(),
        scale: vi.fn(),
        fillRect: vi.fn(),
        strokeRect: vi.fn(),
        beginPath: vi.fn(),
        ellipse: vi.fn(),
        fill: vi.fn(),
        stroke: vi.fn(),
        fillText: vi.fn(),
        drawImage: vi.fn((...args: unknown[]) => {
          drawImageCalls.push(args)
        }),
      }
      vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
        () => fakeCtx as unknown as ReturnType<HTMLCanvasElement['getContext']>,
      )

      const customChar = {
        id: 'char_custom1',
        name: 'Pipo',
        poses: {
          idle: '/v1/scene/characters/char_custom1/idle.png',
        },
      }
      const scene: SceneState = {
        turnId: 't1',
        live: true,
        waitingUser: false,
        lightsOn: true,
        alarmEventId: null,
        agents: [
          orinAgent,
          makeAgent({
            id: 'agent:custom',
            name: 'explore-1',
            characterName: 'Pipo',
            customCharacter: customChar,
            pose: 'think',
          }),
        ],
        transfers: [],
        effects: [],
        pending: [],
      }

      // 1. Antes da imagem carregar -> não desenha customImg
      const { unmount } = render(<SceneCanvas scene={scene} reducedMotion={true} />)
      const fakeImg = new Image()
      Object.defineProperty(fakeImg, 'naturalWidth', { value: 22 })
      Object.defineProperty(fakeImg, 'naturalHeight', { value: 44 })
      expect(drawImageCalls.some((args) => args[0] === fakeImg)).toBe(false)
      unmount()

      // 2. Com a imagem no cache -> desenha fakeImg com altura FIGURE_HEIGHT (44)
      drawImageCalls.length = 0
      primeCustomSpriteCacheForTest(customChar.poses.idle, fakeImg)
      render(<SceneCanvas scene={scene} reducedMotion={true} />)
      const customCall = drawImageCalls.find((args) => args[0] === fakeImg)
      expect(customCall).toBeDefined()
      expect(customCall?.[4]).toBe(FIGURE_HEIGHT)
    })
  })

  describe('4. Cliente da API (api/sceneCharacters.ts)', () => {
    it('chama GET, PUT /names, POST multipart, PATCH, DELETE e PUT /bindings corretamente', async () => {
      const requestMock = vi.fn().mockImplementation(async (opts: { path: string; method?: string; parse: (v: unknown) => unknown }) => {
        if (opts.path === '/v1/scene/characters' && (!opts.method || opts.method === 'GET')) {
          return opts.parse({
            names: ['Bento', 'Luna'],
            default_names: ['Bento', 'Luna'],
            characters: [
              {
                id: 'char_1',
                name: 'Bento',
                poses: { idle: '/v1/scene/characters/char_1/idle.png' },
                created_at: '2026-11-10T00:00:00Z',
              },
            ],
            bindings: { explore: 'char_1' },
          })
        }
        if (opts.path === '/v1/scene/characters/names' && opts.method === 'PUT') {
          return opts.parse({ names: ['Kira', 'Nori'] })
        }
        if (opts.path === '/v1/scene/characters/char_1' && opts.method === 'PATCH') {
          return opts.parse({
            character: {
              id: 'char_1',
              name: 'Bento Editado',
              poses: { idle: '/v1/scene/characters/char_1/idle.png' },
              created_at: '2026-11-10T00:00:00Z',
            },
          })
        }
        if (opts.path === '/v1/scene/characters/char_1' && opts.method === 'DELETE') {
          return opts.parse({ deleted: true, id: 'char_1', bindings: {} })
        }
        if (opts.path === '/v1/scene/characters/bindings' && opts.method === 'PUT') {
          return opts.parse({ bindings: { plan: 'char_1' } })
        }
        throw new Error(`Unexpected request: ${opts.method} ${opts.path}`)
      })

      const uploadMock = vi.fn().mockImplementation(async (opts: { path: string; method?: string; body: FormData; parse: (v: unknown) => unknown }) => {
        return opts.parse({
          character: {
            id: 'char_new',
            name: String(opts.body.get('name') ?? 'Novo'),
            poses: { idle: '/v1/scene/characters/char_new/idle.png' },
            created_at: '2026-11-10T00:00:00Z',
          },
        })
      })

      const client = {
        request: requestMock,
        upload: uploadMock,
      } as unknown as ApiClient

      const cfg = await getSceneCharacters(client)
      expect(cfg.names).toEqual(['Bento', 'Luna'])
      expect(cfg.characters[0]?.name).toBe('Bento')
      expect(cfg.bindings).toEqual({ explore: 'char_1' })

      const updatedNames = await updateSceneCharacterNames(client, ['Kira', 'Nori'])
      expect(updatedNames).toEqual(['Kira', 'Nori'])

      const idleFile = new File([new Uint8Array([137, 80, 78, 71])], 'idle.png', {
        type: 'image/png',
      })
      const created = await createSceneCharacter(client, { name: 'Pipo', idle: idleFile })
      expect(created.name).toBe('Pipo')
      expect(uploadMock).toHaveBeenCalledWith(
        expect.objectContaining({
          path: '/v1/scene/characters',
          method: 'POST',
          expectedStatus: 201,
        }),
      )

      const patched = await updateSceneCharacter(client, 'char_1', { name: 'Bento Editado' })
      expect(patched.name).toBe('Bento Editado')

      const bindings = await updateSceneCharacterBindings(client, { plan: 'char_1' })
      expect(bindings).toEqual({ plan: 'char_1' })

      const del = await deleteSceneCharacter(client, 'char_1')
      expect(del.deleted).toBe(true)
    })
  })

  describe('5. Tela de configurações "Personagens" (CharactersSection)', () => {
    it('edita a lista de nomes, rejeita upload de arquivo inválido no cliente e cria personagem chamando a API', async () => {
      const initialConfig: SceneCharactersConfig = {
        names: ['Bento', 'Luna'],
        defaultNames: ['Bento', 'Luna', 'Tico'],
        characters: [],
        bindings: {},
      }
      seedSceneCharactersCache(initialConfig)

      const requestMock = vi.fn().mockImplementation(async (opts: { path: string; method?: string; body?: Record<string, unknown>; parse: (v: unknown) => unknown }) => {
        if (opts.path === '/v1/scene/characters/names' && opts.method === 'PUT') {
          return opts.parse({ names: opts.body?.names ?? [] })
        }
        if (opts.path === '/v1/scene/characters/bindings' && opts.method === 'PUT') {
          return opts.parse({ bindings: opts.body?.bindings ?? {} })
        }
        return opts.parse(initialConfig)
      })

      const uploadMock = vi.fn().mockImplementation(async (opts: { body: FormData; parse: (v: unknown) => unknown }) => {
        return opts.parse({
          character: {
            id: 'char_created_1',
            name: String(opts.body.get('name') ?? 'Herói'),
            poses: {
              idle: '/v1/scene/characters/char_created_1/idle.png',
            },
            created_at: '2026-11-10T00:00:00Z',
          },
        })
      })

      const fakeClient = {
        request: requestMock,
        upload: uploadMock,
      } as unknown as ApiClient

      render(<CharactersSection client={fakeClient} />)

      // 1. Adicionar nome na lista
      const nameInput = screen.getByTestId('characters-name-input')
      fireEvent.change(nameInput, { target: { value: 'Zeca' } })
      fireEvent.click(screen.getByTestId('characters-add-name-button'))

      await waitFor(() => {
        expect(requestMock).toHaveBeenCalledWith(
          expect.objectContaining({
            path: '/v1/scene/characters/names',
            method: 'PUT',
            body: { names: ['Bento', 'Luna', 'Zeca'] },
          }),
        )
      })
      expect(screen.getByTestId('characters-names-list').textContent).toContain('Zeca')

      // 2. Upload com arquivo inválido (texto/jpeg) exibe erro acessível e não chama upload
      const idleInput = screen.getByTestId('character-create-file-idle')
      const badFile = new File(['not an image'], 'malicious.txt', { type: 'text/plain' })
      fireEvent.change(idleInput, { target: { files: [badFile] } })

      const poseErr = screen.getByTestId('character-create-error-idle')
      expect(poseErr.getAttribute('role')).toBe('alert')
      expect(poseErr.textContent).toContain('PNG, WebP ou GIF')
      expect(uploadMock).not.toHaveBeenCalled()

      // 3. Upload com arquivo válido mostra prévia e cria personagem chamando a API
      const validPng = new File([new Uint8Array([137, 80, 78, 71])], 'idle.png', {
        type: 'image/png',
      })
      fireEvent.change(idleInput, { target: { files: [validPng] } })
      expect(screen.queryByTestId('character-create-error-idle')).toBeNull()
      expect(screen.getByTestId('character-create-preview-idle')).toBeTruthy()

      fireEvent.change(screen.getByTestId('character-create-name-input'), {
        target: { value: 'Capitão Pixel' },
      })
      fireEvent.click(screen.getByTestId('character-create-submit'))

      await waitFor(() => {
        expect(uploadMock).toHaveBeenCalledTimes(1)
      })
      expect(screen.getByTestId('character-card-char_created_1').textContent).toContain(
        'Capitão Pixel',
      )
    })
  })
})

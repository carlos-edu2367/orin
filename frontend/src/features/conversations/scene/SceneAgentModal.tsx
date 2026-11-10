import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type JSX,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type RefObject,
} from 'react'
import type { ConversationActivityEvent } from '../activityTypes'
import { sanitizeText } from '../teamActivityModel'
import { SCENE_ART_FRAMES } from './assets/sceneArtManifest'
import {
  SceneAgentActions,
  useSceneAgentControls,
  type SceneAgentControlResult,
  type SceneAgentControlsState,
} from './SceneAgentActions'
import {
  ScenePendingCards,
  type SceneActionHandlers,
} from './ScenePendingCards'
import { getSceneArt, loadSceneArt } from './sceneArt'
import {
  getOrLoadCustomSprite,
  subscribeCustomSpriteLoads,
} from './SceneCanvas'
import { selectCustomCharacterPose } from './sceneCharacters'
import {
  ART_PERSON_LOOKS,
  CHARACTER_H,
  CHARACTER_W,
  SCENE_PALETTE,
  assignAvatars,
  characterMatrix,
  fnv1a32,
  getCharacterSprite,
} from './sceneSprites'
import { STATION_LABELS } from './sceneStations'
import {
  MAIN_AGENT_ID,
  type SceneActivity,
  type SceneAgent,
  type SceneAgentStatus,
  type ScenePose,
  type SceneState,
} from './sceneTypes'

export interface SceneAgentModalIdentityOverrides {
  displayName?: string
  tag?: string
}

export interface SceneAgentModalIdentity {
  displayName: string
  tag: string
}

function formatShortAgentId(rawId: string, isMain: boolean): string {
  const cleaned = sanitizeText(rawId)
  if (!cleaned || isMain || cleaned === MAIN_AGENT_ID) {
    return 'orin'
  }
  const withoutPrefix = cleaned.replace(/^agent:/i, '')
  const parts = withoutPrefix.split(':').filter(Boolean)
  const tail = parts[parts.length - 1] ?? withoutPrefix
  if (tail.length > 16 && /^[0-9a-f-]+$/i.test(tail)) {
    return tail.slice(0, 8)
  }
  return tail || cleaned
}

/**
 * Single point for resolving the modal's displayed character name and technical tag
 * (short id / subagentType), allowing a future step to swap in a persona name cleanly.
 */
export function resolveAgentModalIdentity(
  agent: Pick<SceneAgent, 'id' | 'isMain' | 'name' | 'subagentType'> & {
    characterName?: string
  },
  overrides?: SceneAgentModalIdentityOverrides,
): SceneAgentModalIdentity {
  const overrideName = sanitizeText(overrides?.displayName)
  const overrideTag = sanitizeText(overrides?.tag)
  const isMain = agent.isMain || agent.id === MAIN_AGENT_ID
  const charName = !isMain ? sanitizeText(agent.characterName) : ''
  const techName = sanitizeText(agent.name, isMain ? 'Orin' : 'Subagente')

  const displayName = overrideName || charName || techName

  if (overrideTag) {
    return { displayName, tag: overrideTag }
  }

  if (!isMain && charName && charName.toLowerCase() !== techName.toLowerCase()) {
    return { displayName, tag: techName }
  }

  const subType = sanitizeText(agent.subagentType)
  const shortId = formatShortAgentId(agent.id, agent.isMain)

  let tag: string
  if (isMain) {
    tag = subType ? `${subType} · agent:main` : 'orin · agent:main'
  } else if (subType && shortId && subType.toLowerCase() !== shortId.toLowerCase()) {
    tag = `${subType} · ${shortId}`
  } else {
    tag = subType || shortId || sanitizeText(agent.id)
  }

  return { displayName, tag }
}

const ORIN_PORTRAIT_FRAMES: Record<ScenePose, string> = {
  idle: 'orin.idle',
  walk: 'orin.move',
  work: 'orin.idle',
  think: 'orin.think',
  raise_hand: 'orin.ask',
  scared: 'orin.scared',
  wait: 'orin.idle',
  deliver: 'orin.sparkle',
  sit: 'orin.sleep',
}

const PERSON_PORTRAIT_FRAMES: Record<ScenePose, string> = {
  idle: 'idle',
  walk: 'walk1',
  work: 'idle',
  think: 'think',
  raise_hand: 'ask',
  scared: 'scared',
  wait: 'wait',
  deliver: 'idle',
  sit: 'relax',
}

export function resolveAgentLookInScene(
  scene: SceneState,
  targetAgentId: string,
): number {
  const inUse = new Set<number>()
  for (const ag of scene.agents) {
    if (ag.isMain || ag.id === MAIN_AGENT_ID) {
      if (ag.id === targetAgentId) return 0
      continue
    }
    const preferred = fnv1a32(ag.id) % ART_PERSON_LOOKS
    let chosen = preferred
    for (let i = 0; i < ART_PERSON_LOOKS; i += 1) {
      const candidate = (preferred + i) % ART_PERSON_LOOKS
      if (!inUse.has(candidate)) {
        chosen = candidate
        break
      }
    }
    if (ag.id === targetAgentId) return chosen
    inUse.add(chosen)
  }
  return fnv1a32(targetAgentId) % ART_PERSON_LOOKS
}

export function resolveAgentPortraitFrameKey(
  agent: Pick<SceneAgent, 'id' | 'isMain' | 'pose'>,
  look: number,
): string {
  if (agent.isMain || agent.id === MAIN_AGENT_ID) {
    return ORIN_PORTRAIT_FRAMES[agent.pose] ?? 'orin.idle'
  }
  const personIdx = (Math.abs(look) % ART_PERSON_LOOKS) + 1
  const poseSuffix = PERSON_PORTRAIT_FRAMES[agent.pose] ?? 'idle'
  const candidate = `p${personIdx}.${poseSuffix}`
  return SCENE_ART_FRAMES[candidate] ? candidate : `p${personIdx}.idle`
}

interface StatusBadgeMeta {
  icon: string
  defaultLabel: string
}

const STATUS_BADGE_META: Record<SceneAgentStatus, StatusBadgeMeta> = {
  thinking: { icon: '◆', defaultLabel: 'Pensando' },
  working: { icon: '▶', defaultLabel: 'Trabalhando' },
  waiting_user: { icon: '?', defaultLabel: 'Esperando você' },
  waiting_agent: { icon: '⇄', defaultLabel: 'Esperando um agente' },
  failed: { icon: '✕', defaultLabel: 'Falhou' },
  completed: { icon: '✓', defaultLabel: 'Concluído' },
  resting: { icon: '☾', defaultLabel: 'Descansando' },
  queued: { icon: '…', defaultLabel: 'Na fila' },
  stopped: { icon: '■', defaultLabel: 'parado por você' },
}

export function activityIconFor(activity: SceneActivity): string {
  const probe = `${activity.detail ?? ''} ${activity.label ?? ''}`.toLowerCase()
  if (/\b(bash|powershell|command|terminal|verify|pytest|vitest|npm)\b/.test(probe)) {
    return '>_'
  }
  if (/\b(write|edit|notebook)\b/.test(probe)) {
    return '✎'
  }
  if (/\b(read|list|glob|grep|search|view|pdf)\b/.test(probe)) {
    return '▤'
  }
  if (/\b(fetch|web|browser|preview)\b/.test(probe)) {
    return '◎'
  }
  if (/\b(plan|todo|task)\b/.test(probe)) {
    return '☰'
  }
  if (/\b(agent|subagent|delegat|message|send)\b/.test(probe)) {
    return '⇄'
  }
  return '▸'
}

function getSafe2dContext(
  canvas: HTMLCanvasElement,
): CanvasRenderingContext2D | null {
  if (typeof canvas.getContext !== 'function') return null
  const isJsdom =
    typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent ?? '')
  const isMocked =
    'mock' in canvas.getContext ||
    (typeof HTMLCanvasElement !== 'undefined' &&
      'mock' in HTMLCanvasElement.prototype.getContext)
  if (isJsdom && !isMocked) return null

  try {
    return canvas.getContext('2d')
  } catch {
    return null
  }
}

function readSystemReducedMotion(): boolean {
  if (
    typeof window === 'undefined' ||
    typeof window.matchMedia !== 'function'
  ) {
    return false
  }
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

const PORTRAIT_CANVAS_W = 48
const PORTRAIT_CANVAS_H = 48

export interface SceneAgentModalProps {
  agentId: string | null
  scene: SceneState
  onClose: () => void
  displayName?: string
  tag?: string
  events?: ConversationActivityEvent[]
  actions?: SceneActionHandlers
  openQuestionTurnIds?: ReadonlySet<string>
  onSendToOrin?: (text: string) => Promise<void> | void
  onStopAgent?: (
    agentId: string,
  ) => Promise<SceneAgentControlResult | void> | SceneAgentControlResult | void
  onMessageAgent?: (
    agentId: string,
    message: string,
  ) => Promise<SceneAgentControlResult | void> | SceneAgentControlResult | void
  controls?: SceneAgentControlsState
  onSelectAgent?: (agentId: string | null) => void
  returnFocusRef?: RefObject<HTMLElement | null>
  reducedMotion?: boolean
}

export function SceneAgentModal({
  agentId,
  scene,
  onClose,
  displayName: displayNameOverride,
  tag: tagOverride,
  events,
  actions,
  openQuestionTurnIds,
  onSendToOrin,
  onStopAgent,
  onMessageAgent,
  controls: externalControls,
  onSelectAgent,
  returnFocusRef,
  reducedMotion: reducedMotionProp,
}: SceneAgentModalProps): JSX.Element | null {
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement | null>(null)
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)
  const portraitCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const previousActiveElementRef = useRef<HTMLElement | null>(null)
  // The agent as last seen in the scene, so the modal keeps showing it after it leaves.
  const [lastKnownAgent, setLastKnownAgent] = useState<SceneAgent | null>(null)

  const [artReady, setArtReady] = useState<boolean>(() => getSceneArt() !== null)
  const [customSpriteRev, setCustomSpriteRev] = useState<number>(0)
  const [systemReducedMotion] = useState<boolean>(() =>
    readSystemReducedMotion(),
  )
  const effectiveReducedMotion =
    typeof reducedMotionProp === 'boolean'
      ? reducedMotionProp
      : systemReducedMotion

  const internalControls = useSceneAgentControls({
    onStopAgent,
    onMessageAgent,
    onSendToOrin,
  })
  const controls = externalControls ?? internalControls

  const liveAgent = useMemo(() => {
    if (!agentId) return undefined
    return scene.agents.find((ag) => ag.id === agentId)
  }, [scene.agents, agentId])

  // Derived state updated during render (React's pattern for remembering a previous value).
  if (liveAgent && liveAgent !== lastKnownAgent) {
    setLastKnownAgent(liveAgent)
  } else if (!agentId && lastKnownAgent !== null) {
    setLastKnownAgent(null)
  }

  useEffect(() => {
    if (!agentId || artReady) return undefined
    return loadSceneArt(() => setArtReady(true))
  }, [agentId, artReady])

  useEffect(() => {
    if (!agentId) return undefined
    return subscribeCustomSpriteLoads(() => {
      setCustomSpriteRev((prev) => prev + 1)
    })
  }, [agentId])

  const fallbackAgent: SceneAgent | null = useMemo(() => {
    if (!agentId) return null
    if (liveAgent) return liveAgent
    if (lastKnownAgent && lastKnownAgent.id === agentId) {
      return lastKnownAgent
    }
    const isMain = agentId === MAIN_AGENT_ID
    return {
      id: agentId,
      isMain,
      name: isMain ? 'Orin' : agentId,
      parentId: isMain ? null : MAIN_AGENT_ID,
      depth: isMain ? 0 : 1,
      station: 'lounge',
      pose: 'idle',
      status: 'resting',
      statusLabel: 'Fora da cena',
      task: '',
      currentTool: null,
      lastThought: '',
      lastSpeech: '',
      bubble: null,
      activities: [],
      durationLabel: '',
    }
  }, [agentId, liveAgent, lastKnownAgent])

  const look = useMemo(() => {
    if (!fallbackAgent) return 0
    return resolveAgentLookInScene(scene, fallbackAgent.id)
  }, [scene, fallbackAgent])

  const portraitFrameKey = useMemo(() => {
    if (!fallbackAgent) return 'orin.idle'
    return resolveAgentPortraitFrameKey(fallbackAgent, look)
  }, [fallbackAgent, look])

  // Draw character portrait on the small pixel-art canvas
  useEffect(() => {
    if (!agentId || !fallbackAgent) return
    const canvas = portraitCanvasRef.current
    if (!canvas) return
    const ctx = getSafe2dContext(canvas)
    if (!ctx) return

    ctx.clearRect(0, 0, PORTRAIT_CANVAS_W, PORTRAIT_CANVAS_H)
    ctx.imageSmoothingEnabled = false

    if (!fallbackAgent.isMain && fallbackAgent.customCharacter?.poses?.idle) {
      const selected = selectCustomCharacterPose(
        fallbackAgent.customCharacter.poses,
        fallbackAgent.pose,
      )
      const customImg =
        getOrLoadCustomSprite(selected.url) ??
        getOrLoadCustomSprite(fallbackAgent.customCharacter.poses.idle)
      if (customImg) {
        const pad = 4
        const maxW = PORTRAIT_CANVAS_W - pad * 2
        const maxH = PORTRAIT_CANVAS_H - pad * 2
        const imgW = customImg.naturalWidth || customImg.width || 28
        const imgH = customImg.naturalHeight || customImg.height || 44
        const scale = Math.min(maxW / imgW, maxH / imgH)
        const drawW = Math.max(1, Math.round(imgW * scale))
        const drawH = Math.max(1, Math.round(imgH * scale))
        const drawX = Math.round((PORTRAIT_CANVAS_W - drawW) / 2)
        const drawY = Math.round((PORTRAIT_CANVAS_H - drawH) / 2)
        ctx.drawImage(customImg, drawX, drawY, drawW, drawH)
        return
      }
    }

    const art = getSceneArt()
    const artFrame = SCENE_ART_FRAMES[portraitFrameKey]
    if (art && artFrame) {
      const pad = 4
      const maxW = PORTRAIT_CANVAS_W - pad * 2
      const maxH = PORTRAIT_CANVAS_H - pad * 2
      const scale = Math.min(maxW / artFrame.w, maxH / artFrame.h)
      const drawW = Math.max(1, Math.round(artFrame.w * scale))
      const drawH = Math.max(1, Math.round(artFrame.h * scale))
      const drawX = Math.round((PORTRAIT_CANVAS_W - drawW) / 2)
      const drawY = Math.round((PORTRAIT_CANVAS_H - drawH) / 2)
      ctx.drawImage(
        art.characters,
        artFrame.x,
        artFrame.y,
        artFrame.w,
        artFrame.h,
        drawX,
        drawY,
        drawW,
        drawH,
      )
      return
    }

    const subagentIds = scene.agents
      .filter((a) => !a.isMain && a.id !== MAIN_AGENT_ID)
      .map((a) => a.id)
    if (
      !fallbackAgent.isMain &&
      fallbackAgent.id !== MAIN_AGENT_ID &&
      !subagentIds.includes(fallbackAgent.id)
    ) {
      subagentIds.push(fallbackAgent.id)
    }
    const avatarMap = assignAvatars(subagentIds)
    const who =
      fallbackAgent.isMain || fallbackAgent.id === MAIN_AGENT_ID
        ? 'orin'
        : (avatarMap.get(fallbackAgent.id) ?? avatarMap.values().next().value!)

    const sprite = getCharacterSprite(who, fallbackAgent.pose, 0)
    const scale = 2
    const drawW = CHARACTER_W * scale
    const drawH = CHARACTER_H * scale
    const offsetX = Math.round((PORTRAIT_CANVAS_W - drawW) / 2)
    const offsetY = Math.round((PORTRAIT_CANVAS_H - drawH) / 2)

    if (sprite) {
      ctx.drawImage(sprite, offsetX, offsetY, drawW, drawH)
      return
    }

    const { matrix, colors } = characterMatrix(who, fallbackAgent.pose, 0)
    for (let y = 0; y < matrix.length; y += 1) {
      const row = matrix[y]!
      for (let x = 0; x < row.length; x += 1) {
        const key = row[x]!
        if (key === '.') continue
        const color = colors[key]
        if (!color) continue
        ctx.fillStyle = color
        ctx.fillRect(offsetX + x * scale, offsetY + y * scale, scale, scale)
      }
    }
  }, [agentId, fallbackAgent, portraitFrameKey, scene.agents, artReady, customSpriteRev])

  const restoreFocusToCanvas = () => {
    const target =
      returnFocusRef?.current ??
      (typeof document !== 'undefined'
        ? document.querySelector<HTMLElement>('[data-testid="scene-canvas"]')
        : null) ??
      previousActiveElementRef.current

    if (target && typeof target.focus === 'function') {
      target.focus()
    }
  }

  const handleClose = () => {
    onClose()
    restoreFocusToCanvas()
  }

  // Focus modal on open
  useEffect(() => {
    if (!agentId) return
    if (typeof document !== 'undefined' && document.activeElement instanceof HTMLElement) {
      previousActiveElementRef.current = document.activeElement
    }
    if (closeButtonRef.current) {
      closeButtonRef.current.focus()
    } else if (dialogRef.current) {
      dialogRef.current.focus()
    }
  }, [agentId])

  if (!agentId || !fallbackAgent) {
    return null
  }

  const identity = resolveAgentModalIdentity(fallbackAgent, {
    displayName: displayNameOverride,
    tag: tagOverride,
  })

  const statusMeta = STATUS_BADGE_META[fallbackAgent.status] ?? STATUS_BADGE_META.resting
  const isStopping = controls.stoppingAgentId === fallbackAgent.id
  const statusText = !liveAgent
    ? 'Não está mais na cena'
    : isStopping
      ? 'parando…'
      : sanitizeText(fallbackAgent.statusLabel) || statusMeta.defaultLabel
  const stationText =
    STATION_LABELS[fallbackAgent.station] ?? sanitizeText(fallbackAgent.station)
  const durationText = sanitizeText(fallbackAgent.durationLabel)

  const fullSpeech = (() => {
    const bubbleSpeech =
      fallbackAgent.bubble &&
      (fallbackAgent.bubble.kind === 'speech' ||
        fallbackAgent.bubble.kind === 'error')
        ? sanitizeText(
            fallbackAgent.bubble.fullText || fallbackAgent.bubble.text,
          )
        : ''
    const savedSpeech = sanitizeText(fallbackAgent.lastSpeech)
    if (bubbleSpeech.length > savedSpeech.length) return bubbleSpeech
    return savedSpeech || bubbleSpeech
  })()

  const fullThought = (() => {
    const bubbleThought =
      fallbackAgent.bubble && fallbackAgent.bubble.kind === 'thought'
        ? sanitizeText(
            fallbackAgent.bubble.fullText || fallbackAgent.bubble.text,
          )
        : ''
    const savedThought = sanitizeText(fallbackAgent.lastThought)
    if (bubbleThought.length > savedThought.length) return bubbleThought
    return savedThought || bubbleThought
  })()

  const diaryItems = liveAgent ? [...liveAgent.activities].reverse() : []
  const agentPending = liveAgent
    ? scene.pending.filter((item) => item.agentId === liveAgent.id)
    : []

  const handleDialogKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      handleClose()
      return
    }

    if (event.key === 'Tab' && dialogRef.current) {
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
        ),
      )
      if (focusable.length === 0) {
        event.preventDefault()
        dialogRef.current.focus()
        return
      }
      const first = focusable[0]!
      const last = focusable[focusable.length - 1]!
      const active =
        typeof document !== 'undefined' ? document.activeElement : null

      if (event.shiftKey) {
        if (active === first || !dialogRef.current.contains(active)) {
          event.preventDefault()
          last.focus()
        }
      } else if (active === last || !dialogRef.current.contains(active)) {
        event.preventDefault()
        first.focus()
      }
    }
  }

  const handleBackdropClick = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) {
      handleClose()
    }
  }

  const modalClassName = [
    'scene-agent-modal',
    effectiveReducedMotion ? 'scene-agent-modal--reduced-motion' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className="scene-agent-modal-backdrop"
      data-testid="scene-agent-modal-backdrop"
      onClick={handleBackdropClick}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={modalClassName}
        data-testid="scene-agent-modal"
        onKeyDown={handleDialogKeyDown}
      >
        {/* Pixel-art Header */}
        <header className="scene-agent-modal__header">
          <div className="scene-agent-modal__portrait-frame">
            <canvas
              ref={portraitCanvasRef}
              width={PORTRAIT_CANVAS_W}
              height={PORTRAIT_CANVAS_H}
              className="scene-agent-modal__portrait-canvas"
              data-testid="scene-agent-modal-portrait"
              data-frame={portraitFrameKey}
              data-look={String(look)}
              aria-hidden="true"
              style={{
                backgroundColor: SCENE_PALETTE.ink,
              }}
            />
          </div>

          <div className="scene-agent-modal__identity">
            <h2
              id={titleId}
              className="scene-agent-modal__name"
              data-testid="scene-agent-modal-name"
            >
              {identity.displayName}
            </h2>
            <span
              className="scene-agent-modal__tag"
              data-testid="scene-agent-modal-tag"
            >
              {identity.tag}
            </span>
          </div>

          <button
            ref={closeButtonRef}
            type="button"
            className="scene-agent-modal__close"
            data-testid="scene-agent-modal-close"
            aria-label={`Fechar janela de ${identity.displayName}`}
            onClick={handleClose}
          >
            ×
          </button>
        </header>

        {/* Game-style Status Bar */}
        <div
          className="scene-agent-modal__status-bar"
          data-testid="scene-agent-modal-status-bar"
        >
          <span
            className={`scene-agent-modal__status-pill scene-agent-modal__status-pill--${liveAgent ? fallbackAgent.status : 'missing'}`}
            data-testid="scene-agent-modal-status"
          >
            <span
              className="scene-agent-modal__status-icon"
              aria-hidden="true"
            >
              {liveAgent ? statusMeta.icon : '◌'}
            </span>
            <span>{statusText}</span>
          </span>

          {liveAgent ? (
            <span
              className="scene-agent-modal__station"
              data-testid="scene-agent-modal-station"
            >
              {stationText}
            </span>
          ) : null}

          {durationText ? (
            <span
              className="scene-agent-modal__duration"
              data-testid="scene-agent-modal-duration"
            >
              ⏱ {durationText}
            </span>
          ) : null}
        </div>

        {/* Scrollable Body */}
        <div className="scene-agent-modal__body">
          {!liveAgent ? (
            <div
              className="scene-agent-modal__missing"
              data-testid="scene-agent-modal-missing"
              role="status"
            >
              Este personagem não está mais na cena.
            </div>
          ) : (
            <>
              {/* Pending user decisions / approvals for this agent */}
              {agentPending.length > 0 ? (
                <div
                  className="scene-agent-modal__pending"
                  data-testid="scene-agent-modal-pending"
                >
                  <ScenePendingCards
                    pending={agentPending}
                    agents={scene.agents}
                    events={events}
                    actions={actions}
                    openQuestionTurnIds={openQuestionTurnIds}
                    onSelectAgent={onSelectAgent ?? (() => undefined)}
                    variant="modal"
                    testIdPrefix="scene-agent-modal-pending"
                    hideSelectButton={true}
                  />
                </div>
              ) : null}

              {/* Shared Agent Controls (Stop / Message) */}
              <SceneAgentActions
                agent={liveAgent}
                controls={controls}
                onStopAgent={onStopAgent}
                onMessageAgent={onMessageAgent}
                onSendToOrin={onSendToOrin}
                testIdPrefix="scene-agent"
                variant="modal"
              />

              {/* Mission & Current Tool */}
              <div className="scene-agent-modal__grid">
                <section
                  className="scene-agent-modal__section"
                  aria-label="Missão"
                >
                  <h3 className="scene-agent-modal__section-title">Missão</h3>
                  <p
                    className="scene-agent-modal__field-value"
                    data-testid="scene-agent-modal-mission"
                  >
                    {sanitizeText(liveAgent.task) || 'Nenhuma missão ativa'}
                  </p>
                </section>

                <section
                  className="scene-agent-modal__section"
                  aria-label="Ferramenta agora"
                >
                  <h3 className="scene-agent-modal__section-title">
                    Ferramenta agora
                  </h3>
                  <p
                    className="scene-agent-modal__field-value scene-agent-modal__field-value--mono"
                    data-testid="scene-agent-modal-tool"
                  >
                    {liveAgent.currentTool
                      ? sanitizeText(liveAgent.currentTool)
                      : 'Nenhuma'}
                  </p>
                </section>
              </div>

              {/* Speech (lastSpeech completo, rolável) */}
              <section
                className="scene-agent-modal__section"
                aria-label="Falou"
              >
                <h3 className="scene-agent-modal__section-title">Falou</h3>
                <div
                  className="scene-agent-modal__scroll-box"
                  data-testid="scene-agent-modal-speech"
                >
                  {fullSpeech ? (
                    <p className="scene-agent-modal__text">{fullSpeech}</p>
                  ) : (
                    <p className="scene-agent-modal__empty">
                      Nenhuma fala registrada.
                    </p>
                  )}
                </div>
              </section>

              {/* Thought (lastThought completo, rolável) */}
              <section
                className="scene-agent-modal__section"
                aria-label="Pensando"
              >
                <h3 className="scene-agent-modal__section-title">Pensando</h3>
                <div
                  className="scene-agent-modal__scroll-box"
                  data-testid="scene-agent-modal-thought"
                >
                  {fullThought ? (
                    <p className="scene-agent-modal__text">{fullThought}</p>
                  ) : (
                    <p className="scene-agent-modal__empty">
                      Nenhum pensamento registrado.
                    </p>
                  )}
                </div>
              </section>

              {/* Diary (activities timeline, newest first, scrollable) */}
              <section
                className="scene-agent-modal__section"
                aria-label="Diário"
              >
                <h3 className="scene-agent-modal__section-title">Diário</h3>
                <div
                  className="scene-agent-modal__scroll-box scene-agent-modal__diary-box"
                  data-testid="scene-agent-modal-diary"
                >
                  {diaryItems.length === 0 ? (
                    <p className="scene-agent-modal__empty">
                      Nenhuma atividade registrada no diário.
                    </p>
                  ) : (
                    <ol
                      className="scene-agent-modal__diary-list"
                      data-testid="scene-agent-modal-diary-list"
                    >
                      {diaryItems.map((act) => {
                        const cleanLabel = sanitizeText(act.label)
                        const cleanDetail = sanitizeText(act.detail)
                        const cleanTime = sanitizeText(act.timestamp)
                        const icon = activityIconFor(act)
                        return (
                          <li
                            key={act.id}
                            className="scene-agent-modal__diary-item"
                            data-testid={`scene-agent-modal-diary-item-${sanitizeText(act.id)}`}
                          >
                            <span
                              className="scene-agent-modal__diary-icon"
                              aria-hidden="true"
                            >
                              {icon}
                            </span>
                            <div className="scene-agent-modal__diary-content">
                              <span className="scene-agent-modal__diary-label">
                                {cleanLabel}
                              </span>
                              {cleanDetail && cleanDetail !== cleanLabel ? (
                                <span className="scene-agent-modal__diary-detail">
                                  {cleanDetail}
                                </span>
                              ) : null}
                            </div>
                            {cleanTime ? (
                              <time className="scene-agent-modal__diary-time">
                                {cleanTime}
                              </time>
                            ) : null}
                          </li>
                        )
                      })}
                    </ol>
                  )}
                </div>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default SceneAgentModal

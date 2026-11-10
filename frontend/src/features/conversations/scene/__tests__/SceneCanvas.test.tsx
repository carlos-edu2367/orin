import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { bubbleDisplayText, SceneCanvas } from '../SceneCanvas'
import { MAIN_AGENT_ID, type SceneState } from '../sceneTypes'

const sampleScene: SceneState = {
  turnId: 'turn-42',
  live: true,
  waitingUser: false,
  lightsOn: true,
  alarmEventId: null,
  agents: [
    {
      id: MAIN_AGENT_ID,
      isMain: true,
      name: 'Orin',
      parentId: null,
      depth: 0,
      station: 'terminal',
      pose: 'work',
      status: 'working',
      statusLabel: 'trabalhando',
      task: 'Rodar verificações',
      currentTool: 'Bash',
      lastThought: 'Vou rodar os testes.',
      lastSpeech: '',
      bubble: {
        kind: 'thought',
        text: 'Rodando testes...',
        eventId: 'evt-b1',
      },
      activities: [],
      durationLabel: '4s',
    },
    {
      id: 'agent:pesquisador',
      isMain: false,
      name: 'Pesquisador',
      subagentType: 'Explore',
      parentId: MAIN_AGENT_ID,
      depth: 1,
      station: 'files',
      pose: 'think',
      status: 'thinking',
      statusLabel: 'pensando',
      task: 'Inspecionar arquivos',
      currentTool: 'Read',
      lastThought: 'Lendo especificação.',
      lastSpeech: '',
      bubble: null,
      activities: [],
      durationLabel: '2s',
    },
    {
      id: 'agent:revisor',
      isMain: false,
      name: 'Revisor',
      subagentType: 'verifier',
      parentId: MAIN_AGENT_ID,
      depth: 1,
      station: 'meeting',
      pose: 'idle',
      status: 'completed',
      statusLabel: 'concluído',
      task: 'Revisar entrega',
      currentTool: null,
      lastThought: 'Tudo certo.',
      lastSpeech: '',
      bubble: null,
      activities: [],
      durationLabel: '3s',
    },
  ],
  transfers: [],
  effects: [],
  pending: [],
}

// A 2D context that accepts any call, so the real draw path runs under jsdom.
function makeFakeContext(): CanvasRenderingContext2D {
  const store: Record<string | symbol, unknown> = {}
  return new Proxy(store, {
    get(target, prop) {
      if (prop in target) return target[prop]
      if (prop === 'measureText') return () => ({ width: 10 })
      return () => undefined
    },
    set(target, prop, value) {
      target[prop] = value
      return true
    },
  }) as unknown as CanvasRenderingContext2D
}

function setDocumentHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
  document.dispatchEvent(new Event('visibilitychange'))
}

describe('SceneCanvas', () => {
  const originalMatchMedia = window.matchMedia

  afterEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: originalMatchMedia,
    })
  })

  describe('animation loop', () => {
    let rafSpy: ReturnType<typeof vi.spyOn>
    let cancelSpy: ReturnType<typeof vi.spyOn>
    let nextRafId = 0
    const disconnects: Array<ReturnType<typeof vi.fn>> = []
    const originalIO = window.IntersectionObserver

    beforeEach(() => {
      nextRafId = 0
      disconnects.length = 0
      vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
        () => makeFakeContext() as unknown as ReturnType<HTMLCanvasElement['getContext']>,
      )
      rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => {
        nextRafId += 1
        return nextRafId
      })
      cancelSpy = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined)
      class MockIntersectionObserver {
        disconnect = vi.fn()
        constructor() {
          disconnects.push(this.disconnect)
        }
        observe() {}
        unobserve() {}
        takeRecords() {
          return []
        }
      }
      Object.defineProperty(window, 'IntersectionObserver', {
        configurable: true,
        writable: true,
        value: MockIntersectionObserver,
      })
    })

    afterEach(() => {
      Reflect.deleteProperty(document, 'hidden')
      Object.defineProperty(window, 'IntersectionObserver', {
        configurable: true,
        writable: true,
        value: originalIO,
      })
      vi.restoreAllMocks()
    })

    it('pauses while the document is hidden, resumes when visible and cleans up on unmount', () => {
      const { unmount } = render(<SceneCanvas scene={sampleScene} />)
      expect(rafSpy).toHaveBeenCalled()
      const runningId = nextRafId

      act(() => setDocumentHidden(true))
      expect(cancelSpy).toHaveBeenCalledWith(runningId)
      const callsWhileHidden = rafSpy.mock.calls.length

      act(() => setDocumentHidden(true))
      expect(rafSpy.mock.calls.length).toBe(callsWhileHidden)

      act(() => setDocumentHidden(false))
      expect(rafSpy.mock.calls.length).toBeGreaterThan(callsWhileHidden)
      const resumedId = nextRafId

      unmount()
      expect(cancelSpy).toHaveBeenCalledWith(resumedId)
      expect(disconnects.length).toBeGreaterThan(0)
      for (const disconnect of disconnects) expect(disconnect).toHaveBeenCalled()
    })
  })

  it('renders a canvas with role="img" and pt-BR aria-label containing agent names and statusLabels', () => {
    render(<SceneCanvas scene={sampleScene} />)

    const frame = screen.getByTestId('scene-canvas-frame')
    expect(frame).toBeInTheDocument()

    const canvas = screen.getByRole('img')
    expect(canvas).toHaveAttribute('data-testid', 'scene-canvas')

    const label = canvas.getAttribute('aria-label') ?? ''
    expect(label).toContain('Cena do escritório')
    expect(label).toContain('Orin')
    expect(label).toContain('trabalhando')
    expect(label).toContain('Mesa do terminal')
    expect(label).toContain('2 subagentes')
    expect(label).toContain('Pesquisador')
    expect(label).toContain('pensando')
    expect(label).toContain('Revisor')
    expect(label).toContain('concluído')
  })

  it('renders without throwing when matchMedia reports prefers-reduced-motion: reduce', () => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query.includes('reduce'),
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    })

    const onSelect = vi.fn()
    expect(() =>
      render(<SceneCanvas scene={sampleScene} onSelectAgent={onSelect} />)
    ).not.toThrow()

    const canvas = screen.getByTestId('scene-canvas')
    fireEvent.click(canvas, { clientX: 112, clientY: 210 })
    expect(onSelect).toHaveBeenCalledWith(MAIN_AGENT_ID)
  })

  it('scales canvas to the largest integer factor that fits container width regardless of container min-height', () => {
    const origRO = window.ResizeObserver
    let roCallback: ((entries: ResizeObserverEntry[]) => void) | null = null
    class MockResizeObserver {
      constructor(cb: (entries: ResizeObserverEntry[]) => void) {
        roCallback = cb
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    Object.defineProperty(window, 'ResizeObserver', {
      configurable: true,
      writable: true,
      value: MockResizeObserver,
    })

    try {
      render(<SceneCanvas scene={sampleScene} />)
      const canvas = screen.getByTestId('scene-canvas')
      expect(roCallback).not.toBeNull()

      // Simulate a stacked container of width 680px and height 240px -> should scale x2 (640x384), not x1
      act(() => {
        roCallback!([
          {
            contentRect: { width: 680, height: 240 } as DOMRectReadOnly,
          } as ResizeObserverEntry,
        ])
      })
      expect(canvas).toHaveStyle({ width: '640px', height: '384px' })
    } finally {
      Object.defineProperty(window, 'ResizeObserver', {
        configurable: true,
        writable: true,
        value: origRO,
      })
    }
  })

  describe('bubbleDisplayText', () => {
    it('mantém o começo de um texto longo e coloca reticências apenas no fim', () => {
      const longSpeech = 'Vou analisar a estrutura do projeto e rodar os testes da cena agora mesmo.'
      const displayed = bubbleDisplayText(longSpeech)
      expect(displayed.length).toBeLessThanOrEqual(26)
      expect(displayed.startsWith('Vou analisar a estrutura')).toBe(true)
      expect(displayed.endsWith('…')).toBe(true)
      expect(displayed.startsWith('…')).toBe(false)
    })

    it('não duplica reticências nas duas pontas quando recebe pensamento com "…" inicial', () => {
      const longThought = '…verificando cada transição de estado na máquina pura da cena pixel art'
      const displayed = bubbleDisplayText(longThought)
      expect(displayed.length).toBeLessThanOrEqual(26)
      expect(displayed.startsWith('…') && displayed.endsWith('…')).toBe(false)
      expect(displayed).toBe('…na pura da cena pixel art')
    })
  })
})

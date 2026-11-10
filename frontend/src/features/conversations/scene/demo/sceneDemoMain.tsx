import React, { useEffect, useMemo, useState, type JSX } from 'react'
import ReactDOM from 'react-dom/client'
import { initThemeDom, ThemeProvider } from '../../../../app/ThemeContext'
import '../../../../styles/theme.css'
import '../../../../styles/index.css'
import '../../../../styles/agentos.css'
import '../../../../styles/live.css'
import { DEFAULT_FALLBACK_CHARACTER_NAMES, seedSceneCharactersCache } from '../../../../api/sceneCharacters'
import { SceneView } from '../SceneView'
import {
  buildDemoSequence,
  buildTwelveAgentsFrame,
  type SceneDemoFrame,
} from './sceneDemoScript'

initThemeDom()
// The demo runs without a backend: give it the default cast the server would serve.
seedSceneCharactersCache({
  names: [...DEFAULT_FALLBACK_CHARACTER_NAMES],
  defaultNames: [...DEFAULT_FALLBACK_CHARACTER_NAMES],
  characters: [],
  bindings: {},
})

const STEP_INTERVAL_MS = 1800

export function SceneDemoApp(): JSX.Element {
  const [turnNumber, setTurnNumber] = useState(1)
  const [stepIndex, setStepIndex] = useState(0)
  const [paused, setPaused] = useState(false)
  const [stressFrame, setStressFrame] = useState<SceneDemoFrame | null>(null)

  const sequence = useMemo(() => buildDemoSequence(turnNumber), [turnNumber])

  useEffect(() => {
    if (paused || stressFrame !== null) return
    const timer = window.setTimeout(() => {
      if (stepIndex + 1 < sequence.length) {
        setStepIndex((prev) => prev + 1)
      } else {
        setTurnNumber((prev) => prev + 1)
        setStepIndex(0)
      }
    }, STEP_INTERVAL_MS)
    return () => window.clearTimeout(timer)
  }, [paused, sequence.length, stepIndex, stressFrame])

  const activeFrame: SceneDemoFrame =
    stressFrame ?? sequence[Math.min(stepIndex, sequence.length - 1)]!

  const handleRestart = () => {
    setStressFrame(null)
    setPaused(false)
    setTurnNumber((prev) => prev + 1)
    setStepIndex(0)
  }

  const handleTogglePause = () => {
    setPaused((prev) => !prev)
  }

  const handleTwelveAgents = () => {
    setStressFrame(buildTwelveAgentsFrame(turnNumber))
  }

  const handleEndTurn = () => {
    setStressFrame(null)
    setStepIndex(sequence.length - 1)
  }

  return (
    <main
      style={{
        minHeight: '100vh',
        background: 'var(--orin-page-bg, #101014)',
        color: 'var(--orin-text-primary, #F4F3F8)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '12px',
          flexWrap: 'wrap',
          maxWidth: '1280px',
          margin: '0 auto',
          padding: '16px 24px 0',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <strong style={{ fontSize: '14px' }}>
            Demo da Cena (Turno #{turnNumber})
          </strong>
          <span
            style={{
              fontSize: '12px',
              color: 'var(--orin-text-secondary, #9A98A6)',
            }}
          >
            {stressFrame
              ? 'Modo 12 agentes simultâneos'
              : `Passo ${stepIndex + 1} de ${sequence.length}`}
          </span>
        </div>

        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button
            type="button"
            className="scene-hud__btn"
            onClick={handleRestart}
          >
            Reiniciar
          </button>
          <button
            type="button"
            className={
              paused
                ? 'scene-hud__btn scene-hud__btn--active'
                : 'scene-hud__btn'
            }
            aria-pressed={paused}
            onClick={handleTogglePause}
          >
            Pausar demo
          </button>
          <button
            type="button"
            className={
              stressFrame
                ? 'scene-hud__btn scene-hud__btn--active'
                : 'scene-hud__btn'
            }
            onClick={handleTwelveAgents}
          >
            12 agentes
          </button>
          <button
            type="button"
            className="scene-hud__btn"
            onClick={handleEndTurn}
          >
            Encerrar turno
          </button>
        </div>
      </div>

      <SceneView
        events={activeFrame.events}
        todos={activeFrame.todos}
        running={activeFrame.running}
        paused={paused}
        onPauseTeam={handleTogglePause}
      />
    </main>
  )
}

const rootEl = document.getElementById('root')
if (rootEl) {
  ReactDOM.createRoot(rootEl).render(
    <React.StrictMode>
      <ThemeProvider>
        <SceneDemoApp />
      </ThemeProvider>
    </React.StrictMode>,
  )
}

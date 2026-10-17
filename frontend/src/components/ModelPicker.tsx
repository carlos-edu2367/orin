import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import type { ProviderModel, ProviderName } from '../api/providers'

/**
 * Orin's loop is tool-driven, so a model that cannot call tools cannot run a
 * turn. Ollama is the first provider to report this per model; a provider
 * that reports no capabilities at all says nothing either way, and its
 * models are left unmarked rather than wrongly accused.
 */
function lacksToolSupport(model: ProviderModel): boolean {
  return model.capabilities.length > 0 && !model.capabilities.includes('tools')
}

export type ModelPickerProps = {
  providers: ProviderName[]
  provider: ProviderName
  onProviderChange: (provider: ProviderName) => void
  models: ProviderModel[]
  modelId: string
  onModelChange: (modelId: string) => void
  loading?: boolean
  failed?: boolean
  disabled?: boolean
  variant?: 'split' | 'unified'
}

/**
 * Provider and model selector.
 *
 * In `unified` mode (used in CanvasHeader), provider and model sit side-by-side
 * inside a single cohesive pill frame and the model popover includes quick
 * provider tabs. Both triggers keep their accessible button labels so keyboard
 * users and existing tests can open either list directly.
 */
export function ModelPicker(props: ModelPickerProps) {
  const variant = props.variant ?? 'unified'
  const [open, setOpen] = useState<'provider' | 'model' | null>(null)
  const [query, setQuery] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const reduced = useReducedMotion()

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(null)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        setOpen(null)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  function handleContainerKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      event.stopPropagation()
      setOpen(null)
    }
  }

  const selected = useMemo(() => props.models.find((model) => model.model_id === props.modelId), [props.models, props.modelId])
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const ranked = [...props.models].sort((a, b) =>
      Number(lacksToolSupport(a)) - Number(lacksToolSupport(b)) || Number(b.is_favorite) - Number(a.is_favorite))
    if (!needle) return ranked.slice(0, 60)
    return ranked.filter((model) => `${model.display_name} ${model.model_id}`.toLowerCase().includes(needle)).slice(0, 60)
  }, [props.models, query])

  const panelMotion = reduced
    ? { initial: false as const, animate: { opacity: 1 }, exit: { opacity: 0 } }
    : { initial: { opacity: 0, y: 6, scale: 0.98 }, animate: { opacity: 1, y: 0, scale: 1 }, exit: { opacity: 0, y: 4, scale: 0.98 } }

  return (
    <div
      className={`model-picker model-picker--${variant}`}
      data-variant={variant}
      ref={containerRef}
      onKeyDown={handleContainerKeyDown}
    >
      <div className="model-picker__pill">
        <button
          type="button"
          className="chip model-picker__segment model-picker__segment--provider"
          data-testid="model-picker-provider-trigger"
          aria-haspopup="listbox"
          aria-expanded={open === 'provider'}
          disabled={props.disabled}
          onClick={() => setOpen((value) => (value === 'provider' ? null : 'provider'))}
        >
          <span className="chip__dot" aria-hidden="true" />
          <span className="model-picker__provider-text">{props.provider}</span>
          <span className="chip__caret" aria-hidden="true">⌄</span>
        </button>

        {variant === 'unified' && (
          <span className="model-picker__divider" aria-hidden="true">/</span>
        )}

        <button
          type="button"
          className="chip model-picker__segment model-picker__segment--model"
          data-testid="model-picker-model-trigger"
          aria-haspopup="listbox"
          aria-expanded={open === 'model'}
          disabled={props.disabled || props.models.length === 0}
          onClick={() => { setQuery(''); setOpen((value) => (value === 'model' ? null : 'model')) }}
        >
          <span className="model-picker__model-text">
            {props.loading ? 'carregando…' : selected?.display_name ?? (props.models.length === 0 ? 'sem modelos' : 'escolher modelo')}
          </span>
          <span className="chip__caret" aria-hidden="true">⌄</span>
        </button>
      </div>

      <AnimatePresence>
        {open === 'provider' && (
          <motion.ul className="picker-panel" role="listbox" aria-label="Providers" {...panelMotion} transition={{ duration: 0.16 }}>
            {props.providers.map((item) => (
              <li key={item}>
                <button
                  type="button"
                  role="option"
                  aria-selected={item === props.provider}
                  className={item === props.provider ? 'picker-option is-selected' : 'picker-option'}
                  onClick={() => { props.onProviderChange(item); setOpen(null) }}
                >
                  {item}
                </button>
              </li>
            ))}
          </motion.ul>
        )}

        {open === 'model' && (
          <motion.div className="picker-panel picker-panel--models" {...panelMotion} transition={{ duration: 0.16 }}>
            {variant === 'unified' && props.providers.length > 1 && (
              <div className="picker-provider-tabs" role="tablist" aria-label="Providers">
                {props.providers.map((item) => (
                  <button
                    key={item}
                    type="button"
                    role="tab"
                    aria-selected={item === props.provider}
                    className={`picker-provider-tab${item === props.provider ? ' is-selected' : ''}`}
                    onClick={() => props.onProviderChange(item)}
                  >
                    {item}
                  </button>
                ))}
              </div>
            )}
            <input
              className="picker-search"
              type="search"
              value={query}
              autoFocus
              placeholder="Filtrar modelos"
              aria-label="Filtrar modelos"
              onChange={(event) => setQuery(event.target.value)}
            />
            <ul role="listbox" aria-label="Modelos disponíveis">
              {filtered.map((model) => (
                <li key={model.model_id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={model.model_id === props.modelId}
                    className={model.model_id === props.modelId ? 'picker-option is-selected' : 'picker-option'}
                    onClick={() => { props.onModelChange(model.model_id); setOpen(null) }}
                  >
                    <span className="picker-option__name">{model.is_favorite && <span aria-label="favorito">★</span>}{model.display_name}</span>
                    {model.context_window ? <span className="picker-option__meta">{Math.round(model.context_window / 1000)}k</span> : null}
                    {lacksToolSupport(model) ? <span className="picker-option__meta picker-option__meta--warn" title="Este modelo não chama ferramentas; o Orin não consegue executar um turno com ele.">sem ferramentas</span> : null}
                  </button>
                </li>
              ))}
              {filtered.length === 0 && <li className="picker-empty">Nenhum modelo corresponde a “{query}”.</li>}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>

      {!props.loading && props.models.length === 0 && (
        <Link className="chip chip--action" to="/providers">
          {props.failed ? 'Catálogo indisponível · revisar' : 'Configurar provider'}
        </Link>
      )}
    </div>
  )
}


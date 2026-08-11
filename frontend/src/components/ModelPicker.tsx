import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { ProviderModel, ProviderName } from '../api/providers'

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
}

/**
 * Provider and model as two quiet chips.
 *
 * They open into a searchable list rather than a native select because a
 * provider catalog is hundreds of entries long, and because an empty catalog has
 * to offer the fix ("Configurar provider") instead of an empty dropdown.
 */
export function ModelPicker(props: ModelPickerProps) {
  const [open, setOpen] = useState<'provider' | 'model' | null>(null)
  const [query, setQuery] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const reduced = useReducedMotion()

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(null)
    }
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(null) }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const selected = useMemo(() => props.models.find((model) => model.model_id === props.modelId), [props.models, props.modelId])
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const ranked = [...props.models].sort((a, b) => Number(b.is_favorite) - Number(a.is_favorite))
    if (!needle) return ranked.slice(0, 60)
    return ranked.filter((model) => `${model.display_name} ${model.model_id}`.toLowerCase().includes(needle)).slice(0, 60)
  }, [props.models, query])

  const panelMotion = reduced
    ? { initial: false as const, animate: { opacity: 1 }, exit: { opacity: 0 } }
    : { initial: { opacity: 0, y: 6, scale: 0.98 }, animate: { opacity: 1, y: 0, scale: 1 }, exit: { opacity: 0, y: 4, scale: 0.98 } }

  return (
    <div className="model-picker" ref={containerRef}>
      <button
        type="button"
        className="chip"
        aria-haspopup="listbox"
        aria-expanded={open === 'provider'}
        disabled={props.disabled}
        onClick={() => setOpen((value) => (value === 'provider' ? null : 'provider'))}
      >
        <span className="chip__dot" aria-hidden="true" />
        {props.provider}
        <span className="chip__caret" aria-hidden="true">⌄</span>
      </button>

      <button
        type="button"
        className="chip"
        aria-haspopup="listbox"
        aria-expanded={open === 'model'}
        disabled={props.disabled || props.models.length === 0}
        onClick={() => { setQuery(''); setOpen((value) => (value === 'model' ? null : 'model')) }}
      >
        {props.loading ? 'carregando…' : selected?.display_name ?? (props.models.length === 0 ? 'sem modelos' : 'escolher modelo')}
        <span className="chip__caret" aria-hidden="true">⌄</span>
      </button>

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

import { useMemo, useState } from 'react'
import type { ProviderModel } from '../../api/providers'

export type ModelCatalogFilter = 'all' | 'favorites' | 'text' | 'image' | 'audio' | 'video'

const FILTERS: { value: ModelCatalogFilter; label: string }[] = [
  { value: 'all', label: 'Todos' },
  { value: 'favorites', label: 'Favoritos' },
  { value: 'text', label: 'Texto' },
  { value: 'image', label: 'Imagem' },
  { value: 'audio', label: 'Áudio' },
  { value: 'video', label: 'Vídeo' },
]

function matchesFilter(model: ProviderModel, filter: ModelCatalogFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'favorites') return model.is_favorite
  return model.output_modalities.includes(filter)
}

// Every word must appear in the name or the id, so "qwen image" finds
// "Qwen: Qwen Image 3" as well as "qwen/qwen-image-3".
function matchesQuery(model: ProviderModel, words: string[]): boolean {
  if (words.length === 0) return true
  const haystack = `${model.display_name} ${model.model_id}`.toLowerCase()
  return words.every((word) => haystack.includes(word))
}

export function formatContextWindow(tokens: number | null): string | null {
  if (tokens === null) return null
  if (tokens >= 1_000_000) return `${Number((tokens / 1_000_000).toFixed(1))}M`
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000)}k`
  return String(tokens)
}

type ProviderModelListProps = {
  provider: string
  models: ProviderModel[]
  disabled: boolean
  onToggleFavorite: (model: ProviderModel) => void
  onRemoveCustom: (modelId: string) => void
}

export function ProviderModelList({ provider, models, disabled, onToggleFavorite, onRemoveCustom }: ProviderModelListProps) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<ModelCatalogFilter>('all')
  const words = useMemo(() => query.toLowerCase().split(/\s+/).filter(Boolean), [query])
  const counts = useMemo(() => {
    const result = {} as Record<ModelCatalogFilter, number>
    for (const { value } of FILTERS) result[value] = models.filter((model) => matchesFilter(model, value) && matchesQuery(model, words)).length
    return result
  }, [models, words])
  const visible = useMemo(() => models.filter((model) => matchesFilter(model, filter) && matchesQuery(model, words)), [models, filter, words])

  if (models.length === 0) return null
  return <>
    <div className="provider-panel__catalog-filters">
      <label htmlFor={`${provider}-model-search`}>Buscar modelo<input id={`${provider}-model-search`} type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Nome ou id, ex.: qwen image" autoComplete="off" /></label>
      <div role="group" aria-label="Filtrar por tipo de saída" className="provider-panel__catalog-chips">
        {FILTERS.map(({ value, label }) => <button key={value} type="button" className="provider-panel__catalog-chip" aria-pressed={filter === value} onClick={() => setFilter(value)}>{label} <span>{counts[value]}</span></button>)}
      </div>
      <p aria-live="polite" className="provider-panel__catalog-count">{visible.length === models.length ? `${models.length} modelos` : `${visible.length} de ${models.length} modelos`}</p>
    </div>
    {visible.length > 0
      ? <ul>{visible.map((model) => {
          const context = formatContextWindow(model.context_window)
          return <li key={model.model_id}><div><strong>{model.display_name}</strong>{model.is_custom && <span className="provider-panel__model-badge">Manual</span>}{model.output_modalities.includes('image') && <span className="provider-panel__model-badge" data-modality="image">Imagem</span>}{model.output_modalities.includes('audio') && <span className="provider-panel__model-badge" data-modality="audio">Áudio</span>}{model.output_modalities.includes('video') && <span className="provider-panel__model-badge" data-modality="video">Vídeo</span>}<code>{model.model_id}</code>{context && <span className="provider-panel__model-context" title={`Janela de contexto: ${model.context_window?.toLocaleString('pt-BR')} tokens`}>Contexto {context}</span>}</div><div className="provider-panel__model-actions"><button type="button" className="button button--secondary" onClick={() => onToggleFavorite(model)} disabled={disabled}>{model.is_favorite ? 'Remover favorito' : 'Favoritar'}</button>{model.is_custom && <button type="button" className="button button--secondary button--danger" onClick={() => { if (window.confirm(`Remover o modelo ${model.model_id}?`)) onRemoveCustom(model.model_id) }} disabled={disabled}>Remover modelo</button>}</div></li>
        })}</ul>
      : <p role="status">Nenhum modelo corresponde à busca e ao filtro.</p>}
  </>
}

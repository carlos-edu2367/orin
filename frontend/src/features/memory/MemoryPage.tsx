import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createBrowserApiClient, type ApiClient } from '../../api/client'
import {
  createManagedMemory,
  deleteManagedMemory,
  listManagedMemories,
  loadCustomMemories,
  resolveSemanticScope,
  saveCustomMemories,
  scopeLabelFromSemantic,
  semanticFromScopeLabel,
  updateManagedMemory,
  type ManagedMemory,
  type MemoryScopeLabel,
  type MemorySemanticScope,
} from '../../api/memory'
import { SettingsPage } from '../settings/SettingsPage'

type MemoryCardItem = {
  id: string
  title: string
  fullText: string
  scopeLabel: MemoryScopeLabel
  semanticScope: MemorySemanticScope
  apiScope: 'user' | 'project'
  metaLabel: string
  source: 'api' | 'local'
}

type UndoSnapshot = {
  cards: MemoryCardItem[]
  selectedId: string
  draftText: string
  draftScope: MemoryScopeLabel
}

function mapApiMemoryToCard(item: ManagedMemory): MemoryCardItem {
  const semanticScope = resolveSemanticScope(item)
  const scopeLabel = scopeLabelFromSemantic(semanticScope)
  const dateLabel = item.updated_at || item.created_at
  const metaLabel = dateLabel ? 'Criada pelo Orin' : 'Lembrança ativa'
  return {
    id: item.memory_id,
    title: item.fact,
    fullText: item.fact,
    scopeLabel,
    semanticScope,
    apiScope: item.scope,
    metaLabel,
    source: 'api',
  }
}

function getTagModifierClass(scope: MemorySemanticScope): string {
  if (scope === 'project') return 'is-project'
  if (scope === 'friend') return 'is-friend'
  return 'is-work'
}

export function MemoryPage({
  projectId,
  embedded = false,
  client: providedClient,
}: {
  projectId?: string
  embedded?: boolean
  client?: ApiClient
}) {
  const client = useMemo(() => providedClient ?? createBrowserApiClient(), [providedClient])
  const apiScope: 'user' | 'project' = projectId ? 'project' : 'user'

  const [activeTab, setActiveTab] = useState<MemorySemanticScope>(projectId ? 'project' : 'work')
  const [explicitTabFilter, setExplicitTabFilter] = useState(false)
  const [query, setQuery] = useState('')
  const [error, setError] = useState(false)
  const [cards, setCards] = useState<MemoryCardItem[]>([])
  const [selectedId, setSelectedId] = useState<string>('')
  const [isCreating, setIsCreating] = useState(false)
  const [draftText, setDraftText] = useState<string>('')
  const [draftScope, setDraftScope] = useState<MemoryScopeLabel>('Trabalho')
  const [toastUndoSnapshot, setToastUndoSnapshot] = useState<UndoSnapshot | null>(null)

  const textareaRef = useRef<HTMLTextAreaElement | null>(null)

  useLayoutEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.textContent = ''
    el.value = draftText
  })

  const syncInspectorToCard = useCallback((card: MemoryCardItem | undefined) => {
    if (!card) {
      setDraftText('')
      setDraftScope('Trabalho')
      return
    }
    setSelectedId(card.id)
    setDraftText(card.fullText)
    setDraftScope(card.scopeLabel)
    setIsCreating(false)
  }, [])

  const load = useCallback(() => {
    return listManagedMemories(client, { scope: apiScope, projectId, query: query.trim() || undefined })
      .then((result) => {
        setError(false)
        const customStored = loadCustomMemories().map((item) => ({
          ...mapApiMemoryToCard(item),
          source: 'local' as const,
        }))

        const mappedApi = result.items.map(mapApiMemoryToCard)
        const combined = [...mappedApi, ...customStored]
        setCards(combined)
        setSelectedId((prevId) => {
          const existing = combined.find((c) => c.id === prevId) ?? combined[0]
          if (existing) {
            setDraftText(existing.fullText)
            setDraftScope(existing.scopeLabel)
            return existing.id
          }
          return ''
        })
      })
      .catch(() => {
        setError(true)
      })
  }, [client, apiScope, projectId, query])

  useEffect(() => {
    if (query === '') {
      void load()
      return undefined
    }
    const timer = window.setTimeout(() => {
      void load()
    }, 150)
    return () => window.clearTimeout(timer)
  }, [load, query])

  const visibleCards = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return cards.filter((card) => {
      if (explicitTabFilter && card.semanticScope !== activeTab) {
        return false
      }
      if (!normalizedQuery) return true
      return (
        card.title.toLowerCase().includes(normalizedQuery) ||
        card.fullText.toLowerCase().includes(normalizedQuery) ||
        card.scopeLabel.toLowerCase().includes(normalizedQuery)
      )
    })
  }, [cards, explicitTabFilter, activeTab, query])

  const selectedCard = useMemo(
    () => cards.find((c) => c.id === selectedId) ?? visibleCards[0] ?? cards[0],
    [cards, selectedId, visibleCards],
  )

  const handleSelectTab = (tab: MemorySemanticScope) => {
    setActiveTab(tab)
    setExplicitTabFilter(tab !== 'work')
    const matching = cards.find((c) => (tab === 'work' ? true : c.semanticScope === tab))
    if (matching) {
      syncInspectorToCard(matching)
    }
  }

  const handleStartAdd = () => {
    setIsCreating(true)
    setDraftText('')
    setDraftScope(scopeLabelFromSemantic(activeTab))
  }

  const handleSelectCard = (card: MemoryCardItem) => {
    syncInspectorToCard(card)
  }

  const handleSave = () => {
    const trimmed = draftText.trim().slice(0, 200)
    if (!trimmed) return

    const snapshot: UndoSnapshot = {
      cards: cards.map((c) => ({ ...c })),
      selectedId,
      draftText: selectedCard?.fullText ?? '',
      draftScope: selectedCard?.scopeLabel ?? 'Trabalho',
    }
    setToastUndoSnapshot(snapshot)

    const nextSemantic = semanticFromScopeLabel(draftScope)
    const nextApiScope: 'user' | 'project' = nextSemantic === 'project' ? 'project' : 'user'

    if (isCreating || !selectedCard) {
      const newCard: MemoryCardItem = {
        id: `custom-mem-${Date.now()}`,
        title: trimmed,
        fullText: trimmed,
        scopeLabel: draftScope,
        semanticScope: nextSemantic,
        apiScope: nextApiScope,
        metaLabel: 'Criada agora',
        source: 'local',
      }
      const nextCards = [newCard, ...cards]
      setCards(nextCards)
      setSelectedId(newCard.id)
      setDraftText(newCard.fullText)
      setIsCreating(false)

      void createManagedMemory(client, {
        fact: trimmed,
        scope: nextApiScope,
        project_id: nextSemantic === 'project' ? (projectId ?? null) : null,
        tags: [nextSemantic],
      })
      return
    }

    const updatedCard: MemoryCardItem = {
      ...selectedCard,
      title: trimmed,
      fullText: trimmed,
      scopeLabel: draftScope,
      semanticScope: nextSemantic,
      apiScope: nextApiScope,
    }
    const nextCards = cards.map((item) => (item.id === selectedCard.id ? updatedCard : item))
    setCards(nextCards)
    setSelectedId(updatedCard.id)
    setDraftText(updatedCard.fullText)
    setIsCreating(false)

    if (selectedCard.source === 'api') {
      void updateManagedMemory(client, selectedCard.id, trimmed, selectedCard.apiScope, projectId).catch(
        () => undefined,
      )
    }
  }

  const handleUndo = () => {
    if (!toastUndoSnapshot) return
    setCards(toastUndoSnapshot.cards)
    setSelectedId(toastUndoSnapshot.selectedId)
    setDraftText(toastUndoSnapshot.draftText)
    setDraftScope(toastUndoSnapshot.draftScope)
    setIsCreating(false)
    setToastUndoSnapshot(null)
  }

  const handleForget = () => {
    if (isCreating) {
      setIsCreating(false)
      if (selectedCard) {
        syncInspectorToCard(selectedCard)
      }
      return
    }
    if (!selectedCard) return

    const target = selectedCard
    const remaining = cards.filter((item) => item.id !== target.id)
    setCards(remaining)

    const nextSelected = remaining[0]
    if (nextSelected) {
      syncInspectorToCard(nextSelected)
    } else {
      setSelectedId('')
      setDraftText('')
    }

    if (target.source === 'api') {
      void deleteManagedMemory(client, target.id, target.apiScope, projectId).catch(() => undefined)
    } else if (target.source === 'local') {
      const stored = loadCustomMemories().filter((item) => item.memory_id !== target.id)
      saveCustomMemories(stored)
    }
  }

  const surface = (
    <section className="memory-ref11" data-testid="memory-page">
      <header className="memory-ref11__header">
        <div className="memory-ref11__header-copy">
          <h1 className="memory-ref11__title" data-testid="memory-heading">
            Memória sob seu controle
          </h1>
          <p className="memory-ref11__subtitle">
            Tudo o que o Orin lembra sobre você e seus projetos. Você pode editar ou apagar qualquer item.
          </p>
        </div>

        {toastUndoSnapshot && (
          <div
            className="memory-saved-toast"
            data-testid="memory-saved-toast"
            role="status"
            aria-live="polite"
          >
            <span>✓ Nova lembrança salva.</span>
            <button
              type="button"
              className="memory-saved-toast__undo"
              data-testid="memory-undo-button"
              onClick={handleUndo}
            >
              Desfazer
            </button>
          </div>
        )}
      </header>

      <div className="memory-ref11__tabs-bar">
        <div className="memory-ref11__tabs" role="tablist" aria-label="Escopo da memória">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'work'}
            className={`memory-ref11__tab${activeTab === 'work' ? ' is-active' : ''}`}
            data-testid="memory-tab-work"
            onClick={() => handleSelectTab('work')}
          >
            Trabalho
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'friend'}
            className={`memory-ref11__tab${activeTab === 'friend' ? ' is-active' : ''}`}
            data-testid="memory-tab-friend"
            onClick={() => handleSelectTab('friend')}
          >
            Friend
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'project'}
            className={`memory-ref11__tab${activeTab === 'project' ? ' is-active' : ''}`}
            data-testid="memory-tab-project"
            onClick={() => handleSelectTab('project')}
          >
            Por projeto
          </button>
        </div>

        <button
          type="button"
          className="memory-ref11__add-btn"
          data-testid="memory-add-button"
          onClick={handleStartAdd}
        >
          + Adicionar lembrança
        </button>
      </div>

      <div className="memory-ref11__search-row">
        <input
          id="memory-search"
          type="search"
          className="memory-ref11__search-input"
          aria-label="Buscar memórias"
          placeholder="Buscar lembranças..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      {error && <p role="alert">Não foi possível carregar as memórias.</p>}

      <div className="memory-ref11__master-detail">
        <div className="memory-cards-list" data-testid="memory-cards-list">
          {visibleCards.length === 0 ? (
            <p className="memory-empty-state" data-testid="memory-empty-state">
              Nenhuma memória salva neste filtro.
            </p>
          ) : (
            visibleCards.map((item) => {
              const isSelected = !isCreating && selectedCard?.id === item.id
              const tagMod = getTagModifierClass(item.semanticScope)
              return (
                <article
                  key={item.id}
                  className={`memory-card-v6${isSelected ? ' is-selected' : ''}`}
                  onClick={() => handleSelectCard(item)}
                >
                  <div className="memory-card-v6__content">
                    <h3 className="memory-card-v6__title">{item.title}</h3>
                    <div className="memory-card-v6__meta">
                      <span className={`memory-card-v6__tag ${tagMod}`}>{item.scopeLabel}</span>
                      <span>{item.metaLabel}</span>
                    </div>
                  </div>
                  <div className="memory-card-v6__actions">
                    <button
                      type="button"
                      className="memory-card-v6__edit-btn"
                      aria-label={`Editar ${item.title}`}
                      onClick={(event) => {
                        event.stopPropagation()
                        handleSelectCard(item)
                      }}
                    >
                      ✎ Editar
                    </button>
                    <button
                      type="button"
                      className="memory-card-v6__more-btn"
                      aria-label={`Opções de ${item.title}`}
                      onClick={(event) => {
                        event.stopPropagation()
                        handleSelectCard(item)
                      }}
                    >
                      •••
                    </button>
                  </div>
                </article>
              )
            })
          )}
        </div>

        <aside
          className="memory-inspector"
          data-testid="memory-inspector"
          aria-label="Editar lembrança"
        >
          <h2 className="memory-inspector__title">Editar lembrança</h2>

          <div className="memory-inspector__field">
            <label htmlFor="memory-inspector-textarea">Lembrança</label>
            <textarea
              ref={textareaRef}
              id="memory-inspector-textarea"
              className="memory-inspector__textarea"
              aria-label="Texto da lembrança"
              maxLength={200}
              value={draftText}
              onChange={(event) => setDraftText(event.target.value.slice(0, 200))}
            />
            <div className="memory-inspector__counter" data-testid="memory-char-counter">
              {draftText.length} / 200 caracteres
            </div>
          </div>

          <div className="memory-inspector__field">
            <label htmlFor="memory-inspector-scope">Escopo</label>
            <select
              id="memory-inspector-scope"
              className="memory-inspector__select"
              aria-label="Escopo"
              value={draftScope}
              onChange={(event) => setDraftScope(event.target.value as MemoryScopeLabel)}
            >
              <option value="Trabalho">Trabalho</option>
              <option value="Friend">Friend</option>
              <option value="Por projeto">Por projeto</option>
            </select>
          </div>

          <div className="memory-inspector__actions">
            <button
              type="button"
              className="memory-inspector__forget-btn"
              data-testid="memory-forget-button"
              onClick={handleForget}
            >
              Esquecer
            </button>
            <button
              type="button"
              className="memory-inspector__save-btn"
              data-testid="memory-save-button"
              onClick={handleSave}
              disabled={!draftText.trim()}
            >
              Salvar
            </button>
          </div>
        </aside>
      </div>
    </section>
  )

  if (projectId) {
    return (
      <main className="project-page">
        <a href={`/projects/${projectId}`}>Voltar ao projeto</a>
        {surface}
      </main>
    )
  }

  if (embedded) {
    return surface
  }

  return <SettingsPage>{surface}</SettingsPage>
}

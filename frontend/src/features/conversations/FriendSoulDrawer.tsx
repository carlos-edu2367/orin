import { useEffect, useRef, useState, type CSSProperties } from 'react'
import {
  clampFriendTraits,
  FRIEND_ORB_THEMES,
  type FriendBondStage,
  type FriendMemoryEntry,
  type FriendMemoryKind,
  type FriendOrbTheme,
  type FriendPersonaState,
  type FriendTraitVector,
} from '../../api/conversations'
import { OrinMascot } from '../../components/OrinMascot'
import { LiveOrb } from './live/LiveOrb'
import { ORB_THEMES } from './live/orbThemes'

export type FriendPersonaUpdate = {
  traits: FriendTraitVector
  style_notes: string[]
  current_mood: string
  display_name: string
  user_nickname: string
  orb_theme: FriendOrbTheme
}

export type FriendSoulDrawerProps = {
  isOpen: boolean
  persona: FriendPersonaState | null
  memories: FriendMemoryEntry[]
  onClose: () => void
  onUpdatePersona: (input: FriendPersonaUpdate) => Promise<FriendPersonaState | void> | void
  onSyncSoul: () => Promise<FriendPersonaState | void> | void
  onDeleteMemory: (memoryId: string) => Promise<void> | void
  onStartVoice?: () => void
}

type FriendPersonalizeTab = 'appearance' | 'voice' | 'personality'

type TraitMeta = {
  key: keyof FriendTraitVector
  label: string
  low: string
  high: string
  min: number
  max: number
  icon: string
}

const PRIMARY_TRAIT_META: ReadonlyArray<TraitMeta> = [
  { key: 'warmth', label: 'Calor humano', low: 'Mais reservado', high: 'Mais caloroso', min: 0.3, max: 0.95, icon: '♡' },
  { key: 'humor', label: 'Bom humor', low: 'Mais sério', high: 'Mais bem-humorado', min: 0.1, max: 0.9, icon: '☺' },
  { key: 'formality', label: 'Formalidade', low: 'Mais casual', high: 'Mais formal', min: 0.05, max: 0.85, icon: '💼' },
  { key: 'candor', label: 'Diretividade', low: 'Mais exploratório', high: 'Mais direto', min: 0.5, max: 0.95, icon: '💬' },
  { key: 'energy', label: 'Energia', low: 'Mais tranquilo', high: 'Mais energético', min: 0.2, max: 0.9, icon: '⚡' },
]

const SECONDARY_TRAIT_META: ReadonlyArray<TraitMeta> = [
  { key: 'sarcasm', label: 'Ironia', low: 'Literal', high: 'Provocador', min: 0.0, max: 0.75, icon: '✦' },
  { key: 'verbosity', label: 'Tamanho das respostas', low: 'Curtas', high: 'Detalhadas', min: 0.15, max: 0.8, icon: '≡' },
]

export const PERSONALITY_PRESETS: ReadonlyArray<{ id: string; label: string; description: string; traits: FriendTraitVector }> = [
  {
    id: 'parceiro',
    label: 'Parceiro tranquilo',
    description: 'Amigo presente, leve e honesto.',
    traits: { warmth: 0.7, humor: 0.5, sarcasm: 0.2, formality: 0.3, verbosity: 0.4, candor: 0.7, energy: 0.5 },
  },
  {
    id: 'animado',
    label: 'Animado',
    description: 'Empolgado, faz piada, energia alta.',
    traits: { warmth: 0.8, humor: 0.75, sarcasm: 0.2, formality: 0.15, verbosity: 0.45, candor: 0.65, energy: 0.85 },
  },
  {
    id: 'ironico',
    label: 'Irônico',
    description: 'Provoca com carinho e fala a real.',
    traits: { warmth: 0.55, humor: 0.7, sarcasm: 0.65, formality: 0.2, verbosity: 0.35, candor: 0.8, energy: 0.6 },
  },
  {
    id: 'profissional',
    label: 'Profissional',
    description: 'Organizado, claro e formal.',
    traits: { warmth: 0.5, humor: 0.2, sarcasm: 0.05, formality: 0.7, verbosity: 0.55, candor: 0.85, energy: 0.45 },
  },
  {
    id: 'direto',
    label: 'Direto ao ponto',
    description: 'Respostas curtas, sem rodeios.',
    traits: { warmth: 0.5, humor: 0.3, sarcasm: 0.15, formality: 0.35, verbosity: 0.18, candor: 0.9, energy: 0.5 },
  },
]

const MOOD_SUGGESTIONS = ['sereno', 'animado', 'curioso', 'focado', 'brincalhão', 'acolhedor']

const DEFAULT_TRAITS: FriendTraitVector = PERSONALITY_PRESETS[0].traits

export function bondStageLabel(stage: FriendBondStage): string {
  switch (stage) {
    case 'acquaintance':
      return 'Conhecido'
    case 'buddy':
      return 'Parceiro'
    case 'close_friend':
      return 'Amigo próximo'
    case 'confidant':
      return 'Confidente'
  }
}

function memoryKindLabel(kind: FriendMemoryKind): string {
  switch (kind) {
    case 'inside_joke':
      return 'Piada interna'
    case 'milestone':
      return 'Marco'
    case 'emotional':
      return 'Emocional'
    case 'episodic':
      return 'Momento'
  }
}

function sameTraits(a: FriendTraitVector, b: FriendTraitVector): boolean {
  return (Object.keys(a) as Array<keyof FriendTraitVector>).every((key) => Math.abs(a[key] - b[key]) < 0.005)
}

function isValidFriendName(value: string | undefined | null): value is string {
  if (!value) return false
  const trimmed = value.trim()
  return trimmed.length > 0 && trimmed.length <= 40 && !/[<>{}`]/.test(trimmed)
}

type Draft = {
  name: string
  nickname: string
  theme: FriendOrbTheme
  traits: FriendTraitVector
  mood: string
  notes: string
}

function draftFrom(persona: FriendPersonaState | null): Draft {
  return {
    name: persona?.display_name ?? 'Orin',
    nickname: persona?.user_nickname ?? '',
    theme: persona?.orb_theme ?? 'aurora',
    traits: clampFriendTraits(persona?.traits ?? DEFAULT_TRAITS),
    mood: persona?.current_mood ?? 'sereno',
    notes: (persona?.style_notes ?? []).join('\n'),
  }
}

export function FriendSoulDrawer({
  isOpen,
  persona,
  memories,
  onClose,
  onUpdatePersona,
  onSyncSoul,
  onDeleteMemory,
  onStartVoice,
}: FriendSoulDrawerProps) {
  const [draft, setDraft] = useState<Draft>(() => draftFrom(persona))
  const [dirty, setDirty] = useState(false)
  const [status, setStatus] = useState<{ tone: 'ok' | 'error' | 'busy'; text: string } | null>(null)
  const [isSyncing, setIsSyncing] = useState(false)
  const [activeTab, setActiveTab] = useState<FriendPersonalizeTab>('personality')
  const [moreSettingsOpen, setMoreSettingsOpen] = useState(false)
  const [isPreviewPlaying, setIsPreviewPlaying] = useState(false)
  const previewTimerRef = useRef<number | null>(null)

  // A freshly loaded persona replaces the draft unless the person is mid-edit.
  const [seenPersona, setSeenPersona] = useState(persona)
  if (persona !== seenPersona) {
    setSeenPersona(persona)
    if (!dirty) setDraft(draftFrom(persona))
  }

  const [seenOpen, setSeenOpen] = useState(isOpen)
  if (isOpen !== seenOpen) {
    setSeenOpen(isOpen)
    if (!isOpen && isPreviewPlaying) {
      setIsPreviewPlaying(false)
    }
  }

  function clearPreviewResources() {
    if (previewTimerRef.current !== null) {
      window.clearTimeout(previewTimerRef.current)
      previewTimerRef.current = null
    }
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      try {
        window.speechSynthesis?.cancel?.()
      } catch {
        // Ignore synthesis cancel errors in restricted environments
      }
    }
  }

  function stopPreview() {
    clearPreviewResources()
    setIsPreviewPlaying(false)
  }

  useEffect(() => {
    if (!isOpen) {
      clearPreviewResources()
      return
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        clearPreviewResources()
        setIsPreviewPlaying(false)
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isOpen, onClose])

  useEffect(() => {
    return () => {
      clearPreviewResources()
    }
  }, [])

  if (!isOpen) return null

  function edit(patch: Partial<Draft>) {
    setDraft((current) => ({ ...current, ...patch }))
    setDirty(true)
    setStatus(null)
  }

  const nameError = !draft.name.trim()
    ? 'Dê um nome a ele.'
    : draft.name.trim().length > 40
      ? 'Use até 40 caracteres.'
      : /[<>{}`]/.test(draft.name)
        ? 'Evite os caracteres < > { } `.'
        : null
  const activePreset = PERSONALITY_PRESETS.find((preset) => sameTraits(preset.traits, draft.traits))?.id ?? null
  const displayName = draft.name.trim() || 'Orin'

  function handleClose() {
    stopPreview()
    onClose()
  }

  function handleStartVoice() {
    stopPreview()
    if (onStartVoice) {
      onStartVoice()
    } else {
      onClose()
    }
  }

  function handleListenPreview() {
    if (isPreviewPlaying) return
    setIsPreviewPlaying(true)
    if (previewTimerRef.current !== null) {
      window.clearTimeout(previewTimerRef.current)
    }
    previewTimerRef.current = window.setTimeout(() => {
      previewTimerRef.current = null
      setIsPreviewPlaying(false)
    }, 600)

    if (
      typeof window !== 'undefined' &&
      'speechSynthesis' in window &&
      typeof window.SpeechSynthesisUtterance === 'function'
    ) {
      try {
        const utterance = new window.SpeechSynthesisUtterance(
          `Olá! Eu sou ${displayName}, seu parceiro de trabalho.`,
        )
        utterance.lang = 'pt-BR'
        const finish = () => {
          if (previewTimerRef.current !== null) {
            window.clearTimeout(previewTimerRef.current)
            previewTimerRef.current = null
          }
          setIsPreviewPlaying(false)
        }
        utterance.onend = finish
        utterance.onerror = finish
        window.speechSynthesis.speak(utterance)
      } catch {
        // Fallback timer releases isPreviewPlaying
      }
    }
  }

  function handleRestoreDefaults() {
    const validName = isValidFriendName(draft.name)
      ? draft.name
      : isValidFriendName(persona?.display_name)
        ? persona.display_name.trim()
        : 'Orin'
    setDraft((current) => ({
      ...current,
      name: validName,
      theme: 'aurora',
      traits: clampFriendTraits(DEFAULT_TRAITS),
      mood: 'sereno',
      notes: '',
    }))
    setDirty(true)
    setStatus(null)
  }

  async function handleSave() {
    if (nameError) {
      setStatus({ tone: 'error', text: nameError })
      return
    }
    setStatus({ tone: 'busy', text: 'Salvando…' })
    try {
      await onUpdatePersona({
        traits: clampFriendTraits(draft.traits),
        style_notes: draft.notes.split('\n').map((line) => line.trim()).filter(Boolean),
        current_mood: draft.mood.trim() || 'sereno',
        display_name: draft.name.trim(),
        user_nickname: draft.nickname.trim(),
        orb_theme: draft.theme,
      })
      setDirty(false)
      setStatus({ tone: 'ok', text: `${displayName} foi atualizado.` })
    } catch {
      setStatus({ tone: 'error', text: 'Não consegui salvar. Confira os campos e tente de novo.' })
    }
  }

  async function handleSync() {
    setIsSyncing(true)
    try {
      await onSyncSoul()
      setDirty(false)
    } finally {
      setIsSyncing(false)
    }
  }

  return (
    <div
      className="friend-drawer"
      data-testid="friend-soul-drawer"
      data-active-tab={activeTab}
      role="dialog"
      aria-modal="true"
      aria-label={`Personalizar ${displayName}`}
    >
      <button type="button" className="friend-drawer__scrim" aria-label="Fechar" tabIndex={-1} onClick={handleClose} />
      <aside className="friend-drawer__panel">
        <header className="friend-drawer__header friend-view__header">
          <div className="friend-drawer__title">
            <h1>Seu Friend</h1>
            <p className="friend-drawer__subtitle">Personalize como o Orin conversa e te acompanha.</p>
          </div>
          <div className="friend-drawer__header-actions">
            <button
              type="button"
              className="voice-button friend-drawer__voice-pill"
              data-testid="friend-start-voice-button"
              onClick={handleStartVoice}
            >
              <span aria-hidden="true">🎙️</span>
              <span>Conversar por voz</span>
            </button>
            <OrinMascot size={32} variant="flat" statusDot="online" ariaHidden />
            <button
              type="button"
              className="voice-button is-quiet"
              data-testid="close-friend-soul-drawer"
              onClick={handleClose}
              aria-label={`Fechar personalização de ${displayName}`}
            >
              Fechar
            </button>
          </div>
        </header>

        <div
          className="friend-tabs"
          role="tablist"
          aria-label="Seções de personalização do Friend"
          data-testid="friend-personalize-tabs"
        >
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'appearance'}
            className={`friend-tab${activeTab === 'appearance' ? ' is-active' : ''}`}
            data-testid="friend-tab-appearance"
            onClick={() => {
              setActiveTab('appearance')
              setMoreSettingsOpen(true)
            }}
          >
            <span aria-hidden="true">✨</span>
            <span>Aparência</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'voice'}
            className={`friend-tab${activeTab === 'voice' ? ' is-active' : ''}`}
            data-testid="friend-tab-voice"
            onClick={() => {
              setActiveTab('voice')
              setMoreSettingsOpen(true)
            }}
          >
            <span aria-hidden="true">🎙️</span>
            <span>Voz</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'personality'}
            className={`friend-tab${activeTab === 'personality' ? ' is-active' : ''}`}
            data-testid="friend-tab-personality"
            onClick={() => {
              setActiveTab('personality')
              setMoreSettingsOpen(false)
            }}
          >
            <span aria-hidden="true">👤</span>
            <span>Personalidade</span>
          </button>
        </div>

        <div className="friend-drawer__body friend-view__grid" data-active-tab={activeTab}>
          <div className="friend-preview-card" data-testid="friend-preview-card">
            <div className="friend-preview-card__mascot">
              <OrinMascot size={160} variant="soft3d" />
              <div className="friend-drawer__preview" aria-hidden="true">
                <LiveOrb phase="listening" theme={draft.theme} level={() => 0} size={64} staticMode />
              </div>
            </div>
            <h2 className="friend-preview-card__name" data-testid="friend-preview-name">
              {displayName}
            </h2>
            <p className="friend-preview-card__tagline">Seu parceiro de trabalho.</p>
            <button
              type="button"
              className="voice-button friend-preview-card__listen"
              data-testid="friend-listen-preview-button"
              disabled={isPreviewPlaying}
              data-busy={isPreviewPlaying ? 'true' : 'false'}
              onClick={handleListenPreview}
            >
              <span aria-hidden="true">▷</span>
              <span>{isPreviewPlaying ? 'Ouvindo prévia…' : 'Ouvir uma prévia'}</span>
            </button>

            <section className="friend-card visually-hidden" data-testid="friend-bond-card">
              <div className="friend-card__row">
                <strong data-testid="friend-bond-stage">{bondStageLabel(persona?.bond_stage ?? 'acquaintance')}</strong>
                <span className="friend-card__muted" data-testid="friend-affinity-score">
                  Afinidade {(persona?.affinity_score ?? 0).toFixed(0)}%
                </span>
              </div>
              <div className="friend-progress" aria-hidden="true">
                <span style={{ width: `${Math.min(100, Math.max(0, persona?.affinity_score ?? 0))}%` }} />
              </div>
              <p className="friend-card__muted">O vínculo cresce sozinho conforme vocês conversam.</p>
            </section>
          </div>

          <div className="friend-view__main">
            <section className="friend-section">
              <h3>Identidade</h3>
              <div className="voice-field">
                <label htmlFor="friend-name-input">Nome do Friend</label>
                <span className="voice-settings__hint">Como você quer chamar seu Friend?</span>
                <div className="friend-input-with-icon">
                  <OrinMascot size={20} variant="mini" ariaHidden />
                  <input
                    id="friend-name-input"
                    data-testid="friend-name-input"
                    value={draft.name}
                    maxLength={40}
                    onChange={(event) => edit({ name: event.target.value })}
                    aria-invalid={nameError ? true : undefined}
                    placeholder="Orin"
                  />
                </div>
                {nameError && dirty && <span className="voice-settings__error">{nameError}</span>}
              </div>
              <div className="voice-field">
                <label htmlFor="friend-nickname-input">Como te chamar</label>
                <span className="voice-settings__hint">Qual nome o Orin deve usar para se referir a você?</span>
                <div className="friend-input-with-icon">
                  <span className="friend-input-with-icon__glyph" aria-hidden="true">
                    👤
                  </span>
                  <input
                    id="friend-nickname-input"
                    data-testid="friend-nickname-input"
                    value={draft.nickname}
                    maxLength={40}
                    onChange={(event) => edit({ nickname: event.target.value })}
                    placeholder="Seu nome ou apelido"
                  />
                </div>
              </div>
            </section>

            <section className="friend-section">
              <div className="friend-section__head">
                <h3>Personalidade</h3>
                <p className="voice-settings__hint">
                  Ajuste o estilo de conversa do Orin para combinar com o seu jeito de trabalhar.
                </p>
              </div>
              <div className="friend-bipolar-list">
                {PRIMARY_TRAIT_META.map((meta) => {
                  const sliderPct = Math.round(((draft.traits[meta.key] - meta.min) / (meta.max - meta.min)) * 100)
                  return (
                    <div key={meta.key} className="friend-bipolar-slider friend-trait" data-trait={meta.key}>
                      <div className="friend-bipolar-slider__label friend-trait__head">
                        <span className="friend-bipolar-slider__icon" aria-hidden="true">
                          {meta.icon}
                        </span>
                        <label htmlFor={`friend-trait-${meta.key}`}>{meta.label}</label>
                      </div>
                      <div className="friend-bipolar-slider__track">
                        <span className="friend-bipolar-slider__pole is-low">{meta.low}</span>
                        <input
                          id={`friend-trait-${meta.key}`}
                          data-testid={`friend-trait-slider-${meta.key}`}
                          type="range"
                          min={meta.min}
                          max={meta.max}
                          step={0.01}
                          value={draft.traits[meta.key]}
                          style={{ '--slider-pct': `${sliderPct}%` } as CSSProperties}
                          onChange={(event) =>
                            edit({ traits: clampFriendTraits({ ...draft.traits, [meta.key]: Number(event.target.value) }) })
                          }
                        />
                        <span className="friend-bipolar-slider__pole is-high">{meta.high}</span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </section>

            <details
              className="friend-more-settings friend-advanced"
              data-testid="friend-more-settings"
              open={moreSettingsOpen}
              onToggle={(event) => setMoreSettingsOpen(event.currentTarget.open)}
            >
              <summary className="friend-more-settings__summary">
                <div className="friend-more-settings__summary-text">
                  <strong>Mais ajustes</strong>
                  <span>Preferências avançadas de linguagem, foco e contexto.</span>
                </div>
              </summary>

              <div className="friend-more-settings__content">
                <div className="friend-presets" role="radiogroup" aria-label="Estilo de personalidade">
                  {PERSONALITY_PRESETS.map((preset) => (
                    <button
                      key={preset.id}
                      type="button"
                      role="radio"
                      aria-checked={activePreset === preset.id}
                      className={`friend-preset${activePreset === preset.id ? ' is-checked' : ''}`}
                      data-testid={`friend-preset-${preset.id}`}
                      onClick={() => edit({ traits: { ...preset.traits } })}
                    >
                      <strong>{preset.label}</strong>
                      <span>{preset.description}</span>
                    </button>
                  ))}
                </div>

                {SECONDARY_TRAIT_META.map((meta) => (
                  <div key={meta.key} className="friend-trait">
                    <div className="friend-trait__head">
                      <label htmlFor={`friend-trait-${meta.key}`}>{meta.label}</label>
                      <span className="voice-field__value">{Math.round(draft.traits[meta.key] * 100)}</span>
                    </div>
                    <input
                      id={`friend-trait-${meta.key}`}
                      data-testid={`friend-trait-slider-${meta.key}`}
                      type="range"
                      min={meta.min}
                      max={meta.max}
                      step={0.01}
                      value={draft.traits[meta.key]}
                      onChange={(event) =>
                        edit({ traits: clampFriendTraits({ ...draft.traits, [meta.key]: Number(event.target.value) }) })
                      }
                    />
                    <div className="friend-trait__ends" aria-hidden="true">
                      <span>{meta.low}</span>
                      <span>{meta.high}</span>
                    </div>
                  </div>
                ))}

                <div className="voice-field">
                  <span className="voice-field__label" id="friend-theme-label">
                    Aparência
                  </span>
                  <div className="friend-swatches" role="radiogroup" aria-labelledby="friend-theme-label">
                    {FRIEND_ORB_THEMES.map((themeId) => {
                      const theme = ORB_THEMES[themeId]
                      return (
                        <button
                          key={themeId}
                          type="button"
                          role="radio"
                          aria-checked={draft.theme === themeId}
                          className={`friend-swatch${draft.theme === themeId ? ' is-checked' : ''}`}
                          data-testid={`friend-theme-${themeId}`}
                          onClick={() => edit({ theme: themeId })}
                          title={theme.label}
                        >
                          <span
                            className="friend-swatch__dot"
                            style={{
                              background: `radial-gradient(circle at 35% 30%, ${theme.core[2]}, ${theme.core[1]} 45%, ${theme.core[0]})`,
                            }}
                          />
                          <span>{theme.label}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>

                <section className="friend-section">
                  <h3>Clima e jeito de falar</h3>
                  <div className="voice-field">
                    <label htmlFor="friend-mood-input">Humor de agora</label>
                    <input
                      id="friend-mood-input"
                      data-testid="friend-mood-input"
                      value={draft.mood}
                      maxLength={60}
                      list="friend-mood-suggestions"
                      onChange={(event) => edit({ mood: event.target.value })}
                    />
                    <datalist id="friend-mood-suggestions">
                      {MOOD_SUGGESTIONS.map((mood) => (
                        <option key={mood} value={mood} />
                      ))}
                    </datalist>
                  </div>
                  <div className="voice-field">
                    <label htmlFor="friend-style-notes">Instruções de estilo (uma por linha)</label>
                    <textarea
                      id="friend-style-notes"
                      data-testid="friend-style-notes-textarea"
                      value={draft.notes}
                      rows={4}
                      onChange={(event) => edit({ notes: event.target.value })}
                      placeholder={'Ex.: Me chama de chefe quando eu terminar algo\nFala de futebol comigo às segundas'}
                    />
                  </div>
                </section>

                <section className="friend-section">
                  <h3>Memórias e piadas internas ({memories.length})</h3>
                  {memories.length === 0 ? (
                    <p className="friend-card__muted">Nada ainda — as memórias aparecem aqui conforme vocês conversam.</p>
                  ) : (
                    <ul className="friend-memories">
                      {memories.map((memory) => (
                        <li key={memory.id} data-testid={`friend-memory-item-${memory.id}`}>
                          <span className="voice-badge is-muted">{memoryKindLabel(memory.kind)}</span>
                          <span className="friend-memories__text">{memory.content}</span>
                          <button
                            type="button"
                            className="voice-button is-quiet is-small"
                            data-testid={`delete-friend-memory-${memory.id}`}
                            onClick={() => void onDeleteMemory(memory.id)}
                            aria-label={`Esquecer memória: ${memory.content}`}
                          >
                            Esquecer
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                <section className="friend-section">
                  <h3>Arquivo SOUL.md</h3>
                  <p className="friend-card__muted">
                    Tudo isso também fica em{' '}
                    <code data-testid="friend-soul-path">{persona?.soul_markdown_path ?? '~/.orin/friend/SOUL.md'}</code>;
                    editou lá fora? Sincronize.
                  </p>
                  <button
                    type="button"
                    className="voice-button"
                    data-testid="sync-friend-soul-button"
                    disabled={isSyncing}
                    onClick={() => void handleSync()}
                  >
                    {isSyncing ? 'Sincronizando…' : 'Sincronizar SOUL.md'}
                  </button>
                </section>
              </div>
            </details>
          </div>
        </div>

        <footer className="friend-drawer__footer">
          <button
            type="button"
            className="voice-button is-quiet friend-drawer__restore"
            data-testid="friend-restore-defaults-button"
            onClick={handleRestoreDefaults}
          >
            <span aria-hidden="true">↺</span>
            <span>Restaurar padrão</span>
          </button>
          <span className={`friend-drawer__status is-${status?.tone ?? 'idle'}`} role="status" data-testid="friend-save-status">
            {status?.text ?? (dirty ? 'Alterações não salvas' : '')}
          </span>
          <button
            type="button"
            className="voice-button is-primary"
            data-testid="save-friend-persona-button"
            disabled={status?.tone === 'busy' || !dirty}
            onClick={() => void handleSave()}
          >
            Salvar alterações
          </button>
        </footer>
      </aside>
    </div>
  )
}

import { useState } from 'react'
import {
  clampFriendTraits,
  type FriendBondStage,
  type FriendMemoryEntry,
  type FriendMemoryKind,
  type FriendPersonaState,
  type FriendTraitVector,
} from '../../api/conversations'

export type FriendSoulDrawerProps = {
  isOpen: boolean
  persona: FriendPersonaState | null
  memories: FriendMemoryEntry[]
  onClose: () => void
  onUpdatePersona: (input: {
    traits: FriendTraitVector
    style_notes: string[]
    current_mood: string
  }) => Promise<FriendPersonaState | void> | void
  onSyncSoul: () => Promise<FriendPersonaState | void> | void
  onDeleteMemory: (memoryId: string) => Promise<void> | void
}

type TraitMeta = {
  key: keyof FriendTraitVector
  label: string
  min: number
  max: number
  description: string
}

const TRAIT_META: ReadonlyArray<TraitMeta> = [
  {
    key: 'warmth',
    label: 'Acolhimento (Warmth)',
    min: 0.3,
    max: 0.95,
    description: 'Empatia e calor humano na conversa.',
  },
  {
    key: 'humor',
    label: 'Bom Humor (Humor)',
    min: 0.1,
    max: 0.9,
    description: 'Leveza, tiradas rápidas e espírito descontraído.',
  },
  {
    key: 'sarcasm',
    label: 'Ironia Fina (Sarcasm)',
    min: 0.0,
    max: 0.75,
    description: 'Provocação inteligente entre amigos.',
  },
  {
    key: 'formality',
    label: 'Formalidade (Formality)',
    min: 0.05,
    max: 0.85,
    description: 'Mais coloquial (baixo) vs. mais estruturado (alto).',
  },
  {
    key: 'verbosity',
    label: 'Prolixidade (Verbosity)',
    min: 0.15,
    max: 0.8,
    description: 'Respostas curtas de bate-papo vs. explicações longas.',
  },
  {
    key: 'candor',
    label: 'Sinceridade (Candor · Piso 0.50)',
    min: 0.5,
    max: 0.95,
    description: 'Honestidade direta sem bajulação artificial (mínimo travado em 0.50).',
  },
  {
    key: 'energy',
    label: 'Energia (Energy)',
    min: 0.2,
    max: 0.9,
    description: 'Ritmo e entusiasmo na voz e no texto.',
  },
]

const DEFAULT_TRAITS: FriendTraitVector = {
  warmth: 0.65,
  humor: 0.5,
  sarcasm: 0.25,
  formality: 0.35,
  verbosity: 0.4,
  candor: 0.7,
  energy: 0.55,
}

export function bondStageLabel(stage: FriendBondStage): string {
  switch (stage) {
    case 'acquaintance':
      return 'Conhecido'
    case 'buddy':
      return 'Parceiro (Buddy)'
    case 'close_friend':
      return 'Amigo Próximo'
    case 'confidant':
      return 'Confidente'
  }
}

function memoryKindLabel(kind: FriendMemoryKind): string {
  switch (kind) {
    case 'inside_joke':
      return 'Piada Interna'
    case 'milestone':
      return 'Marco'
    case 'emotional':
      return 'Emocional'
    case 'episodic':
      return 'Episódica'
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
}: FriendSoulDrawerProps) {
  const [traitOverrides, setTraitOverrides] = useState<FriendTraitVector | null>(null)
  const [moodOverride, setMoodOverride] = useState<string | null>(null)
  const [notesOverride, setNotesOverride] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)

  const draftTraits = traitOverrides ?? clampFriendTraits(persona?.traits ?? DEFAULT_TRAITS)
  const draftMood = moodOverride ?? persona?.current_mood ?? 'neutro'
  const draftNotesText = notesOverride ?? (persona?.style_notes ?? []).join('\n')

  if (!isOpen) return null

  const affinity = persona?.affinity_score ?? 0
  const bondStage = persona?.bond_stage ?? 'acquaintance'

  function handleTraitChange(key: keyof FriendTraitVector, rawValue: string) {
    const parsed = Number.parseFloat(rawValue)
    const nextRaw = {
      ...draftTraits,
      [key]: Number.isFinite(parsed) ? parsed : draftTraits[key],
    }
    setTraitOverrides(clampFriendTraits(nextRaw))
  }

  async function handleSave() {
    setIsSaving(true)
    try {
      const clamped = clampFriendTraits(draftTraits)
      const notes = draftNotesText
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
      await onUpdatePersona({
        traits: clamped,
        style_notes: notes,
        current_mood: draftMood.trim() || 'neutro',
      })
    } finally {
      setIsSaving(false)
    }
  }

  async function handleSync() {
    setIsSyncing(true)
    try {
      await onSyncSoul()
      setTraitOverrides(null)
      setMoodOverride(null)
      setNotesOverride(null)
    } finally {
      setIsSyncing(false)
    }
  }

  return (
    <aside
      className="friend-soul-drawer"
      data-testid="friend-soul-drawer"
      aria-label="Personalidade e Memórias do Orin Friend"
      style={{
        position: 'fixed',
        top: 0,
        right: 0,
        bottom: 0,
        width: 'min(440px, 94vw)',
        zIndex: 80,
        background: '#100E20',
        color: '#F4F2FF',
        borderLeft: '1px solid rgba(167, 139, 250, 0.24)',
        boxShadow: '-16px 0 48px rgba(7, 6, 17, 0.72)',
        display: 'flex',
        flexDirection: 'column',
        overflowY: 'auto',
        padding: '1.25rem',
        gap: '1.25rem',
      }}
    >
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.75rem' }}>
        <div>
          <span
            style={{
              fontSize: '0.72rem',
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              color: '#A78BFA',
              fontWeight: 600,
            }}
          >
            ◈ Personalidade Evolutiva · SOUL.md
          </span>
          <h2 style={{ margin: '0.2rem 0 0', fontSize: '1.15rem' }}>Orin Friend</h2>
        </div>
        <button
          type="button"
          className="btn-ghost"
          data-testid="close-friend-soul-drawer-button"
          onClick={onClose}
          aria-label="Fechar painel do Orin Friend"
        >
          ✕
        </button>
      </header>

      <section
        style={{
          padding: '0.9rem 1rem',
          borderRadius: '0.75rem',
          background: 'rgba(139, 92, 246, 0.1)',
          border: '1px solid rgba(167, 139, 250, 0.25)',
          display: 'grid',
          gap: '0.5rem',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <strong data-testid="friend-bond-stage-label">{bondStageLabel(bondStage)}</strong>
          <span
            data-testid="friend-affinity-score"
            style={{ fontFamily: 'var(--font-mono, monospace)', color: '#C4B5FD', fontWeight: 600 }}
          >
            Afinidade: {affinity.toFixed(1)}%
          </span>
        </div>
        <div
          style={{
            height: '6px',
            borderRadius: '999px',
            background: 'rgba(255, 255, 255, 0.08)',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              width: `${Math.min(100, Math.max(0, affinity))}%`,
              height: '100%',
              background: 'linear-gradient(90deg, #8B5CF6, #C4B5FD)',
            }}
          />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.82rem' }}>
          <span>
            Humor atual: <strong>{draftMood}</strong>
          </span>
          <button
            type="button"
            className="btn-secondary"
            data-testid="friend-soul-sync-button"
            disabled={isSyncing}
            onClick={() => void handleSync()}
            style={{ fontSize: '0.76rem', padding: '0.25rem 0.6rem' }}
          >
            {isSyncing ? 'Sincronizando…' : 'Sincronizar SOUL.md'}
          </button>
        </div>
        {persona?.soul_markdown_path && (
          <code style={{ fontSize: '0.72rem', opacity: 0.72, wordBreak: 'break-all' }}>
            {persona.soul_markdown_path}
          </code>
        )}
      </section>

      <section style={{ display: 'grid', gap: '0.8rem' }}>
        <h3 style={{ margin: 0, fontSize: '0.95rem', color: '#E9D5FF' }}>7 Traços de Personalidade</h3>
        {TRAIT_META.map((trait) => {
          const val = draftTraits[trait.key]
          return (
            <div key={trait.key} style={{ display: 'grid', gap: '0.2rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.84rem' }}>
                <label htmlFor={`trait-slider-${trait.key}`}>{trait.label}</label>
                <span style={{ fontFamily: 'var(--font-mono, monospace)', color: '#A78BFA' }}>
                  {val.toFixed(2)}
                </span>
              </div>
              <input
                id={`trait-slider-${trait.key}`}
                data-testid={`trait-slider-${trait.key}`}
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={val}
                onChange={(e) => handleTraitChange(trait.key, e.target.value)}
              />
              <span style={{ fontSize: '0.74rem', opacity: 0.7 }}>{trait.description}</span>
            </div>
          )
        })}

        <div style={{ display: 'grid', gap: '0.35rem', marginTop: '0.25rem' }}>
          <label htmlFor="friend-mood-input" style={{ fontSize: '0.84rem' }}>
            Humor / Estado de espírito atual
          </label>
          <input
            id="friend-mood-input"
            data-testid="friend-mood-input"
            type="text"
            value={draftMood}
            onChange={(e) => setMoodOverride(e.target.value)}
          />
        </div>

        <div style={{ display: 'grid', gap: '0.35rem' }}>
          <label htmlFor="friend-style-notes-textarea" style={{ fontSize: '0.84rem' }}>
            Notas de estilo aprendidas (1 por linha)
          </label>
          <textarea
            id="friend-style-notes-textarea"
            data-testid="friend-style-notes-textarea"
            rows={3}
            value={draftNotesText}
            onChange={(e) => setNotesOverride(e.target.value)}
          />
        </div>

        <button
          type="button"
          className="btn-primary"
          data-testid="friend-soul-save-button"
          disabled={isSaving}
          onClick={() => void handleSave()}
        >
          {isSaving ? 'Salvando…' : 'Salvar Personalidade'}
        </button>
      </section>

      <section style={{ display: 'grid', gap: '0.65rem' }}>
        <h3 style={{ margin: 0, fontSize: '0.95rem', color: '#E9D5FF' }}>
          Memórias Afetivas & Piadas Internas ({memories.length})
        </h3>
        {memories.length === 0 ? (
          <p style={{ fontSize: '0.84rem', opacity: 0.7, margin: 0 }}>
            Nenhuma memória afetiva registrada ainda. Converse com o Orin Friend para criar histórias e piadas internas!
          </p>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: '0.55rem' }}>
            {memories.map((mem) => (
              <li
                key={mem.id}
                data-testid={`friend-memory-item-${mem.id}`}
                style={{
                  padding: '0.7rem 0.85rem',
                  borderRadius: '0.65rem',
                  background: 'rgba(255, 255, 255, 0.04)',
                  border: '1px solid rgba(255, 255, 255, 0.08)',
                  display: 'grid',
                  gap: '0.35rem',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem' }}>
                  <span
                    style={{
                      fontSize: '0.72rem',
                      padding: '0.12rem 0.45rem',
                      borderRadius: '999px',
                      background: 'rgba(139, 92, 246, 0.2)',
                      color: '#C4B5FD',
                    }}
                  >
                    {memoryKindLabel(mem.kind)}
                    {mem.emotion_tag ? ` · ${mem.emotion_tag}` : ''}
                  </span>
                  <button
                    type="button"
                    className="btn-ghost"
                    data-testid={`delete-friend-memory-${mem.id}`}
                    onClick={() => void onDeleteMemory(mem.id)}
                    aria-label={`Excluir memória ${mem.id}`}
                    style={{ fontSize: '0.75rem', padding: '0.15rem 0.45rem' }}
                  >
                    Excluir
                  </button>
                </div>
                <p style={{ margin: 0, fontSize: '0.86rem', lineHeight: 1.45 }}>{mem.content}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </aside>
  )
}

import { useState, type FormEvent } from 'react'
import type {
  SkillProposal,
  SkillsClient,
  UpdateSkillProposalInput,
} from '../../api/skills'

type SkillProposalsSectionProps = {
  client: SkillsClient
  proposals: SkillProposal[]
  onProposalUpdated: (updated: SkillProposal) => void
  onProposalApproved: (proposalId: string) => void
  onProposalDismissed: (proposalId: string) => void
}

function splitLines(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

function splitCommaTags(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

export function SkillProposalsSection({
  client,
  proposals,
  onProposalUpdated,
  onProposalApproved,
  onProposalDismissed,
}: SkillProposalsSectionProps) {
  const [expandedById, setExpandedById] = useState<Record<string, boolean>>({})
  const [editingId, setEditingId] = useState<string | null>(null)
  const [busyById, setBusyById] = useState<Record<string, boolean>>({})
  const [errorById, setErrorById] = useState<Record<string, string>>({})

  if (proposals.length === 0) {
    return null
  }

  const pendingLabel =
    proposals.length === 1 ? '1 pendente' : `${proposals.length} pendentes`

  async function handleApprove(proposalId: string, edits?: UpdateSkillProposalInput) {
    if (!client.approveProposal || busyById[proposalId]) return
    setBusyById((prev) => ({ ...prev, [proposalId]: true }))
    setErrorById((prev) => ({ ...prev, [proposalId]: '' }))
    try {
      await client.approveProposal(proposalId, edits)
      setEditingId((prev) => (prev === proposalId ? null : prev))
      onProposalApproved(proposalId)
    } catch {
      setErrorById((prev) => ({
        ...prev,
        [proposalId]: 'Não foi possível aprovar a proposta. Tente novamente.',
      }))
    } finally {
      setBusyById((prev) => ({ ...prev, [proposalId]: false }))
    }
  }

  async function handleDismiss(proposalId: string) {
    if (!client.dismissProposal || busyById[proposalId]) return
    setBusyById((prev) => ({ ...prev, [proposalId]: true }))
    setErrorById((prev) => ({ ...prev, [proposalId]: '' }))
    try {
      await client.dismissProposal(proposalId)
      setEditingId((prev) => (prev === proposalId ? null : prev))
      onProposalDismissed(proposalId)
    } catch {
      setErrorById((prev) => ({
        ...prev,
        [proposalId]: 'Não foi possível descartar a proposta. Tente novamente.',
      }))
    } finally {
      setBusyById((prev) => ({ ...prev, [proposalId]: false }))
    }
  }

  async function handleSaveEdits(proposalId: string, input: UpdateSkillProposalInput) {
    if (!client.updateProposal || busyById[proposalId]) return
    setBusyById((prev) => ({ ...prev, [proposalId]: true }))
    setErrorById((prev) => ({ ...prev, [proposalId]: '' }))
    try {
      const updated = await client.updateProposal(proposalId, input)
      onProposalUpdated(updated)
      setEditingId((prev) => (prev === proposalId ? null : prev))
    } catch {
      setErrorById((prev) => ({
        ...prev,
        [proposalId]: 'Não foi possível salvar as alterações da proposta.',
      }))
    } finally {
      setBusyById((prev) => ({ ...prev, [proposalId]: false }))
    }
  }

  return (
    <section
      className="skill-proposals-section"
      data-testid="skill-proposals-section"
      aria-label="Sugestões de Habilidades Aprendidas"
      style={{
        display: 'grid',
        gap: '14px',
        padding: '18px 20px',
        borderRadius: '16px',
        border: '1px solid var(--orin-accent-border, var(--orin-border))',
        background: 'var(--orin-surface-2, var(--raised))',
      }}
    >
      <header
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'space-between',
          gap: '12px',
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'grid', gap: '4px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
            <h2
              style={{
                margin: 0,
                fontSize: '16px',
                fontWeight: 650,
                color: 'var(--orin-text-primary, var(--text))',
              }}
            >
              Sugestões de Habilidades Aprendidas
            </h2>
            <span className="skill-pill is-custom">{pendingLabel}</span>
          </div>
          <p
            style={{
              margin: 0,
              fontSize: '13px',
              lineHeight: 1.5,
              color: 'var(--orin-text-secondary, var(--muted))',
            }}
          >
            Extraídas automaticamente de fluxos validados nas suas conversas. Nenhuma habilidade
            entra em vigor sem a sua revisão e aprovação.
          </p>
        </div>
      </header>

      <div style={{ display: 'grid', gap: '12px' }}>
        {proposals.map((proposal) => {
          const isExpanded = Boolean(expandedById[proposal.proposal_id])
          const isEditing = editingId === proposal.proposal_id
          const isBusy = Boolean(busyById[proposal.proposal_id])
          const errorMessage = errorById[proposal.proposal_id] ?? ''

          return (
            <article
              key={proposal.proposal_id}
              data-testid={`skill-proposal-card-${proposal.proposal_id}`}
              style={{
                display: 'grid',
                gap: '12px',
                padding: '16px',
                borderRadius: '12px',
                border: '1px solid var(--orin-border, var(--line))',
                background: 'var(--orin-surface-1, var(--surface))',
              }}
            >
              {isEditing ? (
                <SkillProposalEditForm
                  proposal={proposal}
                  busy={isBusy}
                  errorMessage={errorMessage}
                  onCancel={() => {
                    setEditingId(null)
                    setErrorById((prev) => ({ ...prev, [proposal.proposal_id]: '' }))
                  }}
                  onSave={(input) => void handleSaveEdits(proposal.proposal_id, input)}
                  onSaveAndApprove={(input) => void handleApprove(proposal.proposal_id, input)}
                />
              ) : (
                <>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      justifyContent: 'space-between',
                      gap: '12px',
                      flexWrap: 'wrap',
                    }}
                  >
                    <div style={{ display: 'grid', gap: '6px', minWidth: 0, flex: 1 }}>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                          flexWrap: 'wrap',
                        }}
                      >
                        <strong
                          style={{
                            fontSize: '15px',
                            fontWeight: 650,
                            color: 'var(--orin-text-primary, var(--text))',
                          }}
                        >
                          {proposal.name}
                        </strong>
                        <span
                          className={`skill-pill ${
                            proposal.action === 'create' ? 'is-custom' : 'is-installed'
                          }`}
                        >
                          {proposal.action === 'create' ? 'Nova skill' : 'Atualização'}
                        </span>
                      </div>
                      <p
                        style={{
                          margin: 0,
                          fontSize: '13px',
                          lineHeight: 1.5,
                          color: 'var(--orin-text-secondary, var(--muted))',
                        }}
                      >
                        {proposal.description}
                      </p>
                    </div>

                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        flexWrap: 'wrap',
                      }}
                    >
                      <button
                        type="button"
                        className="button button--primary"
                        disabled={isBusy}
                        onClick={() => void handleApprove(proposal.proposal_id)}
                      >
                        {isBusy ? 'Processando…' : 'Aprovar'}
                      </button>
                      <button
                        type="button"
                        className="button button--secondary"
                        disabled={isBusy}
                        onClick={() => {
                          setErrorById((prev) => ({ ...prev, [proposal.proposal_id]: '' }))
                          setEditingId(proposal.proposal_id)
                        }}
                      >
                        Editar
                      </button>
                      <button
                        type="button"
                        className="button button--quiet"
                        disabled={isBusy}
                        onClick={() => void handleDismiss(proposal.proposal_id)}
                      >
                        Descartar
                      </button>
                    </div>
                  </div>

                  {(proposal.tags.length > 0 || proposal.requires_tools.length > 0) && (
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        flexWrap: 'wrap',
                      }}
                    >
                      {proposal.tags.map((tag) => (
                        <span key={`tag-${tag}`} className="skill-tag">
                          {tag}
                        </span>
                      ))}
                      {proposal.requires_tools.map((tool) => (
                        <code
                          key={`tool-${tool}`}
                          className="skill-tag"
                          style={{ color: 'var(--orin-accent, var(--signal-dim))' }}
                        >
                          {tool}
                        </code>
                      ))}
                    </div>
                  )}

                  <div className="skill-instructions-accordion">
                    <button
                      type="button"
                      className="skill-instructions-accordion__trigger"
                      aria-expanded={isExpanded}
                      onClick={() =>
                        setExpandedById((prev) => ({
                          ...prev,
                          [proposal.proposal_id]: !prev[proposal.proposal_id],
                        }))
                      }
                    >
                      <span>Ver instruções propostas</span>
                      <span aria-hidden="true">{isExpanded ? '▴' : '▾'}</span>
                    </button>

                    {isExpanded && (
                      <div
                        className="skill-instructions-accordion__body"
                        style={{ display: 'grid', gap: '10px' }}
                      >
                        {proposal.when_to_use.length > 0 && (
                          <div>
                            <strong
                              style={{
                                display: 'block',
                                marginBottom: '4px',
                                fontSize: '11.5px',
                                color: 'var(--orin-text-secondary, var(--muted))',
                              }}
                            >
                              Quando usar
                            </strong>
                            <ul className="skills-metadata">
                              {proposal.when_to_use.map((item) => (
                                <li key={item}>{item}</li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {proposal.when_not_to_use.length > 0 && (
                          <div>
                            <strong
                              style={{
                                display: 'block',
                                marginBottom: '4px',
                                fontSize: '11.5px',
                                color: 'var(--orin-text-secondary, var(--muted))',
                              }}
                            >
                              Quando não usar
                            </strong>
                            <ul className="skills-metadata">
                              {proposal.when_not_to_use.map((item) => (
                                <li key={item}>{item}</li>
                              ))}
                            </ul>
                          </div>
                        )}

                        <pre className="skills-instructions">{proposal.instructions}</pre>
                      </div>
                    )}
                  </div>

                  {errorMessage && (
                    <p role="alert" className="skills-form__error">
                      {errorMessage}
                    </p>
                  )}
                </>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}

function SkillProposalEditForm({
  proposal,
  busy,
  errorMessage,
  onCancel,
  onSave,
  onSaveAndApprove,
}: {
  proposal: SkillProposal
  busy: boolean
  errorMessage: string
  onCancel: () => void
  onSave: (input: UpdateSkillProposalInput) => void
  onSaveAndApprove: (input: UpdateSkillProposalInput) => void
}) {
  const [name, setName] = useState(proposal.name)
  const [description, setDescription] = useState(proposal.description)
  const [instructions, setInstructions] = useState(proposal.instructions)
  const [tags, setTags] = useState(proposal.tags.join(', '))
  const [whenToUse, setWhenToUse] = useState(proposal.when_to_use.join('\n'))
  const [whenNotToUse, setWhenNotToUse] = useState(proposal.when_not_to_use.join('\n'))

  function buildInput(): UpdateSkillProposalInput | null {
    const trimmedName = name.trim()
    const trimmedDescription = description.trim()
    const trimmedInstructions = instructions.trim()
    if (!trimmedName || !trimmedDescription || !trimmedInstructions) {
      return null
    }
    return {
      name: trimmedName,
      description: trimmedDescription,
      instructions: trimmedInstructions,
      tags: splitCommaTags(tags),
      when_to_use: splitLines(whenToUse),
      when_not_to_use: splitLines(whenNotToUse),
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const input = buildInput()
    if (!input || busy) return
    onSave(input)
  }

  function handleSaveAndApproveClick() {
    const input = buildInput()
    if (!input || busy) return
    onSaveAndApprove(input)
  }

  return (
    <form className="skills-form" onSubmit={handleSubmit} style={{ marginTop: 0 }}>
      <div className="skills-form__heading">
        <div>
          <p className="eyebrow">REVISAR PROPOSTA</p>
          <h2>Ajustar habilidade antes de aprovar</h2>
        </div>
        <button type="button" className="button button--quiet" onClick={onCancel} disabled={busy}>
          Cancelar
        </button>
      </div>

      <label>
        Nome da habilidade
        <input
          required
          value={name}
          onChange={(event) => setName(event.target.value)}
          disabled={busy}
        />
      </label>

      <label>
        Descrição da proposta
        <textarea
          required
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          disabled={busy}
        />
      </label>

      <label>
        Instruções (SKILL.md)
        <textarea
          required
          value={instructions}
          onChange={(event) => setInstructions(event.target.value)}
          disabled={busy}
        />
      </label>

      <label>
        Tags
        <input
          value={tags}
          onChange={(event) => setTags(event.target.value)}
          placeholder="debugging, database"
          disabled={busy}
        />
      </label>

      <label>
        Quando usar (uma condição por linha)
        <textarea
          value={whenToUse}
          onChange={(event) => setWhenToUse(event.target.value)}
          disabled={busy}
        />
      </label>

      <label>
        Quando não usar (uma condição por linha)
        <textarea
          value={whenNotToUse}
          onChange={(event) => setWhenNotToUse(event.target.value)}
          disabled={busy}
        />
      </label>

      {errorMessage && (
        <p role="alert" className="skills-form__error">
          {errorMessage}
        </p>
      )}

      <div className="skills-form__actions" style={{ gap: '8px' }}>
        <button type="submit" className="button button--secondary" disabled={busy}>
          {busy ? 'Salvando…' : 'Salvar alterações'}
        </button>
        <button
          type="button"
          className="button button--primary"
          disabled={busy}
          onClick={handleSaveAndApproveClick}
        >
          {busy ? 'Aprovando…' : 'Salvar e aprovar'}
        </button>
      </div>
    </form>
  )
}

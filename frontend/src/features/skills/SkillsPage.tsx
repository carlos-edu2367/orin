import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { createBrowserApiClient } from '../../api/client'
import { createSkillsClient, type AgentSkillMode, type SkillAgent, type CreateSkillInput, type SkillDetail, type SkillList, type SkillSummary, type SkillsClient } from '../../api/skills'
import { CommandPalette } from '../../components/CommandPalette'
import { Brand } from '../../components/Brand'
import { Disclosure } from '../../components/ui/Disclosure'

type SkillsPageProps = { client?: SkillsClient; embedded?: boolean }

type ListState =
  | { status: 'loading' }
  | { status: 'loaded'; value: SkillList }
  | { status: 'error' }

type DetailState =
  | { status: 'loading' }
  | { status: 'loaded'; value: SkillDetail }
  | { status: 'error' }

export function SkillsPage({ client, embedded = false }: SkillsPageProps) {
  const { skillId } = useParams()
  const apiClient = useMemo(() => client ?? createSkillsClient(createBrowserApiClient()), [client])

  return skillId ? <SkillDetailPage client={apiClient} skillId={skillId} embedded={embedded} /> : <SkillsLibrary client={apiClient} embedded={embedded} />
}

export function SkillsLibrary({ client, embedded = false }: { client: SkillsClient; embedded?: boolean }) {
  const [query, setQuery] = useState('')
  const [source, setSource] = useState('')
  const [list, setList] = useState<ListState>({ status: 'loading' })
  const [creating, setCreating] = useState(false)
  const [reload, setReload] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const [moreError, setMoreError] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    client.list({ query, source: source || undefined }, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setList({ status: 'loaded', value }) })
      .catch(() => { if (!controller.signal.aborted) setList({ status: 'error' }) })
    return () => controller.abort()
  }, [client, query, reload, source])

  const sources = list.status === 'loaded' ? [...new Set(list.value.items.map((item) => item.source))].sort() : []

  function clearPaginationForBaseRefresh() {
    setLoadingMore(false)
    setMoreError(false)
  }

  async function loadMore() {
    if (loadingMore || list.status !== 'loaded' || !list.value.next_cursor) return
    const cursor = list.value.next_cursor
    const controller = new AbortController()
    setLoadingMore(true)
    setMoreError(false)
    try {
      const next = await client.list({ query, source: source || undefined, cursor }, controller.signal)
      setList((current) => current.status === 'loaded' && current.value.next_cursor === cursor
        ? { status: 'loaded', value: { items: [...current.value.items, ...next.items], next_cursor: next.next_cursor } }
        : current)
    } catch {
      setMoreError(true)
    } finally {
      setLoadingMore(false)
    }
  }

  const content = (
    <section className="skills-library" aria-labelledby="skills-title">
      <div className="skills-library__heading">
        <div>
          <p className="eyebrow">SKILLS / PROCEDIMENTOS</p>
          <h1 id="skills-title">Skills</h1>
          <p>Procedimentos reutilizáveis que os agentes descobrem por metadados e carregam apenas quando necessários.</p>
        </div>
        <button type="button" className="button button--primary" onClick={() => setCreating(true)}>Criar skill</button>
      </div>

      {creating ? <CreateSkillForm
        client={client}
        onCancel={() => setCreating(false)}
        onCreated={(created) => {
          setList((current) => current.status === 'loaded'
            ? { status: 'loaded', value: { ...current.value, items: [created, ...current.value.items] } }
            : current)
          setCreating(false)
        }}
      /> : <>
        <label className="skills-search" htmlFor="skills-search">
          <span>Buscar skills</span>
          <input id="skills-search" type="search" value={query} onChange={(event) => { clearPaginationForBaseRefresh(); setQuery(event.target.value) }} placeholder="Nome, descrição ou tag" />
        </label>
        {sources.length > 0 && <div className="skills-filters" aria-label="Filtrar por origem">
          <button type="button" className={`ghost-button ${source === '' ? 'is-active' : ''}`} aria-pressed={source === ''} onClick={() => { clearPaginationForBaseRefresh(); setSource('') }}>Todas</button>
          {sources.map((item) => <button key={item} type="button" className={`ghost-button ${source === item ? 'is-active' : ''}`} aria-pressed={source === item} onClick={() => { clearPaginationForBaseRefresh(); setSource(item) }}>{item}</button>)}
        </div>}
        <SkillRows state={list} onRetry={() => { clearPaginationForBaseRefresh(); setReload((value) => value + 1) }} onLoadMore={() => void loadMore()} loadingMore={loadingMore} moreError={moreError} embedded={embedded} />
        <AgentSkillsPanel client={client} skills={list.status === 'loaded' ? list.value.items : []} />
      </>}
    </section>
  )
  if (embedded) return content
  return (
    <main className="app-shell skills-shell">
      <header className="topbar">
        <Brand to="/" />
        <span className="topbar__context">Biblioteca de skills</span>
        <CommandPalette />
      </header>

      {content}
    </main>
  )
}

function SkillRows({ state, onRetry, onLoadMore, loadingMore, moreError, embedded = false }: { state: ListState; onRetry: () => void; onLoadMore: () => void; loadingMore: boolean; moreError: boolean; embedded?: boolean }) {
  if (state.status === 'loading') return <p className="skills-state" role="status">Carregando skills…</p>
  if (state.status === 'error') return <div className="skills-state skills-state--error" role="alert">Não foi possível carregar as skills.<button type="button" className="button button--secondary" onClick={onRetry}>Tentar novamente</button></div>
  if (state.value.items.length === 0) return <p className="skills-state">Nenhuma skill corresponde à busca atual.</p>
  return <><ul className="skills-list" aria-label="Skills instaladas">
    {state.value.items.map((skill) => <li key={skill.id}>
      <Link className="skill-row" to={`${embedded ? '/settings/skills' : '/skills'}/${encodeURIComponent(skill.id)}`}>
        <span className="skill-row__main"><strong>{skill.name}</strong><span>{skill.description}</span></span>
        <span className="skill-row__meta"><code>v{skill.version}</code><span>{skill.source}</span><span aria-label={skill.available ? 'Disponível' : 'Indisponível'} className={skill.available ? 'skill-row__availability is-available' : 'skill-row__availability'}>{skill.available ? 'Disponível' : 'Indisponível'}</span></span>
      </Link>
    </li>)}
  </ul>{state.value.next_cursor && <button type="button" className="button button--secondary skills-load-more" onClick={onLoadMore} disabled={loadingMore}>{loadingMore ? 'Carregando…' : 'Carregar mais skills'}</button>}{moreError && <div className="skills-more-error" role="alert">Não foi possível carregar mais skills.<button type="button" className="button button--secondary" onClick={onLoadMore}>Tentar carregar mais</button></div>}</>
}

export function AgentSkillsPanel({ client, skills }: { client: SkillsClient; skills: SkillSummary[] }) {
  const [agentId, setAgentId] = useState('agent:main')
  const [mode, setMode] = useState<AgentSkillMode>('auto')
  const [pinned, setPinned] = useState<string[]>([])
  const [associationItems, setAssociationItems] = useState<SkillSummary[]>([])
  const [status, setStatus] = useState<'idle' | 'loading' | 'loaded' | 'saving' | 'error'>('idle')
  const [error, setError] = useState('')
  const selected = new Set(pinned)

  const availableSkills = useMemo(() => {
    const byId = new Map(associationItems.map((skill) => [skill.id, skill]))
    skills.forEach((skill) => byId.set(skill.id, skill))
    return [...byId.values()]
  }, [associationItems, skills])
  const associatedSkills = availableSkills.filter((skill) => selected.has(skill.id))

  async function load() {
    if (!agentId.trim() || status === 'loading' || status === 'saving') return
    setStatus('loading')
    setError('')
    try {
      const association = await client.getAgentSkills(agentId.trim())
      setMode(association.mode)
      setPinned(association.items.map((item) => item.id))
      setAssociationItems(association.items)
      setStatus('loaded')
    } catch {
      setError('Não foi possível carregar as skills deste agente.')
      setStatus('error')
    }
  }

  async function save() {
    if (!agentId.trim() || status !== 'loaded') return
    setStatus('saving')
    setError('')
    try {
      const association = await client.setAgentSkills(agentId.trim(), { mode, skill_ids: mode === 'pinned' ? pinned : [] })
      setMode(association.mode)
      setPinned(association.items.map((item) => item.id))
      setAssociationItems(association.items)
      setStatus('loaded')
    } catch {
      setError('Não foi possível salvar as skills deste agente.')
      setStatus('loaded')
    }
  }

  function togglePinned(skillId: string) {
    setPinned((current) => current.includes(skillId) ? current.filter((item) => item !== skillId) : [...current, skillId])
  }

  return <section className="agent-skills" aria-labelledby="agent-skills-title">
    <div><p className="eyebrow">CONFIGURAÇÃO DO AGENTE</p><h2 id="agent-skills-title">Skills do agente</h2><p>Defina se o agente descobre skills automaticamente ou trabalha com uma seleção fixada.</p></div>
    <div className="agent-skills__load">
      <label>ID do agente<input value={agentId} onChange={(event) => setAgentId(event.target.value)} /></label>
      <button type="button" className="button button--secondary" onClick={() => void load()} disabled={status === 'loading' || status === 'saving'}>{status === 'loading' ? 'Carregando…' : 'Carregar configuração'}</button>
    </div>
    {status === 'loaded' || status === 'saving' ? <>
      <fieldset className="agent-skills__mode">
        <legend>Modo de descoberta</legend>
        <label><input type="radio" name="agent-skill-mode" checked={mode === 'auto'} onChange={() => setMode('auto')} />Auto discover</label>
        <label><input type="radio" name="agent-skill-mode" checked={mode === 'pinned'} onChange={() => setMode('pinned')} />Skills fixadas</label>
      </fieldset>
      <div className="agent-skills__pinned" aria-disabled={mode !== 'pinned'}>
        <p>Skills fixadas</p>
        {associatedSkills.length > 0 && <ul aria-label="Skills fixadas">{associatedSkills.map((skill) => <li key={skill.id}>{skill.name}</li>)}</ul>}
        <div className="agent-skills__picker">
          {availableSkills.map((skill) => <label key={skill.id}><input type="checkbox" checked={selected.has(skill.id)} disabled={mode !== 'pinned'} onChange={() => togglePinned(skill.id)} />{skill.name}</label>)}
        </div>
      </div>
      <button type="button" className="button button--primary" onClick={() => void save()} disabled={status === 'saving'}>{status === 'saving' ? 'Salvando…' : 'Salvar configuração'}</button>
    </> : <p className="agent-skills__hint">Carregue uma configuração para editar as skills fixadas.</p>}
    {error && <p className="agent-skills__error" role="alert">{error}</p>}
  </section>
}

function SkillDetailPage({ client, skillId, embedded = false }: { client: SkillsClient; skillId: string; embedded?: boolean }) {
  const [detail, setDetail] = useState<DetailState>({ status: 'loading' })
  const [reload, setReload] = useState(0)
  const [editing, setEditing] = useState(false)
  const [usedBy, setUsedBy] = useState<SkillAgent[]>([])
  const [removingVersion, setRemovingVersion] = useState<string | null>(null)
  const [removeError, setRemoveError] = useState<string | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    client.get(skillId, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setDetail({ status: 'loaded', value }) })
      .catch(() => { if (!controller.signal.aborted) setDetail({ status: 'error' }) })
    return () => controller.abort()
  }, [client, reload, skillId])

  useEffect(() => {
    const controller = new AbortController()
    client.listSkillAgents(skillId, controller.signal).then((items) => {
      if (!controller.signal.aborted) setUsedBy(items)
    }).catch(() => undefined)
    return () => controller.abort()
  }, [client, skillId])

  async function removeVersion(version: string) {
    if (removingVersion || detail.status !== 'loaded' || version === detail.value.version) return
    if (!window.confirm(`Desinstalar a versão ${version}? Ela será removida desta instalação.`)) return
    setRemovingVersion(version)
    setRemoveError(null)
    try {
      const next = await client.removeVersion(skillId, version)
      setDetail({ status: 'loaded', value: next })
    } catch {
      setRemoveError('Não foi possível desinstalar essa versão. Ela pode estar sendo usada por um agente.')
    } finally {
      setRemovingVersion(null)
    }
  }

  const content = <section className="skills-detail" aria-live="polite">
      {detail.status === 'loading' && <p className="skills-state" role="status">Carregando skill…</p>}
      {detail.status === 'error' && <div className="skills-state skills-state--error" role="alert">Não foi possível carregar esta skill.<button type="button" className="button button--secondary" onClick={() => setReload((value) => value + 1)}>Tentar novamente</button></div>}
      {detail.status === 'loaded' && (editing
        ? <EditSkillForm client={client} skill={detail.value} onCancel={() => setEditing(false)} onUpdated={(value) => { setDetail({ status: 'loaded', value }); setEditing(false) }} />
        : <SkillDetailView skill={detail.value} usedBy={usedBy} onEdit={() => setEditing(true)} onRemoveVersion={(version) => void removeVersion(version)} removingVersion={removingVersion} removeError={removeError} />)}
    </section>
  if (embedded) return content
  return <main className="app-shell skills-shell">
    <header className="topbar">
      <Brand to="/" />
      <span className="topbar__context">Detalhe da skill</span>
      <Link className="topbar__back" to="/skills">Voltar às skills</Link>
    </header>
    {content}
  </main>
}

function SkillDetailView({ skill, usedBy, onEdit, onRemoveVersion, removingVersion, removeError }: { skill: SkillDetail; usedBy: SkillAgent[]; onEdit: () => void; onRemoveVersion: (version: string) => void; removingVersion: string | null; removeError: string | null }) {
  return <>
    <p className="eyebrow">{skill.source} / {skill.available ? 'DISPONÍVEL' : 'INDISPONÍVEL'}</p>
    <h1>{skill.name}</h1>
    <div className="skills-detail__summary"><p className="skills-detail__description">{skill.description}</p><button type="button" className="button button--secondary" onClick={onEdit}>Editar skill</button></div>
    <dl className="skills-detail__facts">
      <div><dt>Versão</dt><dd><code>{skill.version}</code></dd></div>
      <div><dt>Tags</dt><dd>{skill.tags.length ? skill.tags.map((tag) => <span key={tag} className="skill-tag">{tag}</span>) : 'Sem tags'}</dd></div>
    </dl>
    <div className="skills-detail__disclosures">
      <Disclosure label="Instruções"><pre className="skills-instructions">{skill.instructions}</pre></Disclosure>
      <Disclosure label="Dependências"><MetadataList items={skill.dependencies} empty="Sem dependências." /></Disclosure>
      <Disclosure label="Uso e ferramentas"><MetadataList items={skill.requires_tools} empty="Sem ferramentas obrigatórias." /></Disclosure>
      <Disclosure label={`Versões instaladas · ${skill.versions.length}`}>
        <div className="skill-versions" aria-label="Versões instaladas">
          {skill.versions.map((version) => {
            const current = version === skill.version
            return <div className={`skill-version-row${current ? ' is-current' : ''}`} key={version}>
              <div className="skill-version-row__identity"><span className="skill-version-row__dot" aria-hidden="true" /><code>v{version}</code>{current && <span className="skill-version-row__badge">Atual</span>}</div>
              {current ? <span className="skill-version-row__protected">Em uso</span> : skill.source === 'custom' ? <button type="button" className="skill-version-row__remove" aria-label={`Desinstalar versão ${version}`} onClick={() => onRemoveVersion(version)} disabled={removingVersion !== null}>{removingVersion === version ? 'Desinstalando…' : 'Desinstalar'}</button> : <span className="skill-version-row__protected">Protegida</span>}
            </div>
          })}
        </div>
        {skill.versions.length < 2 && <p className="skills-metadata__empty">Não há versões antigas instaladas.</p>}
        {removeError && <p className="skills-form__error" role="alert">{removeError}</p>}
      </Disclosure>
      {usedBy.length > 0 && <Disclosure label="Usada por"><ul className="skills-metadata">{usedBy.map((agent) => <li key={agent.agent_id}>{agent.agent_id} · {agent.mode}</li>)}</ul></Disclosure>}
    </div>
  </>
}

function MetadataList({ items, empty }: { items: string[]; empty: string }) {
  return items.length ? <ul className="skills-metadata">{items.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="skills-metadata__empty">{empty}</p>
}

function CreateSkillForm({ client, onCancel, onCreated }: { client: SkillsClient; onCancel: () => void; onCreated: (created: Awaited<ReturnType<SkillsClient['create']>>) => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [version, setVersion] = useState('1.0.0')
  const [tags, setTags] = useState('')
  const [instructions, setInstructions] = useState('')
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const input: CreateSkillInput = { name: name.trim(), description: description.trim(), version: version.trim(), tags: tags.split(',').map((item) => item.trim()).filter(Boolean), instructions: instructions.trim() }
    if (saving || !input.name || !input.description || !input.version || !input.instructions) return
    setSaving(true)
    setFailed(false)
    try { onCreated(await client.create(input)) } catch { setFailed(true); setSaving(false) }
  }

  return <form className="skills-form" onSubmit={submit}>
    <div className="skills-form__heading"><div><p className="eyebrow">NOVA SKILL</p><h2>Defina o procedimento</h2></div><button type="button" className="button button--quiet" onClick={onCancel} disabled={saving}>Cancelar</button></div>
    <label>Nome<input required value={name} onChange={(event) => setName(event.target.value)} /></label>
    <label>Descrição<textarea required value={description} onChange={(event) => setDescription(event.target.value)} /></label>
    <label>Instruções<textarea required value={instructions} onChange={(event) => setInstructions(event.target.value)} placeholder="Descreva o procedimento em Markdown" /></label>
    <label>Tags<input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="debugging, testing" /></label>
    <Disclosure label="Campos avançados" defaultOpen={false} className="skills-form__advanced">
      <label>Versão<input required value={version} onChange={(event) => setVersion(event.target.value)} /></label>
    </Disclosure>
    {failed && <p role="alert" className="skills-form__error">Não foi possível salvar a skill. Revise os campos e tente novamente.</p>}
    <div className="skills-form__actions"><button type="submit" className="button button--primary" disabled={saving}>{saving ? 'Salvando…' : 'Salvar skill'}</button></div>
  </form>
}

function EditSkillForm({ client, skill, onCancel, onUpdated }: { client: SkillsClient; skill: SkillDetail; onCancel: () => void; onUpdated: (skill: SkillDetail) => void }) {
  const [name, setName] = useState(skill.name)
  const [description, setDescription] = useState(skill.description)
  const [version, setVersion] = useState(skill.version)
  const [tags, setTags] = useState(skill.tags.join(', '))
  const [instructions, setInstructions] = useState(skill.instructions)
  const [saving, setSaving] = useState(false)
  const [failed, setFailed] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const input: CreateSkillInput = { name: name.trim(), description: description.trim(), version: version.trim(), tags: tags.split(',').map((item) => item.trim()).filter(Boolean), instructions: instructions.trim() }
    if (saving || !input.name || !input.description || !input.version || !input.instructions) return
    setSaving(true)
    setFailed(false)
    try { onUpdated(await client.update(skill.id, input)) } catch { setFailed(true); setSaving(false) }
  }

  return <form className="skills-form" onSubmit={submit}>
    <div className="skills-form__heading"><div><p className="eyebrow">EDITAR SKILL</p><h2>Atualize o procedimento</h2></div><button type="button" className="button button--quiet" onClick={onCancel} disabled={saving}>Cancelar</button></div>
    <label>Nome<input required value={name} onChange={(event) => setName(event.target.value)} /></label>
    <label>Descrição<textarea required value={description} onChange={(event) => setDescription(event.target.value)} /></label>
    <label>Instruções<textarea required value={instructions} onChange={(event) => setInstructions(event.target.value)} /></label>
    <label>Tags<input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="debugging, testing" /></label>
    <Disclosure label="Campos avançados" className="skills-form__advanced">
      <label>Versão<input required value={version} onChange={(event) => setVersion(event.target.value)} /></label>
    </Disclosure>
    {failed && <p role="alert" className="skills-form__error">Não foi possível salvar as alterações. Revise os campos e tente novamente.</p>}
    <div className="skills-form__actions"><button type="submit" className="button button--primary" disabled={saving}>{saving ? 'Salvando…' : 'Salvar alterações'}</button></div>
  </form>
}
